"""Monitoring rules API (step 11).

=====================================  ======================================
``GET    /api/rules``                  your rules (``?all=1``: every user's,
                                       administrators only;
                                       ``?include_archived=1``)
``POST   /api/rules``                  create: ``{name, definition}`` or
                                       ``{name, saved_search_id, definition}``
``GET    /api/rules/<id>``             one rule (owner or administrator)
``PUT    /api/rules/<id>``             rename / new definition (owner only)
``DELETE /api/rules/<id>``             archive (kept for history)
``POST   /api/rules/<id>/pause``       stop evaluating
``POST   /api/rules/<id>/resume``      evaluate again (re-checks the owner)
``POST   /api/rules/<id>/suppress``    ``{minutes}``; ``0`` lifts it
``GET    /api/rules/<id>/versions``    every definition the rule has had
``GET    /api/rules/<id>/evaluations`` evaluation log (``limit``/``offset``)
``POST   /api/rules/<id>/evaluate``    evaluate now (a ``rule_evaluation`` job)
``POST   /api/rules/evaluate``         every active rule (administrators)
=====================================  ======================================

Rules and their evaluation are in services/monitoring; the notifications
they produce are read through /api/notifications, addressed to the owner.
Absent and not-permitted rules are both 404. Rule changes are written to the
audit log.
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.security.flask_ext import (
    admin_required, current_user, login_required, write_access_required)
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

MAX_EVALUATION_PAGE = 200


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _actor():
    user = current_user()
    return user, getattr(user, "id", None), bool(user and user.has_role("admin"))


def _audit(action, rule_id, detail=None):
    from core.security.service import get_auth_service

    user, uid, _ = _actor()
    get_auth_service().audit(action, user_id=uid, username=getattr(user, "username", None),
                             resource=f"monitoring_rule:{rule_id}", detail=detail,
                             ip_address=request.remote_addr)


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("a JSON object is required")
    return data


def register_rule_routes(app):
    from Api.utils.utils import get_connection
    from services.monitoring import rules
    from services.monitoring.rules import RuleError

    def rule_error(exc: RuleError):
        return _error(exc.code, exc.message, exc.status)

    def with_conn(fn):
        with get_connection() as conn:
            return fn(conn)

    @app.route("/api/rules", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_rules_list():
        _, uid, is_admin = _actor()
        all_users = request.args.get("all") in ("1", "true")
        if all_users and not is_admin:
            return _error("FORBIDDEN", "only administrators may list every user's rules", 403)
        rows = with_conn(lambda c: rules.list_rules(
            c, user_id=uid, is_admin=is_admin, all_users=all_users,
            include_archived=request.args.get("include_archived") in ("1", "true")))
        truncated = len(rows) > rules.MAX_LIST
        return jsonify({"success": True, "rules": [rules.rule_to_api(r)
                                                   for r in rows[:rules.MAX_LIST]],
                        "count": min(len(rows), rules.MAX_LIST), "truncated": truncated,
                        "limit": rules.MAX_LIST})

    @app.route("/api/rules", methods=["POST"])
    @write_access_required
    def api_rules_create():
        try:
            data = _json_body()
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        unknown = sorted(set(data) - {"name", "definition", "saved_search_id"})
        if unknown:
            return _error("VALIDATION_FAILED", f"unknown field(s): {', '.join(unknown)}", 400)
        _, uid, is_admin = _actor()
        try:
            rule = with_conn(lambda c: rules.create_rule(
                c, owner_id=uid, is_admin=is_admin, name=data.get("name"),
                definition=data.get("definition"), saved_search_id=data.get("saved_search_id")))
        except RuleError as exc:
            return rule_error(exc)
        _audit("rule.created", rule["id"], {"version": 1,
                                            "fingerprint": rule["definition_fingerprint"],
                                            "saved_search_id": rule["saved_search_id"]})
        return jsonify({"success": True, "rule": rules.rule_to_api(rule)}), 201

    @app.route("/api/rules/<int:rule_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_rule_get(rule_id):
        _, uid, is_admin = _actor()
        try:
            rule = with_conn(lambda c: rules.get_rule(c, rule_id, user_id=uid, is_admin=is_admin))
        except RuleError as exc:
            return rule_error(exc)
        return jsonify({"success": True, "rule": rules.rule_to_api(rule)})

    @app.route("/api/rules/<int:rule_id>", methods=["PUT"])
    @write_access_required
    def api_rule_update(rule_id):
        try:
            data = _json_body()
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        unknown = sorted(set(data) - {"name", "definition"})
        if unknown:
            return _error("VALIDATION_FAILED", f"unknown field(s): {', '.join(unknown)}", 400)
        _, uid, is_admin = _actor()
        try:
            rule = with_conn(lambda c: rules.update_rule(
                c, rule_id, user_id=uid, is_admin=is_admin, name=data.get("name"),
                definition=data.get("definition")))
        except RuleError as exc:
            return rule_error(exc)
        _audit("rule.updated", rule_id, {"version": rule["version"],
                                         "fingerprint": rule["definition_fingerprint"]})
        return jsonify({"success": True, "rule": rules.rule_to_api(rule)})

    def _transition(rule_id, action):
        _, uid, is_admin = _actor()
        try:
            rule = with_conn(lambda c: rules.set_status(c, rule_id, action, user_id=uid,
                                                        is_admin=is_admin))
        except RuleError as exc:
            return rule_error(exc)
        _audit(f"rule.{action}", rule_id, {"status": rule["status"]})
        return jsonify({"success": True, "rule": rules.rule_to_api(rule)})

    @app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
    @write_access_required
    def api_rule_archive(rule_id):
        return _transition(rule_id, "archive")

    @app.route("/api/rules/<int:rule_id>/pause", methods=["POST"])
    @write_access_required
    def api_rule_pause(rule_id):
        return _transition(rule_id, "pause")

    @app.route("/api/rules/<int:rule_id>/resume", methods=["POST"])
    @write_access_required
    def api_rule_resume(rule_id):
        return _transition(rule_id, "resume")

    @app.route("/api/rules/<int:rule_id>/suppress", methods=["POST"])
    @write_access_required
    def api_rule_suppress(rule_id):
        try:
            data = _json_body()
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        _, uid, is_admin = _actor()
        try:
            rule = with_conn(lambda c: rules.suppress(c, rule_id, minutes=data.get("minutes"),
                                                      user_id=uid, is_admin=is_admin))
        except RuleError as exc:
            return rule_error(exc)
        _audit("rule.suppressed", rule_id, {"suppressed_until": rule["suppressed_until"]
                                            and rule["suppressed_until"].isoformat()})
        return jsonify({"success": True, "rule": rules.rule_to_api(rule)})

    def _visible(conn, rule_id):
        _, uid, is_admin = _actor()
        return rules.get_rule(conn, rule_id, user_id=uid, is_admin=is_admin)

    @app.route("/api/rules/<int:rule_id>/versions", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_rule_versions(rule_id):
        try:
            versions = with_conn(lambda c: (_visible(c, rule_id),
                                            rules.list_versions(c, rule_id))[1])
        except RuleError as exc:
            return rule_error(exc)
        return jsonify({"success": True, "versions": versions})

    @app.route("/api/rules/<int:rule_id>/evaluations", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_rule_evaluations(rule_id):
        from services.detection.signal_query import SignalQueryError, _page

        try:
            limit, offset = _page(request.args.get("limit", 50), request.args.get("offset", 0))
        except SignalQueryError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        try:
            items, total = with_conn(lambda c: (_visible(c, rule_id), rules.list_evaluations(
                c, rule_id, limit=limit, offset=offset))[1])
        except RuleError as exc:
            return rule_error(exc)
        return jsonify({"success": True, "evaluations": items, "total": total,
                        "limit": limit, "offset": offset})

    def _start_job(rule_ids, source):
        from services.jobs.manager import JobManager
        from services.jobs.models import job_to_api

        manager = JobManager.get_instance()
        user = current_user()
        try:
            job = manager.create_job(
                "rule_evaluation", source=source,
                options={"rule_ids": rule_ids,
                         "trigger": "manual" if rule_ids else "all_rules"},
                created_by=getattr(user, "username", None) or "system")
        except Exception:
            logger.exception("rule evaluation job creation failed")
            return _error("JOB_CREATE_FAILED", "Evaluation job could not be created", 500)
        if manager.synchronous:
            return jsonify({"success": True, "job": job_to_api(manager.get(job["job_id"]))})
        return jsonify({"success": True, "job": job_to_api(job)}), 202

    @app.route("/api/rules/<int:rule_id>/evaluate", methods=["POST"])
    @write_access_required
    @limiter.limit("30 per minute")
    def api_rule_evaluate(rule_id):
        try:
            rule = with_conn(lambda c: _visible(c, rule_id))
        except RuleError as exc:
            return rule_error(exc)
        if rule["status"] != "active":
            return _error("CONFLICT", f"a {rule['status']} rule is not evaluated", 409)
        return _start_job([rule_id], f"rules:{rule_id}")

    @app.route("/api/rules/evaluate", methods=["POST"])
    @admin_required
    @limiter.limit("10 per minute")
    def api_rules_evaluate_all():
        return _start_job(None, "rules:all")
