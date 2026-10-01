"""Gazetteer read and management API (Phase 2).

* ``GET /api/gazetteer`` - the loaded gazetteer: seed version, SHA-256,
  fingerprint, counts, and the place detector version derived from it.
* ``GET /api/places`` - searchable, sortable, SQL-paginated places; place details
  include every recorded name and mention counts scoped to the caller.
* ``/api/place-names`` - browse multilingual names and admin CRUD for user-curated
  aliases; seed-provided names are read-only.
* ``/api/gazetteer/loads`` - browse and inspect immutable seed/curation revisions.
* Place mutations are admin-only and append a detector fingerprint revision when
  matching/evidence fields change. Seed-managed place facts remain source-controlled;
  they can be retired, while unreferenced user places may be deleted.
"""

import logging
import re
import uuid

from flask import jsonify, request

from core.criteria.access import scope_for
from core.geo.names import LANGUAGE_SCRIPT, LANGUAGES, match_key, script_of
from core.security import ROLE_ADMIN
from core.security.flask_ext import current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

MAX_PER_PAGE = 200
LOAD_SORT = {"id": "id", "seed_version": "seed_version", "loaded_at": "loaded_at",
             "place_count": "place_count", "name_count": "name_count", "loaded_by": "loaded_by"}
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


def _audit(action, target_id, detail=None, target_type="place"):
    user = current_user()
    uid = getattr(user, "id", None)
    try:
        from core.security.service import get_auth_service
        audit_detail = dict(detail or {})
        audit_detail.setdefault("target_type", target_type)
        audit_detail.setdefault("target_id", str(target_id))
        get_auth_service().audit(
            action,
            user_id=uid,
            username=getattr(user, "username", None),
            resource=f"{target_type}:{target_id}",
            detail=audit_detail,
        )
    except Exception:
        logger.warning("place audit log failed: %s %s", action, target_id, exc_info=True)



def _admin_required():
    return getattr(current_user(), "role", None) == ROLE_ADMIN


def _actor_name():
    user = current_user()
    return "admin:" + str(getattr(user, "username", None) or getattr(user, "id", "unknown"))


def _clean_name(data):
    """Validate and normalize a multilingual alias using the detector rules."""
    if not isinstance(data, dict):
        return None, "name entry must be an object"
    name = data.get("name")
    if not isinstance(name, str):
        return None, "name is required (1-200 characters)"
    name = name.strip()
    if not name or len(name) > 200:
        return None, "name is required (1-200 characters)"
    language = str(data.get("language") or "en").strip().lower()
    if language not in LANGUAGES:
        return None, f"language must be one of {', '.join(LANGUAGES)}"
    name_type = str(data.get("name_type") or "variant").strip().lower()
    if name_type not in ("endonym", "exonym", "historical", "variant", "unclassified"):
        return None, "name_type must be endonym, exonym, historical, variant, or unclassified"
    actual_script = script_of(name)
    expected_script = LANGUAGE_SCRIPT[language]
    if actual_script is None and any(char.isalpha() for char in name):
        return None, "name must use one supported script"
    if actual_script and actual_script != expected_script:
        return None, f"name text does not use the {expected_script} script required for {language}"
    key = match_key(name)
    if not any(char.isalnum() for char in key):
        return None, "name must contain searchable letters or digits"
    if len(key) > 255:
        return None, "normalized name exceeds the 255-character database limit"
    homograph = data.get("homograph", False)
    if not isinstance(homograph, bool):
        return None, "homograph must be true or false"
    note = data.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note) > 2000:
            return None, "note must be at most 2000 characters"
        note = note.strip() or None
    return {"name": name, "match_key": key, "language": language,
            "script": expected_script, "name_type": name_type,
            "homograph": homograph, "note": note}, None


def _record_curation(cur, action):
    from services.geo.gazetteer import record_curation_change
    return record_curation_change(cur, loaded_by=_actor_name(), action=action)


