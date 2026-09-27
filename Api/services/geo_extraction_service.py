"""Content-based geolocation extraction (File Analysis: Geolocation).

Real extraction, not fabrication: every location this module ever records
came from an exact, word-bounded match of a known place name inside a
file's own extracted text (``contents_raw``). A file that never mentions a
place gets no coordinates and does not appear in Geolocation - there is no
fallback to "where the source/side is based".

Matching is per canonical content (``hash_id``), so every path sharing that
hash inherits the same, real mentions - it is not recomputed per physical
copy.
"""

import logging
import re
from typing import Dict, List, Tuple

from Api.utils import execute_query
from Api.services.geo_gazetteer import GAZETTEER_SORTED

logger = logging.getLogger(__name__)

# One compiled, word-bounded, case-sensitive pattern per gazetteer entry,
# longest name first so "Hong Kong" is claimed before any shorter accidental
# overlap could be. Case-sensitive on purpose: place names in the gazetteer
# are properly capitalized, and matching that exact capitalization in real
# prose is a meaningfully stronger signal than a lowercase incidental word.
_PATTERNS: List[Tuple[str, str, float, float, "re.Pattern"]] = [
    (name, country, lat, lon, re.compile(r"\b" + re.escape(name) + r"\b"))
    for (name, country, lat, lon) in GAZETTEER_SORTED
]


def _find_mentions(text: str) -> Dict[str, Tuple[str, float, float, int]]:
    """Real gazetteer matches in ``text``: ``{place_name: (country, lat, lon, count)}``."""
    if not text:
        return {}
    found: Dict[str, Tuple[str, float, float, int]] = {}
    for name, country, lat, lon, pattern in _PATTERNS:
        matches = pattern.findall(text)
        if matches:
            found[name] = (country, lat, lon, len(matches))
    return found


def _load_hash_text(hash_id: int) -> str:
    rows = execute_query(
        "SELECT content FROM contents_raw WHERE hash_id = %s ORDER BY chunk_seq",
        (hash_id,),
        fetch="all",
    )
    if not rows:
        return ""
    return "".join(r[0] or "" for r in rows)


def scan_and_tag_geolocations(force: bool = False) -> dict:
    """Scan every canonical content's real extracted text for gazetteer places.

    Args:
        force: when False (default), only hashes with no existing mentions
            are scanned (cheap incremental re-run after new ingests). When
            True, every hash is re-scanned and its mentions replaced - use
            after editing the gazetteer.

    Returns a summary dict: hashes scanned, hashes matched, distinct places
    found, and files (paths) whose ``coordinates`` were set.
    """
    if force:
        hash_ids = execute_query(
            "SELECT DISTINCT hash_id FROM contents_raw ORDER BY hash_id",
            fetch="all",
        )
    else:
        hash_ids = execute_query(
            """
            SELECT DISTINCT cr.hash_id
            FROM contents_raw cr
            WHERE NOT EXISTS (
                SELECT 1 FROM path_geo_mentions pgm WHERE pgm.hash_id = cr.hash_id
            )
            ORDER BY cr.hash_id
            """,
            fetch="all",
        )
    hash_ids = [r[0] for r in (hash_ids or [])]

    scanned = 0
    matched_hashes = 0
    distinct_places = set()
    files_tagged = 0

    for hash_id in hash_ids:
        scanned += 1
        text = _load_hash_text(hash_id)
        mentions = _find_mentions(text)
        if not mentions:
            continue

        matched_hashes += 1
        if force:
            execute_query(
                "DELETE FROM path_geo_mentions WHERE hash_id = %s",
                (hash_id,),
                fetch=None,
            )

        best_name, best_count = None, -1
        for place_name, (country, lat, lon, count) in mentions.items():
            distinct_places.add(place_name)
            execute_query(
                """
                INSERT INTO path_geo_mentions (hash_id, place_name, country, latitude, longitude, mention_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (hash_id, place_name)
                DO UPDATE SET mention_count = EXCLUDED.mention_count, country = EXCLUDED.country,
                              latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude
                """,
                (hash_id, place_name, country, lat, lon, count),
                fetch=None,
            )
            if count > best_count:
                best_name, best_count = place_name, count

        if best_name:
            _, (_, lat, lon, _) = best_name, mentions[best_name]
            updated = execute_query(
                """
                UPDATE paths p
                SET coordinates = %s
                FROM hash_contexts hc
                WHERE p.context_id = hc.id AND hc.hash_id = %s
                RETURNING p.id
                """,
                (f"{lat},{lon}", hash_id),
                fetch="all",
            )
            files_tagged += len(updated or [])

    logger.info(
        "Geolocation scan: %s hashes scanned, %s matched, %s distinct places, %s files tagged (force=%s)",
        scanned, matched_hashes, len(distinct_places), files_tagged, force,
    )
    return {
        "hashes_scanned": scanned,
        "hashes_matched": matched_hashes,
        "distinct_places": len(distinct_places),
        "files_tagged": files_tagged,
    }
