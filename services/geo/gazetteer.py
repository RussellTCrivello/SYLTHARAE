"""The database gazetteer: seed loading, fingerprint and the detector index.

The gazetteer lives in PostgreSQL (``geo_places``, ``geo_place_names``,
created by migration 0019). Its reviewed content is the committed seed
``data/gazetteer/places_seed.json`` (built by ``tools/gazetteer/build_seed.py``
from Wikidata, CC0). :func:`sync_seed` is the seed synchronizer; approved
curation writes use :func:`record_curation_change` so their active detector
fingerprint and immutable revision history stay in step:

* idempotent upsert keyed on ``place_key`` / ``(place, language, match_key)``;
* places that leave the seed are *retired*, never deleted - stored place
  signals keep pointing at them (``content_signal_places`` is ``ON DELETE
  RESTRICT``), so evidence is never orphaned;
* every load that changes anything records a ``geo_gazetteer_loads`` row
  with the seed's SHA-256 and a fingerprint computed **from the database
  rows**. The place detector's version embeds that fingerprint, so editing
  the gazetteer makes stored place signals stale for re-detection.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from core.geo.names import LANGUAGE_SCRIPT, match_key, script_of

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = ROOT / "data" / "gazetteer" / "places_seed.json"
SEED_FORMAT = 1
SEED_SOURCE = "wikidata"


class SeedIntegrityError(ValueError):
    """The seed file is malformed or does not match its recorded SHA-256."""


class GazetteerUnavailable(RuntimeError):
    """No gazetteer has been loaded into this database."""


def load_seed_file(path: Path = DEFAULT_SEED) -> Dict[str, Any]:
    """Read and verify a seed file (format, SHA-256 of the places payload)."""
    try:
        seed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SeedIntegrityError(f"cannot read gazetteer seed {path}: {exc}") from exc
    if seed.get("format") != SEED_FORMAT:
        raise SeedIntegrityError(f"unsupported gazetteer seed format {seed.get('format')!r}")
    payload = json.dumps(seed.get("places"), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if digest != seed.get("content_sha256"):
        raise SeedIntegrityError("gazetteer seed content does not match its content_sha256")
    for place in seed["places"]:
        for name in place["names"]:
            if script_of(match_key(name["name"])) != LANGUAGE_SCRIPT.get(name["language"]):
                raise SeedIntegrityError(
                    f"{place['place_key']}: {name['language']} name {name['name']!r} "
                    "is not in the language's script")
    return seed


_UPSERT_PLACE = (
    "INSERT INTO geo_places (place_key, label, feature_type, country_codes, latitude,"
    " longitude, source, source_title, seed_version) VALUES %s"
    " ON CONFLICT (place_key) DO UPDATE SET label = EXCLUDED.label,"
    " feature_type = EXCLUDED.feature_type, country_codes = EXCLUDED.country_codes,"
    " latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, source = EXCLUDED.source,"
    " source_title = EXCLUDED.source_title, seed_version = EXCLUDED.seed_version,"
    " retired = FALSE, updated_at = NOW()"
    " WHERE (geo_places.label, geo_places.feature_type, geo_places.country_codes,"
    " geo_places.latitude, geo_places.longitude, geo_places.source, geo_places.source_title,"
    " geo_places.retired)"
    " IS DISTINCT FROM (EXCLUDED.label, EXCLUDED.feature_type, EXCLUDED.country_codes,"
    " EXCLUDED.latitude, EXCLUDED.longitude, EXCLUDED.source, EXCLUDED.source_title, FALSE)"
    " RETURNING id, place_key, (xmax = 0) AS inserted"
)

_UPSERT_NAME = (
    "INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
    " homograph, source, note) VALUES %s"
    " ON CONFLICT (place_id, language, match_key) DO UPDATE SET name = EXCLUDED.name,"
    " script = EXCLUDED.script, name_type = EXCLUDED.name_type,"
    " homograph = EXCLUDED.homograph, source = EXCLUDED.source, note = EXCLUDED.note"
    " WHERE (geo_place_names.name, geo_place_names.script, geo_place_names.name_type,"
    " geo_place_names.homograph, geo_place_names.source, geo_place_names.note)"
    " IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.script, EXCLUDED.name_type,"
    " EXCLUDED.homograph, EXCLUDED.source, EXCLUDED.note)"
    " RETURNING (xmax = 0) AS inserted"
)


def compute_fingerprint(cur) -> Tuple[str, int, int]:
    """SHA-256 over the active gazetteer as stored (places and names, sorted).

    Computed from the database, not the seed file, so it describes exactly
    what the detector will match against.
    """
    cur.execute("SELECT place_key, label, feature_type, country_codes, latitude, longitude"
                " FROM geo_places WHERE NOT retired ORDER BY place_key")
    places = [[r[0], r[1], r[2], list(r[3] or []),
               None if r[4] is None else repr(float(r[4])),
               None if r[5] is None else repr(float(r[5]))] for r in cur.fetchall()]
    cur.execute("SELECT p.place_key, n.language, n.match_key, n.name, n.script, n.name_type,"
                " n.homograph, n.note FROM geo_place_names n JOIN geo_places p ON p.id = n.place_id"
                " WHERE NOT p.retired ORDER BY p.place_key, n.language, n.match_key")
    names = [list(r) for r in cur.fetchall()]
    payload = json.dumps({"places": places, "names": names}, ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), len(places), len(names)


def lock_curation(cur) -> None:
    """Serialize authorized curation mutations before they change stored rows.

    The lock is transaction-scoped. It keeps concurrent place/name edits from
    recording out-of-order fingerprints or missing an intermediate revision.
    """
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x47415A45545445,))


def record_curation_change(cur, *, loaded_by: str, action: str) -> Dict[str, Any]:
    """Record a manual curation revision when detector-visible rows changed.

    ``content_sha256`` remains the digest of the reviewed seed payload that
    anchors this database. ``fingerprint`` is computed from the active stored
    gazetteer, so every matching change advances the detector version. The
    load table is append-only; user-facing history endpoints are read-only.
    """
    latest = current_load(cur)
    if latest is None:
        raise GazetteerUnavailable("no gazetteer load exists for curation")
    fingerprint, place_count, name_count = compute_fingerprint(cur)
    changed = fingerprint != latest["fingerprint"]
    if changed:
        stats = {
            "kind": "admin_curation",
            "action": str(action)[:80],
            "places_active": place_count,
            "names_active": name_count,
        }
        cur.execute(
            "INSERT INTO geo_gazetteer_loads (seed_version, content_sha256, fingerprint,"
            " place_count, name_count, stats, loaded_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (latest["seed_version"], latest["content_sha256"], fingerprint,
             place_count, name_count, psycopg2.extras.Json(stats), (loaded_by or "admin")[:80]),
        )
        load_id = cur.fetchone()[0]
    else:
        load_id = latest["id"]
    return {"changed": changed, "load_id": load_id, "fingerprint": fingerprint,
            "place_count": place_count, "name_count": name_count}


def current_load(cur) -> Optional[Dict[str, Any]]:
    """The latest gazetteer load, or None (also when migration 0019 is absent)."""
    cur.execute("SELECT to_regclass('geo_gazetteer_loads') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    cur.execute("SELECT id, seed_version, content_sha256, fingerprint, place_count, name_count,"
                " loaded_by, loaded_at FROM geo_gazetteer_loads ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return None
    keys = ("id", "seed_version", "content_sha256", "fingerprint", "place_count",
            "name_count", "loaded_by", "loaded_at")
    return dict(zip(keys, row))


def sync_seed(cur, seed: Optional[Dict[str, Any]] = None, *, loaded_by: str) -> Dict[str, Any]:
    """Make the database gazetteer equal to ``seed`` (idempotent).

    Runs inside the caller's transaction; a failure leaves nothing changed.
    Returns counts and whether anything changed.
    """
    if seed is None:
        seed = load_seed_file()
    if not loaded_by:
        raise ValueError("loaded_by is required (who applied the seed)")
    lock_curation(cur)
    places = seed["places"]
    stats = {"places_added": 0, "places_updated": 0, "places_retired": 0,
             "names_added": 0, "names_updated": 0, "names_removed": 0}

    rows = [(p["place_key"], p["label"], p["feature_type"], list(p["country_codes"]),
             p["latitude"], p["longitude"], SEED_SOURCE, p.get("source_title"),
             seed["seed_version"]) for p in places]
    for _pid, _key, inserted in psycopg2.extras.execute_values(
            cur, _UPSERT_PLACE, rows, page_size=500, fetch=True):
        stats["places_added" if inserted else "places_updated"] += 1
    cur.execute("SELECT id, place_key FROM geo_places WHERE place_key = ANY(%s)",
                ([p["place_key"] for p in places],))
    ids = dict((key, pid) for pid, key in cur.fetchall())

    cur.execute("UPDATE geo_places SET retired = TRUE, updated_at = NOW()"
                " WHERE source = %s AND NOT retired AND NOT (place_key = ANY(%s))",
                (SEED_SOURCE, list(ids)))
    stats["places_retired"] = cur.rowcount

    desired = []
    for p in places:
        for n in p["names"]:
            desired.append((ids[p["place_key"]], n["name"], match_key(n["name"]), n["language"],
                            n["script"], n["name_type"], bool(n["homograph"]), n["source"],
                            n.get("note")))
    cur.execute("CREATE TEMP TABLE _gz_names (place_id INTEGER, language VARCHAR(8),"
                " match_key VARCHAR(255)) ON COMMIT DROP")
    psycopg2.extras.execute_values(cur, "INSERT INTO _gz_names VALUES %s",
                                   [(d[0], d[3], d[2]) for d in desired], page_size=1000)
    cur.execute("DELETE FROM geo_place_names n WHERE n.place_id = ANY(%s) AND n.source <> 'user' AND NOT EXISTS ("
                " SELECT 1 FROM _gz_names d WHERE d.place_id = n.place_id"
                " AND d.language = n.language AND d.match_key = n.match_key)",
                (list(ids.values()),))
    stats["names_removed"] = cur.rowcount
    cur.execute("DROP TABLE _gz_names")
    for (inserted,) in psycopg2.extras.execute_values(cur, _UPSERT_NAME, desired,
                                                      page_size=1000, fetch=True):
        stats["names_added" if inserted else "names_updated"] += 1

    fingerprint, place_count, name_count = compute_fingerprint(cur)
    latest = current_load(cur)
    changed = (latest is None or latest["fingerprint"] != fingerprint
               or latest["content_sha256"] != seed["content_sha256"])
    if changed:
        cur.execute("INSERT INTO geo_gazetteer_loads (seed_version, content_sha256, fingerprint,"
                    " place_count, name_count, stats, loaded_by)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (seed["seed_version"], seed["content_sha256"], fingerprint, place_count,
                     name_count, psycopg2.extras.Json(stats), loaded_by[:80]))
    logger.info("gazetteer sync by %s: changed=%s %s fingerprint=%s", loaded_by, changed,
                stats, fingerprint[:12])
    return {"changed": changed, "fingerprint": fingerprint, "place_count": place_count,
            "name_count": name_count, "seed_version": seed["seed_version"], **stats}


# ---------------------------------------------------------------------------
# Detector index
# ---------------------------------------------------------------------------

_INDEX_CACHE: Dict[str, Any] = {}


def detector_gazetteer(cur):
    """The ``place_intel.Gazetteer`` for the current load, cached per fingerprint.

    One cheap query per call (the latest load row); names are read only when
    the fingerprint changes. Raises :class:`GazetteerUnavailable` when no
    gazetteer is loaded - a place run then fails loudly instead of recording
    "no places found".
    """
    from core.detection import place_intel

    load = current_load(cur)
    if load is None:
        raise GazetteerUnavailable("no gazetteer is loaded (migration 0019 not applied?)")
    fingerprint = load["fingerprint"]
    cached = _INDEX_CACHE.get("gazetteer")
    if cached is not None and cached.fingerprint == fingerprint:
        return cached
    cur.execute("SELECT p.id, p.place_key, p.label, p.feature_type, p.country_codes, n.name,"
                " n.language, n.script, n.name_type, n.homograph, n.note"
                " FROM geo_place_names n JOIN geo_places p ON p.id = n.place_id"
                " WHERE NOT p.retired ORDER BY p.place_key, n.language, n.match_key")
    gazetteer = place_intel.Gazetteer.from_rows(cur.fetchall(), fingerprint)
    _INDEX_CACHE["gazetteer"] = gazetteer
    return gazetteer


def current_detector_version(cur) -> Optional[str]:
    """``places-X.Y.Z+g<fingerprint>`` for the loaded gazetteer, or None."""
    from core.detection import place_intel

    load = current_load(cur)
    return place_intel.detector_version(load["fingerprint"]) if load else None