def _unique_conflict(exc):
    return getattr(exc, "pgcode", None) == "23505"


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
        if status not in (None, "active", "retired", "all"):
            return _error("VALIDATION_FAILED", "status must be active, retired, or all", 400)
        sort = request.args.get("sort", "label")
        order = request.args.get("order", "asc").lower()
        place_sort = {"label": "lower(p.label)", "place_key": "p.place_key",
                      "feature_type": "p.feature_type", "source": "p.source",
                      "retired": "p.retired"}
        if sort not in place_sort or order not in ("asc", "desc"):
            return _error("VALIDATION_FAILED", "sort/order is not supported", 400)

        if language and language not in LANGUAGES:
            return _error("VALIDATION_FAILED", f"language must be one of {', '.join(LANGUAGES)}", 400)
        if feature and feature not in FEATURE_TYPES:
            return _error("VALIDATION_FAILED",
                          f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400)
        if country and (len(country) != 2 or not country.isalpha() or not country.isascii()):
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
            if language:
                where.append(
                    "(p.label ILIKE %s OR p.place_key ILIKE %s OR EXISTS "
                    "(SELECT 1 FROM geo_place_names n WHERE n.place_id = p.id "
                    "AND lower(n.match_key) LIKE %s AND n.language = %s))"
                )
                params.extend([f"%{q}%", f"%{q}%", like + "%", language])
            else:
                where.append(
                    "(p.label ILIKE %s OR p.place_key ILIKE %s OR EXISTS "
                    "(SELECT 1 FROM geo_place_names n WHERE n.place_id = p.id "
                    "AND lower(n.match_key) LIKE %s))"
                )
                params.extend([f"%{q}%", f"%{q}%", like + "%"])
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
                        f" ORDER BY {place_sort[sort]} {order.upper()}, p.place_key ASC LIMIT %s OFFSET %s",
                        params + [per_page, (page - 1) * per_page])
            rows = cur.fetchall()
            names, name_counts = {}, {}
            if rows:
                cur.execute(
                    "SELECT place_id, name, language, name_type, name_total FROM ("
                    " SELECT place_id, name, language, name_type,"
                    " count(*) OVER (PARTITION BY place_id) AS name_total,"
                    " row_number() OVER (PARTITION BY place_id ORDER BY language, name, id) AS name_rank"
                    " FROM geo_place_names WHERE place_id = ANY(%s)) preview"
                    " WHERE name_rank <= 5 ORDER BY place_id, language, name, name_rank",
                    ([r[0] for r in rows],))
                for pid, name, lang, ntype, name_total in cur.fetchall():
                    names.setdefault(pid, []).append(
                        {"name": name, "language": lang, "name_type": ntype})
                    name_counts[pid] = name_total
            return total, rows, names, name_counts

        try:
            total, rows, names, name_counts = _read(run)
        except Exception:
            logger.exception("places search failed")
            return _error("PLACES_UNAVAILABLE", "Places could not be read", 500)
        items = [{"place_key": r[1], "label": r[2], "feature_type": r[3],
                  "country_codes": list(r[4] or []), "latitude": r[5], "longitude": r[6],
                  "source": r[7], "retired": bool(r[8]),
                  "names": names.get(r[0], []), "names_total": name_counts.get(r[0], 0),
                  "names_preview_truncated": name_counts.get(r[0], 0) > len(names.get(r[0], []))}
                 for r in rows]
        return jsonify({"success": True, "data": items, "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page},
            "sort": sort, "order": order})

    @app.route("/api/places", methods=["POST"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places_create():
        if not _admin_required():
            return _error("FORBIDDEN", "Admin privilege required", 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "A JSON object is required", 400)

        label = data.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 200:
            return _error("VALIDATION_FAILED", "label is required (1-200 characters)", 400)
        label = label.strip()
        feature_type = str(data.get("feature_type") or "").strip().lower()
        if feature_type not in FEATURE_TYPES:
            return _error("VALIDATION_FAILED",
                          f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400)

        raw_countries = data.get("country_codes", [])
        if not isinstance(raw_countries, list):
            return _error("VALIDATION_FAILED", "country_codes must be a list", 400)
        country_codes = []
        for raw_code in raw_countries:
            code = str(raw_code).strip().upper()
            if len(code) != 2 or not code.isalpha() or not code.isascii():
                return _error("VALIDATION_FAILED", "country codes must be ISO alpha-2", 400)
            if code not in country_codes:
                country_codes.append(code)

        coordinates = []
        for field, lo, hi in (("latitude", -90.0, 90.0), ("longitude", -180.0, 180.0)):
            value = data.get(field)
            if value is not None:
                try:
                    value = float(value)
                    if not lo <= value <= hi:
                        raise ValueError
                except (TypeError, ValueError):
                    return _error("VALIDATION_FAILED", f"{field} must be between {lo:g} and {hi:g}", 400)
            coordinates.append(value)
        latitude, longitude = coordinates
        if (latitude is None) != (longitude is None):
            return _error("VALIDATION_FAILED", "latitude and longitude must be supplied together", 400)

        place_key = data.get("place_key")
        if place_key is None or not str(place_key).strip():
            slug = "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")[:40]
            place_key = f"user:{slug or 'place'}_{uuid.uuid4().hex[:6]}"
        else:
            place_key = str(place_key).strip()
        if len(place_key) > 100 or not re.fullmatch(r"user:[A-Za-z0-9_.-]+", place_key):
            return _error("VALIDATION_FAILED", "place_key must use the user:<identifier> format", 400)

        raw_names = data.get("names")
        if raw_names is None:
            raw_names = [{"name": label, "language": "en", "name_type": "endonym"}]
        if not isinstance(raw_names, list) or not raw_names:
            return _error("VALIDATION_FAILED", "names must contain at least one valid name", 400)
        cleaned_names = []
        seen_names = set()
        for item in raw_names:
            name, error = _clean_name(item)
            if error:
                return _error("VALIDATION_FAILED", error, 400)
            key = (name["language"], name["match_key"])
            if key in seen_names:
                return _error("VALIDATION_FAILED", "names contain a duplicate normalized alias", 400)
            seen_names.add(key)
            cleaned_names.append(name)

        def do_create(cur):
            from services.geo.gazetteer import lock_curation
            lock_curation(cur)
            cur.execute("SELECT 1 FROM geo_places WHERE place_key = %s", (place_key,))
            if cur.fetchone():
                return None, "conflict", None
            cur.execute(
                "INSERT INTO geo_places (place_key, label, feature_type, country_codes, latitude, longitude,"
                " source, source_title, seed_version, retired, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'user', 'User Created', NULL, FALSE, NOW(), NOW())"
                " RETURNING id",
                (place_key, label, feature_type, country_codes, latitude, longitude))
            place_id = cur.fetchone()[0]
            for name in cleaned_names:
                cur.execute(
                    "INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
                    " homograph, source, note) VALUES (%s, %s, %s, %s, %s, %s, %s, 'user', %s)",
                    (place_id, name["name"], name["match_key"], name["language"],
                     name["script"], name["name_type"], name["homograph"], name["note"]))
            revision = _record_curation(cur, "place.create")
            return place_id, None, revision

        try:
            place_id, error, revision = _write(do_create)
        except Exception as exc:
            logger.exception("create place failed")
            if _unique_conflict(exc):
                return _error("CONFLICT", "Place or alias already exists", 409)
            if type(exc).__name__ == "GazetteerUnavailable":
                return _error("GAZETTEER_UNAVAILABLE", "Gazetteer is not ready for curation", 503)
            return _error("PLACES_UNAVAILABLE", "Failed to create place", 500)
        if error == "conflict":
            return _error("CONFLICT", f"Place with key '{place_key}' already exists", 409)

        _audit("places.create", place_key, {"label": label, "feature_type": feature_type,
                                             "country_codes": country_codes,
                                             "names_count": len(cleaned_names),
                                             "gazetteer_load_id": revision["load_id"]})
        return jsonify({"success": True, "place": {
            "place_key": place_key, "label": label, "feature_type": feature_type,
            "country_codes": country_codes, "latitude": latitude, "longitude": longitude,
            "source": "user", "retired": False, "names": cleaned_names,
        }, "gazetteer_revision": revision}), 201

    @app.route("/api/places/<string:place_key>", methods=["PUT"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places_update(place_key):
        if not _admin_required():
            return _error("FORBIDDEN", "Admin privilege required", 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "A JSON object is required", 400)
        if "names" in data:
            return _error("VALIDATION_FAILED", "Manage aliases in the Place Names tab", 400)
        if "retired" in data and not isinstance(data["retired"], bool):
            return _error("VALIDATION_FAILED", "retired must be true or false", 400)

        def do_update(cur):
            from services.geo.gazetteer import lock_curation
            lock_curation(cur)
            cur.execute(
                "SELECT id, place_key, label, feature_type, country_codes, latitude, longitude,"
                " source, retired FROM geo_places WHERE place_key = %s FOR UPDATE", (place_key,))
            row = cur.fetchone()
            if row is None:
                return None, 404, None
            pid, pkey, label, feature, countries, lat, lon, source, retired = row
            mutable = {"label", "feature_type", "country_codes", "latitude", "longitude"}
            if source != "user" and mutable.intersection(data):
                return "Seed-managed place data is read-only; add a user alias or change retirement status", 403, None

            new_label, new_feature, new_countries = label, feature, list(countries or [])
            new_lat, new_lon, new_retired = lat, lon, retired
            if "label" in data:
                value = data["label"]
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
                    return "label must be 1-200 characters", 400, None
                new_label = value.strip()
            if "feature_type" in data:
                value = str(data["feature_type"] or "").strip().lower()
                if value not in FEATURE_TYPES:
                    return f"feature_type must be one of {', '.join(FEATURE_TYPES)}", 400, None
                new_feature = value
            if "country_codes" in data:
                if not isinstance(data["country_codes"], list):
                    return "country_codes must be a list", 400, None
                new_countries = []
                for raw in data["country_codes"]:
                    code = str(raw).strip().upper()
                    if len(code) != 2 or not code.isascii() or not code.isalpha():
                        return "country codes must be ISO alpha-2", 400, None
                    if code not in new_countries:
                        new_countries.append(code)
            for field, bound, current in (("latitude", 90.0, lat), ("longitude", 180.0, lon)):
                if field in data:
                    value = data[field]
                    if value is not None:
                        try:
                            value = float(value)
                            if not -bound <= value <= bound:
                                raise ValueError
                        except (TypeError, ValueError):
                            return f"{field} must be between {-bound:g} and {bound:g}", 400, None
                    if field == "latitude":
                        new_lat = value
                    else:
                        new_lon = value
            if (new_lat is None) != (new_lon is None):
                return "latitude and longitude must be supplied together", 400, None
            if "retired" in data:
                new_retired = data["retired"]

            cur.execute(
                "UPDATE geo_places SET label = %s, feature_type = %s, country_codes = %s,"
                " latitude = %s, longitude = %s, retired = %s, updated_at = NOW() WHERE id = %s",
                (new_label, new_feature, new_countries, new_lat, new_lon, new_retired, pid))
            revision = _record_curation(cur, "place.update")
            return {"place_key": pkey, "label": new_label, "feature_type": new_feature,
                    "country_codes": new_countries, "latitude": new_lat, "longitude": new_lon,
                    "source": source, "retired": new_retired,
                    "previous": {"label": label, "feature_type": feature,
                                 "country_codes": list(countries or []), "latitude": lat,
                                 "longitude": lon, "retired": retired},
                    "gazetteer_load_id": revision["load_id"]}, 200, revision

        try:
            result, code, revision = _write(do_update)
        except Exception as exc:
            logger.exception("update place failed for %s", place_key)
            if _unique_conflict(exc):
                return _error("CONFLICT", "Place alias already exists", 409)
            if type(exc).__name__ == "GazetteerUnavailable":
                return _error("GAZETTEER_UNAVAILABLE", "Gazetteer is not ready for curation", 503)
            return _error("PLACES_UNAVAILABLE", "Failed to update place", 500)
        if code == 404:
            return _error("NOT_FOUND", "Place not found", 404)
        if code in (400, 403):
            return _error("FORBIDDEN" if code == 403 else "VALIDATION_FAILED", result, code)
        _audit("places.update", place_key, result)
        return jsonify({"success": True, "place": result, "gazetteer_revision": revision}), 200

    @app.route("/api/places/<string:place_key>", methods=["DELETE"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_places_delete(place_key):
        if not _admin_required():
            return _error("FORBIDDEN", "Admin privilege required", 403)

        def do_delete(cur):
            from services.geo.gazetteer import lock_curation
            lock_curation(cur)
            cur.execute("SELECT id, source, retired FROM geo_places WHERE place_key = %s FOR UPDATE",
                        (place_key,))
            row = cur.fetchone()
            if row is None:
                return None, 404, None
            pid, source, retired = row
            cur.execute("SELECT count(*) FROM content_signal_places WHERE place_id = %s", (pid,))
            has_signals = cur.fetchone()[0] > 0
            if source == "user" and not has_signals:
                cur.execute("DELETE FROM geo_places WHERE id = %s", (pid,))
                action = "deleted"
            else:
                cur.execute("UPDATE geo_places SET retired = TRUE, updated_at = NOW() WHERE id = %s", (pid,))
                action = "retired"
            revision = _record_curation(cur, "place.delete." + action)
            return action, 200, revision

        try:
            action, code, revision = _write(do_delete)
        except Exception as exc:
            logger.exception("delete place failed for %s", place_key)
            if type(exc).__name__ == "GazetteerUnavailable":
                return _error("GAZETTEER_UNAVAILABLE", "Gazetteer is not ready for curation", 503)
            return _error("PLACES_UNAVAILABLE", "Failed to delete place", 500)
        if code == 404:
            return _error("NOT_FOUND", "Place not found", 404)
        _audit("places.delete", place_key, {"action": action, "gazetteer_load_id": revision["load_id"]})
        return jsonify({"success": True, "place_key": place_key,
                        "action": action, "gazetteer_revision": revision}), 200

    @app.route("/api/place-names", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_place_names():
        try:
            page = _int("page", 1, 1, 100_000)
            per_page = _int("per_page", 25, 1, MAX_PER_PAGE)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        q = (request.args.get("q") or "").strip()
        language = request.args.get("language") or None
        name_type = request.args.get("name_type") or None
        source = request.args.get("source") or None
        place_key = (request.args.get("place_key") or "").strip() or None
        status = request.args.get("status", "active")
        sort = request.args.get("sort", "name")
        order = request.args.get("order", "asc").lower()
        sort_columns = {"name": "lower(n.name)", "language": "n.language",
                        "name_type": "n.name_type", "place_label": "lower(p.label)",
                        "place_key": "p.place_key", "source": "n.source",
                        "homograph": "n.homograph"}
        if sort not in sort_columns or order not in ("asc", "desc"):
            return _error("VALIDATION_FAILED", "sort/order is not supported", 400)
        if language and language not in LANGUAGES:
            return _error("VALIDATION_FAILED", f"language must be one of {', '.join(LANGUAGES)}", 400)
        if name_type and name_type not in ("endonym", "exonym", "historical", "variant", "unclassified"):
            return _error("VALIDATION_FAILED", "name_type is not supported", 400)
        if source not in (None, "all", "user", "seed"):
            return _error("VALIDATION_FAILED", "source must be user, seed, or all", 400)
        if status not in ("active", "retired", "all"):
            return _error("VALIDATION_FAILED", "status must be active, retired, or all", 400)
        if len(q) > 100 or len(place_key or "") > 100:
            return _error("VALIDATION_FAILED", "search fields are limited to 100 characters", 400)

        where, params = [], []
        if status == "active":
            where.append("NOT p.retired")
        elif status == "retired":
            where.append("p.retired")
        if source == "user":
            where.append("n.source = 'user'")
        elif source == "seed":
            where.append("n.source <> 'user'")
        if language:
            where.append("n.language = %s")
            params.append(language)
        if name_type:
            where.append("n.name_type = %s")
            params.append(name_type)
        if place_key:
            where.append("p.place_key = %s")
            params.append(place_key)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(n.name ILIKE %s OR p.label ILIKE %s OR p.place_key ILIKE %s"
                         " OR lower(n.match_key) LIKE %s)")
            normalized_like = (match_key(q).lower().replace("\\", "\\\\")
                               .replace("%", "\\%").replace("_", "\\_"))
            params.extend([f"%{escaped}%", f"%{escaped}%", f"%{escaped}%",
                           normalized_like + "%"])
        clause = " AND ".join(where) if where else "TRUE"

        def run(cur):
            cur.execute("SELECT count(*) FROM geo_place_names n JOIN geo_places p ON p.id = n.place_id WHERE " + clause,
                        params)
            total = cur.fetchone()[0]
            cur.execute("SELECT n.id, p.place_key, p.label, n.name, n.match_key, n.language, n.script,"
                        " n.name_type, n.homograph, n.source, n.note, p.retired"
                        " FROM geo_place_names n JOIN geo_places p ON p.id = n.place_id WHERE " + clause +
                        f" ORDER BY {sort_columns[sort]} {order.upper()}, n.id ASC LIMIT %s OFFSET %s",
                        params + [per_page, (page - 1) * per_page])
            return total, cur.fetchall()

        try:
            total, rows = _read(run)
        except Exception:
            logger.exception("place-name search failed")
            return _error("PLACES_UNAVAILABLE", "Place names could not be read", 500)
        items = [{"id": r[0], "place_key": r[1], "place_label": r[2], "name": r[3],
                  "match_key": r[4], "language": r[5], "script": r[6],
                  "name_type": r[7], "homograph": bool(r[8]), "source": r[9],
                  "note": r[10], "place_retired": bool(r[11]), "editable": r[9] == "user"}
                 for r in rows]
        return jsonify({"success": True, "data": items, "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page}, "sort": sort, "order": order})

    @app.route("/api/place-names", methods=["POST"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_place_name_create():
        if not _admin_required():
            return _error("FORBIDDEN", "Admin privilege required", 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "A JSON object is required", 400)
        place_key = data.get("place_key")
        if not isinstance(place_key, str) or not place_key.strip() or len(place_key.strip()) > 100:
            return _error("VALIDATION_FAILED", "place_key is required", 400)
        name, error = _clean_name(data)
        if error:
            return _error("VALIDATION_FAILED", error, 400)

        def create(cur):
            from services.geo.gazetteer import lock_curation
            lock_curation(cur)
            cur.execute("SELECT id FROM geo_places WHERE place_key = %s AND NOT retired FOR UPDATE",
                        (place_key.strip(),))
            row = cur.fetchone()
            if row is None:
                return None, 404, None
            cur.execute("INSERT INTO geo_place_names (place_id, name, match_key, language, script,"
                        " name_type, homograph, source, note) VALUES (%s, %s, %s, %s, %s, %s, %s, 'user', %s)"
                        " RETURNING id",
                        (row[0], name["name"], name["match_key"], name["language"], name["script"],
                         name["name_type"], name["homograph"], name["note"]))
            name_id = cur.fetchone()[0]
            revision = _record_curation(cur, "place_name.create")
            return name_id, 201, revision

        try:
            name_id, code, revision = _write(create)
        except Exception as exc:
            logger.exception("create place alias failed")
            if _unique_conflict(exc):
                return _error("CONFLICT", "That normalized alias already exists for this place and language", 409)
            if type(exc).__name__ == "GazetteerUnavailable":
                return _error("GAZETTEER_UNAVAILABLE", "Gazetteer is not ready for curation", 503)
            return _error("PLACES_UNAVAILABLE", "Place alias could not be created", 500)
        if code == 404:
            return _error("NOT_FOUND", "An active place with that key was not found", 404)
        _audit("places.name.create", name_id,
               {"place_key": place_key, "name": name["name"], "language": name["language"],
                "gazetteer_load_id": revision["load_id"]}, target_type="place_name")
        return jsonify({"success": True, "id": name_id, "gazetteer_revision": revision}), 201

    @app.route("/api/place-names/<int:name_id>", methods=["PUT"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_place_name_update(name_id):
        if not _admin_required():
            return _error("FORBIDDEN", "Admin privilege required", 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "A JSON object is required", 400)
        if not data or set(data) - {"place_key", "name", "language", "name_type", "homograph", "note"}:
            return _error("VALIDATION_FAILED", "Only place, name, language, type, homograph, and note can be edited", 400)

        def update(cur):
            from services.geo.gazetteer import lock_curation
            lock_curation(cur)
            cur.execute("SELECT n.place_id, n.source, p.place_key, n.name, n.language, n.name_type,"
                        " n.homograph, n.note FROM geo_place_names n JOIN geo_places p ON p.id = n.place_id"
                        " WHERE n.id = %s FOR UPDATE OF n", (name_id,))
            old = cur.fetchone()
            if old is None:
                return None, 404, None
            if old[1] != "user":
                return None, 403, None
            payload = {"name": data.get("name", old[3]), "language": data.get("language", old[4]),
                       "name_type": data.get("name_type", old[5]), "homograph": data.get("homograph", old[6]),
                       "note": data.get("note", old[7])}
            cleaned, error = _clean_name(payload)
            if error:
                return error, 400, None
            target_key = data.get("place_key", old[2])
            if not isinstance(target_key, str) or not target_key.strip() or len(target_key.strip()) > 100:
                return "place_key is invalid", 400, None
            cur.execute("SELECT id FROM geo_places WHERE place_key = %s AND NOT retired FOR UPDATE",
                        (target_key.strip(),))
            target = cur.fetchone()
            if target is None:
                return "An active place with that key was not found", 404, None
            cur.execute("UPDATE geo_place_names SET place_id = %s, name = %s, match_key = %s,"
                        " language = %s, script = %s, name_type = %s, homograph = %s, note = %s"
                        " WHERE id = %s",
                        (target[0], cleaned["name"], cleaned["match_key"], cleaned["language"],
                         cleaned["script"], cleaned["name_type"], cleaned["homograph"], cleaned["note"], name_id))
            revision = _record_curation(cur, "place_name.update")
            return {"id": name_id, "place_key": target_key.strip(), "name": cleaned["name"],
                    "language": cleaned["language"], "name_type": cleaned["name_type"],
                    "homograph": cleaned["homograph"], "note": cleaned["note"],
                    "previous": {"place_key": old[2], "name": old[3], "language": old[4],
                                 "name_type": old[5], "homograph": old[6], "note": old[7]}}, 200, revision

        try:
            result, code, revision = _write(update)
        except Exception as exc:
            logger.exception("update place alias failed for %s", name_id)
            if _unique_conflict(exc):
                return _error("CONFLICT", "That normalized alias already exists for this place and language", 409)
            if type(exc).__name__ == "GazetteerUnavailable":
                return _error("GAZETTEER_UNAVAILABLE", "Gazetteer is not ready for curation", 503)
            return _error("PLACES_UNAVAILABLE", "Place alias could not be updated", 500)
        if code == 403:
            return _error("FORBIDDEN", "Seed-managed names are read-only; add a user alias instead", 403)
        if code == 400:
            return _error("VALIDATION_FAILED", result, 400)
        if code == 404:
            return _error("NOT_FOUND", result or "Place name not found", 404)
        _audit("places.name.update", name_id, {**result, "gazetteer_load_id": revision["load_id"]},
               target_type="place_name")
        return jsonify({"success": True, "name": result, "gazetteer_revision": revision}), 200

    @app.route("/api/place-names/<int:name_id>", methods=["DELETE"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_place_name_delete(name_id):
        if not _admin_required():
            return _error("FORBIDDEN", "Admin privilege required", 403)

        def delete(cur):
            from services.geo.gazetteer import lock_curation
            lock_curation(cur)
            cur.execute("SELECT n.source, n.name, n.language, n.name_type, n.homograph, n.note, p.place_key"
                        " FROM geo_place_names n JOIN geo_places p ON p.id = n.place_id"
                        " WHERE n.id = %s FOR UPDATE OF n", (name_id,))
            row = cur.fetchone()
            if row is None:
                return None, 404, None
            if row[0] != "user":
                return None, 403, None
            cur.execute("DELETE FROM geo_place_names WHERE id = %s", (name_id,))
            revision = _record_curation(cur, "place_name.delete")
            detail = {"name": row[1], "language": row[2], "name_type": row[3],
                      "homograph": row[4], "note": row[5], "place_key": row[6]}
            return detail, 200, revision

        try:
            name, code, revision = _write(delete)
        except Exception as exc:
            logger.exception("delete place alias failed for %s", name_id)
            if type(exc).__name__ == "GazetteerUnavailable":
                return _error("GAZETTEER_UNAVAILABLE", "Gazetteer is not ready for curation", 503)
            return _error("PLACES_UNAVAILABLE", "Place alias could not be deleted", 500)
        if code == 403:
            return _error("FORBIDDEN", "Seed-managed names are read-only", 403)
        if code == 404:
            return _error("NOT_FOUND", "Place name not found", 404)
        _audit("places.name.delete", name_id, {**name, "gazetteer_load_id": revision["load_id"]},
               target_type="place_name")
        return jsonify({"success": True, "id": name_id, "gazetteer_revision": revision}), 200

    @app.route("/api/gazetteer/loads", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_gazetteer_loads():
        try:
            page = _int("page", 1, 1, 100_000)
            per_page = _int("per_page", 25, 1, MAX_PER_PAGE)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort", "loaded_at")
        order = request.args.get("order", "desc").lower()
        if sort not in LOAD_SORT or order not in ("asc", "desc"):
            return _error("VALIDATION_FAILED", "sort/order is not supported", 400)
        if len(q) > 100:
            return _error("VALIDATION_FAILED", "q is limited to 100 characters", 400)
        where, params = "TRUE", []
        if q:
            where = "(seed_version ILIKE %s OR loaded_by ILIKE %s OR content_sha256 ILIKE %s OR fingerprint ILIKE %s)"
            like = f"%{q}%"
            params = [like, like, like, like]
        def run(cur):
            cur.execute("SELECT count(*) FROM geo_gazetteer_loads WHERE " + where, params)
            total = cur.fetchone()[0]
            cur.execute("SELECT id, seed_version, content_sha256, fingerprint, place_count, name_count,"
                        " loaded_by, loaded_at FROM geo_gazetteer_loads WHERE " + where +
                        f" ORDER BY {LOAD_SORT[sort]} {order.upper()}, id DESC LIMIT %s OFFSET %s",
                        params + [per_page, (page - 1) * per_page])
            return total, cur.fetchall()
        try:
            total, rows = _read(run)
        except Exception:
            logger.exception("gazetteer load history read failed")
            return _error("GAZETTEER_UNAVAILABLE", "Gazetteer history could not be read", 500)
        items = [{"id": r[0], "seed_version": r[1], "content_sha256": r[2].strip(),
                  "fingerprint": r[3].strip(), "place_count": r[4], "name_count": r[5],
                  "loaded_by": r[6], "loaded_at": r[7].isoformat() if r[7] else None}
                 for r in rows]
        return jsonify({"success": True, "data": items, "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page}, "sort": sort, "order": order,
            "immutable": True})

    @app.route("/api/gazetteer/loads/<int:load_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_gazetteer_load_detail(load_id):
        def run(cur):
            cur.execute("SELECT id, seed_version, content_sha256, fingerprint, place_count, name_count,"
                        " stats, loaded_by, loaded_at FROM geo_gazetteer_loads WHERE id = %s", (load_id,))
            return cur.fetchone()
        try:
            row = _read(run)
        except Exception:
            logger.exception("gazetteer load detail read failed")
            return _error("GAZETTEER_UNAVAILABLE", "Gazetteer load could not be read", 500)
        if row is None:
            return _error("NOT_FOUND", "Gazetteer load not found", 404)
        return jsonify({"success": True, "load": {"id": row[0], "seed_version": row[1],
            "content_sha256": row[2].strip(), "fingerprint": row[3].strip(),
            "place_count": row[4], "name_count": row[5], "stats": row[6],
            "loaded_by": row[7], "loaded_at": row[8].isoformat() if row[8] else None,
            "immutable": True}})

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
