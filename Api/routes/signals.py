"""Content signals API (content-keyed; detectors ``temporal`` and ``places``).

* ``GET  /api/content/<hash_id>/signals`` - any authenticated user. Signals
  and the detection run status (per detector, ``runs``) for one content;
  place signals carry their candidate places. ``detector`` (repeatable)
  restricts the detectors returned. Authorization is enforced in
  ``signal_store.signals_for`` against the caller's ``AccessScope`` before
  anything is read; "absent" and "not permitted" are the same 404.
  ``reference_date`` (YYYY-MM-DD) sets the clock used for past/future
  orientation. When omitted, the server's current UTC date is used and the
  response says so (``reference_date_source``) - the clock is never implicit.
* ``POST /api/signals/redetect`` - administrators. Queues a
  ``signal_redetection`` job on the existing JobManager (no second job
  framework). Body: ``{"scope": "stale"|"all"|"hash_ids", "hash_ids": [...],
  "detectors": ["temporal", "places"]}`` (``detectors`` optional, default all).
"""

import datetime
import logging
import uuid

from flask import jsonify, request

from core.criteria.access import scope_for
from core.security.flask_ext import admin_required, current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from services.detection import detectors as detector_registry
from services.detection import redetection, signal_store

logger = logging.getLogger(__name__)

#: Upper bound for an explicit hash_ids re-detection request.
MAX_REDETECT_IDS = 10_000


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _parse_reference_date(raw):
    if raw in (None, ""):
        return datetime.datetime.now(datetime.timezone.utc).date(), "server_utc_today"
    try:
        return datetime.date.fromisoformat(raw), "request"
    except (TypeError, ValueError):
        raise ValueError("reference_date must be an ISO date (YYYY-MM-DD)")


def register_signal_routes(app):

    @app.route("/api/content/<int:hash_id>/signals", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_content_signals(hash_id):
        try:
            reference_date, source = _parse_reference_date(request.args.get("reference_date"))
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        wanted = request.args.getlist("detector") or None
        try:
            detector_registry.validate(wanted)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        from Api.utils.utils import get_connection

        try:
            with get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        body = signal_store.signals_for(
                            cur, hash_id, reference_date, scope=scope_for(current_user()),
                            detectors=wanted)
                finally:
                    conn.rollback()  # read-only; never leave a transaction open
        except LookupError:
            return _error("NOT_FOUND", "Content not found", 404)
        except Exception:
            logger.exception("signals read failed for hash_id=%s", hash_id)
            return _error("SIGNALS_UNAVAILABLE", "Signals could not be read", 500)
        body["reference_date_source"] = source
        body["success"] = True
        return jsonify(body)

    @app.route("/api/signals/redetect", methods=["POST"])
    @admin_required
    @limiter.limit("10 per minute")
    def api_signals_redetect():
        data = request.get_json(silent=True) or {}
        scope = data.get("scope", "stale")
        if scope not in redetection.SCOPES:
            return _error("VALIDATION_FAILED",
                          f"scope must be one of: {', '.join(redetection.SCOPES)}", 400)
        hash_ids = None
        if scope == "hash_ids":
            raw = data.get("hash_ids")
            if (not isinstance(raw, list) or not raw
                    or not all(isinstance(h, int) and not isinstance(h, bool) and h > 0
                               for h in raw)):
                return _error("VALIDATION_FAILED",
                              "hash_ids must be a non-empty list of positive integers", 400)
            if len(raw) > MAX_REDETECT_IDS:
                return _error("VALIDATION_FAILED",
                              f"at most {MAX_REDETECT_IDS} hash_ids per request", 400)
            hash_ids = sorted(set(raw))
        elif data.get("hash_ids"):
            return _error("VALIDATION_FAILED", "hash_ids is only valid with scope 'hash_ids'", 400)
        try:
            detectors = list(detector_registry.validate(data.get("detectors")))
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)

        from services.jobs.manager import JobManager
        from services.jobs.models import job_to_api

        manager = JobManager.get_instance()
        user = current_user()
        try:
            job = manager.create_job(
                "signal_redetection",
                source=f"signals:{scope}" + (f":{len(hash_ids)}" if hash_ids else "")
                + f":{'+'.join(detectors)}",
                options={"scope": scope, "hash_ids": hash_ids, "detectors": detectors},
                created_by=getattr(user, "username", None) or "system",
            )
        except Exception:
            logger.exception("signal re-detection job creation failed")
            return _error("JOB_CREATE_FAILED", "Re-detection job could not be created", 500)
        if manager.synchronous:
            job = manager.get(job["job_id"])
            return jsonify({"success": True, "job": job_to_api(job)})
        return jsonify({"success": True, "job": job_to_api(job)}), 202
