"""Gazetteer read and management API (Phase 2).

* ``GET /api/gazetteer`` - the loaded gazetteer: seed version, SHA-256,
  fingerprint, counts, and the place detector version derived from it.
* ``GET /api/places?q=&language=&feature_type=&country=&status=&source=&page=&per_page=`` -
  places matching criteria, paginated in SQL, ordered by label then key.
* ``GET /api/places/<place_key>`` - one place with every recorded name and
  its mention counts in content the caller may read.
* ``POST /api/places`` - create a user-managed geographic location (admin only).
* ``PUT /api/places/<place_key>`` - update an existing place or toggle active/retired (admin only).
* ``DELETE /api/places/<place_key>`` - delete a user-managed place or retire a seed-managed place (admin only).
"""

import logging
import uuid

from flask import jsonify, request

from core.criteria.access import scope_for
from core.geo.names import LANGUAGE_SCRIPT, LANGUAGES, match_key, script_of
from core.security import ROLE_ADMIN
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


def _write(fn):
    from Api.utils.utils import get_connection

    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                result = fn(cur)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def _audit(action, target_id, detail=None):
    user = current_user()
    uid = getattr(user, "id", None)
    try:
        from core.security.service import get_auth_service
        get_auth_service().audit(
            action,
            user_id=uid,
            username=getattr(user, "username", None),
            target_type="place",
            target_id=str(target_id),
            detail=detail or {},
        )
    except Exception:
        logger.warning("place audit log failed: %s %s", action, target_id, exc_info=True)


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
        return jsonify({"success": True, "loaded": True, "load": load,
                        "detector_ver": version})

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
        status = request.args.get("status") or None
        source = request.args.get("source") or None
        include_retired = request.args.get("include_retired") in ("1", "true", "yes")

        if language and language not in LANGUAGES:
            return _error("VALIDATION_FAILED", f"language must be one of {', '.join(LANGUAGES)}", 400)
        if feature and feature not in FEATURE_TYPES:
            return _error("VALIDATION_FAILED",
                          f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400)
        if country and (len(country) != 2 or not country.isalpha()):
            return _error("VALIDATION_FAILED", "country must be an ISO 3166-1 alpha-2 code", 400)
        if len(q) > 100:
            return _error("VALIDATION_FAILED", "q is limited to 100 characters", 400)

        where, params = [], []
        if status == "retired":
            where.append("p.retired")
        elif status == "all" or include_retired:
            where.append("1=1")
        else:
            where.append("NOT p.retired")

        if source and source != "all":
            where.append("p.source = %s")
            params.append(source)

        if q:
            like = match_key(q).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            name_filter = "lower(n.match_key) LIKE %s"
            name_params = [like + "%"]
            if language:
                name_filter += " AND n.language = %s"
                name_params.append(language)
            where.append(
                "(p.label ILIKE %s OR p.place_key ILIKE %s OR EXISTS (SELECT 1 FROM geo_place_names n"
                f" WHERE n.place_id = p.id AND {name_filter}))"
            )
            params.extend([f"%{q}%", f"%{q}%"] + name_params)
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
            cur.execute(f"SELECT count(*) FROM geo_places p WHERE {clause}", params)  # nosec B608
            total = cur.fetchone()[0]
            cur.execute("SELECT p.id, p.place_key, p.label, p.feature_type, p.country_codes,"  # nosec B608
                        " p.latitude, p.longitude, p.source, p.retired FROM geo_places p WHERE " + clause +
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
                  "source": r[7], "retired": bool(r[8]),
                  "names": names.get(r[0], [])} for r in rows]
        return jsonify({"success": True, "data": items, "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page}})

    @app.route("/api/places", methods=["POST"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places_create():
        user = current_user()
        if getattr(user, "role", None) != ROLE_ADMIN:
            return _error("FORBIDDEN", "Admin privilege required", 403)

        data = request.get_json(silent=True) or {}
        label = (data.get("label") or "").strip()
        if not label or len(label) > 200:
            return _error("VALIDATION_FAILED", "label is required (1-200 characters)", 400)

        feature_type = (data.get("feature_type") or "").strip().lower()
        if feature_type not in FEATURE_TYPES:
            return _error("VALIDATION_FAILED",
                          f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400)

        raw_countries = data.get("country_codes") or []
        country_codes = []
        for c in raw_countries:
            c = str(c).strip().upper()
            if len(c) != 2 or not c.isalpha():
                return _error("VALIDATION_FAILED", f"country code '{c}' must be an ISO 3166-1 alpha-2 code", 400)
            country_codes.append(c)

        latitude = data.get("latitude")
        if latitude is not None:
            try:
                latitude = float(latitude)
                if not -90.0 <= latitude <= 90.0:
                    raise ValueError
            except (ValueError, TypeError):
                return _error("VALIDATION_FAILED", "latitude must be a float between -90 and 90", 400)

        longitude = data.get("longitude")
        if longitude is not None:
            try:
                longitude = float(longitude)
                if not -180.0 <= longitude <= 180.0:
                    raise ValueError
            except (ValueError, TypeError):
                return _error("VALIDATION_FAILED", "longitude must be a float between -180 and 180", 400)

        place_key = (data.get("place_key") or "").strip()
        if not place_key:
            slug = "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")[:40]
            place_key = f"user:{slug}_{uuid.uuid4().hex[:6]}"
        elif len(place_key) > 100:
            return _error("VALIDATION_FAILED", "place_key cannot exceed 100 characters", 400)

        raw_names = data.get("names")
        if raw_names is None or not isinstance(raw_names, list) or len(raw_names) == 0:
            raw_names = [{"name": label, "language": "en", "name_type": "endonym"}]

        cleaned_names = []
        for item in raw_names:
            if not isinstance(item, dict):
                continue
            n_val = (item.get("name") or "").strip()
            if not n_val or len(n_val) > 200:
                return _error("VALIDATION_FAILED", "each name must be 1-200 characters", 400)
            lang = (item.get("language") or "en").strip().lower()
            if lang not in LANGUAGES:
                return _error("VALIDATION_FAILED", f"name language must be one of {', '.join(LANGUAGES)}", 400)
            ntype = (item.get("name_type") or "variant").strip().lower()
            if ntype not in ("endonym", "exonym", "variant"):
                ntype = "variant"
            cleaned_names.append({"name": n_val, "language": lang, "name_type": ntype})

        def do_create(cur):
            cur.execute("SELECT 1 FROM geo_places WHERE place_key = %s", (place_key,))
            if cur.fetchone():
                return None, "conflict"
            cur.execute(
                "INSERT INTO geo_places (place_key, label, feature_type, country_codes, latitude, longitude,"
                " source, source_title, seed_version, retired, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'user', 'User Created', NULL, FALSE, NOW(), NOW())"
                " RETURNING id",
                (place_key, label, feature_type, country_codes, latitude, longitude)
            )
            place_id = cur.fetchone()[0]
            for n in cleaned_names:
                mkey = match_key(n["name"])
                sc = script_of(n["name"]) or LANGUAGE_SCRIPT.get(n["language"], "Latn")
                cur.execute(
                    "INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
                    " homograph, source, note) VALUES (%s, %s, %s, %s, %s, %s, FALSE, 'user', 'user-defined')",
                    (place_id, n["name"], mkey, n["language"], sc, n["name_type"])
                )
            return place_id, None

        try:
            place_id, err = _write(do_create)
        except Exception:
            logger.exception("create place failed")
            return _error("PLACES_UNAVAILABLE", "Failed to create place", 500)

        if err == "conflict":
            return _error("CONFLICT", f"Place with key '{place_key}' already exists", 409)

        _audit("places.create", place_key, {
            "label": label, "feature_type": feature_type, "country_codes": country_codes,
            "names_count": len(cleaned_names)
        })

        return jsonify({
            "success": True,
            "place": {
                "place_key": place_key,
                "label": label,
                "feature_type": feature_type,
                "country_codes": country_codes,
                "latitude": latitude,
                "longitude": longitude,
                "source": "user",
                "retired": False,
                "names": cleaned_names,
            }
        }), 201

    @app.route("/api/places/<string:place_key>", methods=["PUT"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places_update(place_key):
        user = current_user()
        if getattr(user, "role", None) != ROLE_ADMIN:
            return _error("FORBIDDEN", "Admin privilege required", 403)

        data = request.get_json(silent=True) or {}

        def do_update(cur):
            cur.execute(
                "SELECT id, place_key, label, feature_type, country_codes, latitude, longitude,"
                " source, retired FROM geo_places WHERE place_key = %s",
                (place_key,)
            )
            row = cur.fetchone()
            if row is None:
                return None, 404

            pid, pkey, cur_label, cur_ftype, cur_countries, cur_lat, cur_lon, psrc, cur_retired = row

            new_label = cur_label
            if "label" in data:
                lbl = (data["label"] or "").strip()
                if not lbl or len(lbl) > 200:
                    return "label must be 1-200 characters", 400
                new_label = lbl

            new_ftype = cur_ftype
            if "feature_type" in data:
                ft = (data["feature_type"] or "").strip().lower()
                if ft not in FEATURE_TYPES:
                    return f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400
                new_ftype = ft

            new_countries = cur_countries
            if "country_codes" in data:
                cc_list = []
                for c in data["country_codes"]:
                    c = str(c).strip().upper()
                    if len(c) != 2 or not c.isalpha():
                        return f"country code '{c}' must be an ISO 3166-1 alpha-2 code", 400
                    cc_list.append(c)
                new_countries = cc_list

            new_lat = cur_lat
            if "latitude" in data:
                lat_val = data["latitude"]
                if lat_val is not None:
                    try:
                        lat_val = float(lat_val)
                        if not -90.0 <= lat_val <= 90.0:
                            raise ValueError
                    except (ValueError, TypeError):
                        return "latitude must be a float between -90 and 90", 400
                new_lat = lat_val

            new_lon = cur_lon
            if "longitude" in data:
                lon_val = data["longitude"]
                if lon_val is not None:
                    try:
                        lon_val = float(lon_val)
                        if not -180.0 <= lon_val <= 180.0:
                            raise ValueError
                    except (ValueError, TypeError):
                        return "longitude must be a float between -180 and 180", 400
                new_lon = lon_val

            new_retired = bool(data["retired"]) if "retired" in data else cur_retired

            cur.execute(
                "UPDATE geo_places SET label = %s, feature_type = %s, country_codes = %s,"
                " latitude = %s, longitude = %s, retired = %s, updated_at = NOW()"
                " WHERE id = %s",
                (new_label, new_ftype, new_countries, new_lat, new_lon, new_retired, pid)
            )

            if "names" in data and isinstance(data["names"], list):
                if psrc == "user":
                    cur.execute("DELETE FROM geo_place_names WHERE place_id = %s", (pid,))
                for item in data["names"]:
                    if not isinstance(item, dict):
                        continue
                    n_val = (item.get("name") or "").strip()
                    if not n_val or len(n_val) > 200:
                        continue
                    lang = (item.get("language") or "en").strip().lower()
                    if lang not in LANGUAGES:
                        continue
                    ntype = (item.get("name_type") or "variant").strip().lower()
                    if ntype not in ("endonym", "exonym", "variant"):
                        ntype = "variant"
                    mkey = match_key(n_val)
                    sc = script_of(n_val) or LANGUAGE_SCRIPT.get(lang, "Latn")
                    cur.execute(
                        "INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
                        " homograph, source, note) VALUES (%s, %s, %s, %s, %s, %s, FALSE, 'user', 'user-defined')",
                        (pid, n_val, mkey, lang, sc, ntype)
                    )

            return {
                "place_key": pkey,
                "label": new_label,
                "feature_type": new_ftype,
                "country_codes": list(new_countries or []),
                "latitude": new_lat,
                "longitude": new_lon,
                "source": psrc,
                "retired": new_retired,
            }, 200

        try:
            res, code = _write(do_update)
        except Exception:
            logger.exception("update place failed for %s", place_key)
            return _error("PLACES_UNAVAILABLE", "Failed to update place", 500)

        if code == 404:
            return _error("NOT_FOUND", "Place not found", 404)
        if code == 400:
            return _error("VALIDATION_FAILED", res, 400)

        _audit("places.update", place_key, res)
        return jsonify({"success": True, "place": res}), 200

    @app.route("/api/places/<string:place_key>", methods=["DELETE"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places_delete(place_key):
        user = current_user()
        if getattr(user, "role", None) != ROLE_ADMIN:
            return _error("FORBIDDEN", "Admin privilege required", 403)

        def do_delete(cur):
            cur.execute("SELECT id, source FROM geo_places WHERE place_key = %s", (place_key,))
            row = cur.fetchone()
            if row is None:
                return None, 404
            pid, psrc = row
            cur.execute("SELECT count(*) FROM content_signal_places WHERE place_id = %s", (pid,))
            has_signals = cur.fetchone()[0] > 0
            if psrc == "user" and not has_signals:
                cur.execute("DELETE FROM geo_place_names WHERE place_id = %s", (pid,))
                cur.execute("DELETE FROM geo_places WHERE id = %s", (pid,))
                return "deleted", 200
            else:
                cur.execute("UPDATE geo_places SET retired = TRUE, updated_at = NOW() WHERE id = %s", (pid,))
                return "retired", 200

        try:
            action, code = _write(do_delete)
        except Exception:
            logger.exception("delete place failed for %s", place_key)
            return _error("PLACES_UNAVAILABLE", "Failed to delete place", 500)

        if code == 404:
            return _error("NOT_FOUND", "Place not found", 404)

        _audit("places.delete", place_key, {"action": action})
        return jsonify({"success": True, "place_key": place_key, "action": action}), 200

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
            cur.execute("SELECT s.resolution, count(*), count(DISTINCT s.hash_id)"  # nosec B608
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
