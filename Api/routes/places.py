"""Gazetteer read API (Phase 2). Any authenticated user; read-only.

* ``GET /api/gazetteer`` - the loaded gazetteer: seed version, SHA-256,
  fingerprint, counts, and the place detector version derived from it.
* ``GET /api/places?q=&language=&feature_type=&country=&page=&per_page=`` -
  places whose names start with ``q`` (compared after the detector's own
  normalisation, case-insensitively), paginated in SQL, ordered by label
  then key.
* ``GET /api/places/<place_key>`` - one place with every recorded name and
  its mention counts in content the caller may read (identified vs
  ambiguous kept apart - an ambiguous mention is *not* counted as this
  place).

The gazetteer itself is not writable through the API; it changes only via
the reviewed seed (``tools/gazetteer/build_seed.py`` + ``sync_seed``).
"""

import logging
import uuid

from flask import jsonify, request

from core.criteria.access import scope_for
from core.geo.names import LANGUAGES, match_key
from core.security.flask_ext import current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

MAX_PER_PAGE = 200
FEATURE_TYPES = ("city", "country", "region")


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _int(name, default, lo, hi):
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer")
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return value


def _read(fn):
    from Api.utils.utils import get_connection

    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                return fn(cur)
        finally:
            conn.rollback()


def register_place_routes(app):

    @app.route("/api/gazetteer", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_gazetteer():
        from services.geo.gazetteer import current_detector_version, current_load

        try:
            load, version = _read(lambda cur: (current_load(cur), current_detector_version(cur)))
        except Exception:
            logger.exception("gazetteer status read failed")
            return _error("GAZETTEER_UNAVAILABLE", "Gazetteer status could not be read", 500)
        if load is None:
            return jsonify({"success": True, "loaded": False, "detector_ver": None})
        load["loaded_at"] = load["loaded_at"].isoformat() if load["loaded_at"] else None
        return jsonify({"success": True, "loaded": True, "load": load, "detector_ver": version})

    @app.route("/api/places", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places():
        try:
            page = _int("page", 1, 1, 100_000)
            per_page = _int("per_page", 50, 1, MAX_PER_PAGE)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        q = (request.args.get("q") or "").strip()
        language = request.args.get("language") or None
        feature = request.args.get("feature_type") or None
        country = (request.args.get("country") or "").upper() or None
        if language and language not in LANGUAGES:
            return _error("VALIDATION_FAILED", f"language must be one of {', '.join(LANGUAGES)}", 400)
        if feature and feature not in FEATURE_TYPES:
            return _error("VALIDATION_FAILED",
                          f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400)
        if country and (len(country) != 2 or not country.isalpha()):
            return _error("VALIDATION_FAILED", "country must be an ISO 3166-1 alpha-2 code", 400)
        if len(q) > 100:
            return _error("VALIDATION_FAILED", "q is limited to 100 characters", 400)
        where, params = ["NOT p.retired"], []
        if q:
            like = match_key(q).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            name_filter = "lower(n.match_key) LIKE %s"
            params.append(like + "%")
            if language:
                name_filter += " AND n.language = %s"
                params.append(language)
            where.append("EXISTS (SELECT 1 FROM geo_place_names n WHERE n.place_id = p.id"  # nosec B608 # WHERE built from fixed fragments; validated q/language/feature_type/country are bound parameters
                         f" AND {name_filter})")
        elif language:
            where.append("EXISTS (SELECT 1 FROM geo_place_names n WHERE n.place_id = p.id"
                         " AND n.language = %s)")
            params.append(language)
        if feature:
            where.append("p.feature_type = %s")
            params.append(feature)
        if country:
            where.append("%s = ANY(p.country_codes)")
            params.append(country)
        clause = " AND ".join(where)

        def run(cur):
            cur.execute(f"SELECT count(*) FROM geo_places p WHERE {clause}", params)  # nosec B608 # WHERE built from fixed fragments; validated q/language/feature_type/country are bound parameters
            total = cur.fetchone()[0]
            cur.execute("SELECT p.id, p.place_key, p.label, p.feature_type, p.country_codes,"  # nosec B608 # WHERE built from fixed fragments; validated q/language/feature_type/country are bound parameters
                        " p.latitude, p.longitude FROM geo_places p WHERE " + clause +
                        " ORDER BY p.label, p.place_key LIMIT %s OFFSET %s",
                        params + [per_page, (page - 1) * per_page])
            rows = cur.fetchall()
            names = {}
            if rows:
                cur.execute("SELECT place_id, name, language, name_type FROM geo_place_names"
                            " WHERE place_id = ANY(%s) ORDER BY place_id, language, name",
                            ([r[0] for r in rows],))
                for pid, name, lang, ntype in cur.fetchall():
                    names.setdefault(pid, []).append(
                        {"name": name, "language": lang, "name_type": ntype})
            return total, rows, names

        try:
            total, rows, names = _read(run)
        except Exception:
            logger.exception("places search failed")
            return _error("PLACES_UNAVAILABLE", "Places could not be read", 500)
        items = [{"place_key": r[1], "label": r[2], "feature_type": r[3],
                  "country_codes": list(r[4] or []), "latitude": r[5], "longitude": r[6],
                  "names": names.get(r[0], [])} for r in rows]
        return jsonify({"success": True, "data": items, "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page}})

    @app.route("/api/places/<string:place_key>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_place(place_key):
        scope = scope_for(current_user())

        def run(cur):
            cur.execute("SELECT id, place_key, label, feature_type, country_codes, latitude,"
                        " longitude, source, source_title, seed_version, retired"
                        " FROM geo_places WHERE place_key = %s", (place_key,))
            place = cur.fetchone()
            if place is None:
                return None
            cur.execute("SELECT name, language, script, name_type, homograph, source, note"
                        " FROM geo_place_names WHERE place_id = %s"
                        " ORDER BY language, name_type, name", (place[0],))
            names = cur.fetchall()
            scope_sql, scope_params = "", []
            if scope.allowed_source_ids is not None:
                scope_sql = (" AND EXISTS (SELECT 1 FROM hash_contexts hc WHERE"
                             " hc.hash_id = s.hash_id AND hc.source_id = ANY(%s))")
                scope_params = [list(scope.allowed_source_ids)]
            cur.execute("SELECT s.resolution, count(*), count(DISTINCT s.hash_id)"  # nosec B608 # WHERE built from fixed fragments; validated q/language/feature_type/country are bound parameters
                        " FROM content_signal_places csp"
                        " JOIN content_signals s ON s.id = csp.signal_id"
                        " WHERE csp.place_id = %s" + scope_sql + " GROUP BY s.resolution",
                        [place[0]] + scope_params)
            counts = {r[0]: {"mentions": r[1], "contents": r[2]} for r in cur.fetchall()}
            return place, names, counts

        try:
            found = _read(run)
        except Exception:
            logger.exception("place read failed for %s", place_key)
            return _error("PLACES_UNAVAILABLE", "Place could not be read", 500)
        if found is None:
            return _error("NOT_FOUND", "Place not found", 404)
        place, names, counts = found
        zero = {"mentions": 0, "contents": 0}
        return jsonify({"success": True, "place": {
            "place_key": place[1], "label": place[2], "feature_type": place[3],
            "country_codes": list(place[4] or []), "latitude": place[5], "longitude": place[6],
            "source": place[7], "source_title": place[8], "seed_version": place[9],
            "retired": place[10],
            "names": [{"name": n[0], "language": n[1], "script": n[2], "name_type": n[3],
                       "homograph": n[4], "source": n[5], "note": n[6]} for n in names],
            "mentions": {"identified": counts.get("identified", zero),
                         "ambiguous_candidate": counts.get("ambiguous", zero),
                         "scope": "all_sources" if scope.allowed_source_ids is None
                         else "restricted"}}})
