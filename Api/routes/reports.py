"""Reports API (step 14: runs).

============================================================  =====================================
``GET  /reports``                                              the Reports page (registry ``reports``)
``GET  /api/reports/definitions``                              the reports your role may run, with
                                                               their parameters and datasets
``POST /api/reports/runs``                                     run one ``{report_id, version?,
                                                               parameters?, saved_search_id?}``
                                                               (a ``report_run`` job; 202)
``GET  /api/reports/runs``                                     your runs (``?all=1``: everyone's,
                                                               administrators; ``report_id``,
                                                               ``status``, ``limit``, ``offset``)
``GET  /api/reports/runs/<id>``                                one run and its dataset summaries
``GET  /api/reports/runs/<id>/datasets/<key>``                 a page of one dataset's rows
                                                               (``limit`` <= 500, ``offset``)
============================================================  =====================================

Creating a run is a write: viewers are read-only (SEC-02), so they see the
reports their role may read (``can_run: false``) and get 403 on submission.
A report the caller's role may not read, and a run the caller may not read,
are both 404 (absent and not permitted are the same answer). Submitting a run
is audited (``report.run``). Viewing stored rows in the application is not an
export; downloading an artifact (step 15) is, and will record DATA_EXPORTED.
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.security.flask_ext import current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _int_arg(name, default):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None


def definition_to_api(definition, registry, *, can_run=False):
    """What the page needs to show and fill a report's form. Labels are
    translated for the caller's language; ids stay as declared."""
    from flask_babel import gettext as _

    topic = registry.help_topic(definition.help_topic)
    datasets = []
    for key in definition.datasets:
        ds = registry.dataset(key)
        datasets.append({
            "key": key, "unit": ds.unit, "semantics": ds.semantics,
            "row_limit": ds.row_limit, "description": ds.description,
            "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable,
                         "label": _(c.label)} for c in ds.columns],
        })
    return {
        "report_id": definition.report_id, "version": definition.version,
        "key": definition.key, "title": _(definition.title),
        "description": _(definition.description), "unit": definition.unit,
        "help_topic": definition.help_topic,
        "help": ({"title": _(topic.title), "summary": _(topic.summary)} if topic else None),
        "parameters": [dict(p.semantic(), label=_(p.label)) for p in definition.parameters],
        "datasets": datasets,
        "can_run": bool(can_run),
    }


def register_report_routes(app):
    from Api.utils.utils import get_connection
    from core.reporting import REGISTRY
    from services.reporting import runs as store

    def err(exc):
        return _error(exc.code, exc.message, exc.status)

    @app.route("/reports", methods=["GET"])
    @login_required
    def reports_page():
        """Report definitions, the run form and run history (registry
        ``reports``). A view over the reports API only."""
        from flask import render_template

        user = current_user()
        return render_template("Reports/reports.html", page_data={
            "user_id": getattr(user, "id", None),
            "can_run": store.can_run(getattr(user, "role", None)),
            "is_admin": bool(user and user.has_role("admin")),
            "list_limit": 50, "row_page": 100, "max_row_page": store.MAX_ROW_PAGE,
            "job_poll_ms": 1500, "job_poll_limit": 400,
        })

    @app.route("/api/reports/definitions", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_definitions():
        user = current_user()
        role = getattr(user, "role", None)
        items = [definition_to_api(d, REGISTRY, can_run=store.can_run(role))
                 for d in store.visible_definitions(role)]
        return jsonify({"success": True, "items": items})

    @app.route("/api/reports/runs", methods=["POST"])
    @login_required
    @limiter.limit("30 per minute")
    def api_report_run_create():
        from core.security.service import get_auth_service
        from services.jobs.manager import JobManager
        from services.jobs.models import job_to_api

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "a JSON object is required", 400)
        unknown = sorted(set(data) - {"report_id", "version", "parameters", "saved_search_id"})
        if unknown:
            return _error("VALIDATION_FAILED", f"unknown field(s): {', '.join(unknown)}", 400)
        user = current_user()
        try:
            with get_connection() as conn:
                run = store.submit_run(conn, user=user, report_id=data.get("report_id"),
                                       version=data.get("version"),
                                       parameters=data.get("parameters"),
                                       saved_search_id=data.get("saved_search_id"))
        except store.ReportRunError as exc:
            return err(exc)
        get_auth_service().audit(
            "report.run", user_id=getattr(user, "id", None),
            username=getattr(user, "username", None), resource=f"report_run:{run['id']}",
            detail={"report": run["report_key"],
                    "parameters_fingerprint": run["parameters_fingerprint"],
                    "criteria_fingerprint": run["criteria_fingerprint"],
                    "saved_search_id": run["saved_search_id"]},
            ip_address=request.remote_addr)
        manager = JobManager.get_instance()
        try:
            job = manager.create_job("report_run", source=f"reports:{run['report_key']}",
                                     options={"run_id": run["id"]},
                                     created_by=getattr(user, "username", None) or "system")
        except Exception:
            logger.exception("report_run job creation failed for run %s", run["id"])
            with get_connection() as conn:
                store.fail_unstarted(conn, run["id"], "the report job could not be created")
            return _error("JOB_CREATE_FAILED", "The job could not be created", 500)
        with get_connection() as conn:
            run = store.get_run(conn, run["id"], user=user)
        status = 200 if manager.synchronous else 202
        return jsonify({"success": True, "run": run,
                        "job": job_to_api(manager.get(job["job_id"]) or job)}), status

    @app.route("/api/reports/runs", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_runs():
        user = current_user()
        try:
            limit, offset = _int_arg("limit", 50), _int_arg("offset", 0)
            with get_connection() as conn:
                page = store.list_runs(conn, user=user,
                                       all_users=request.args.get("all") in ("1", "true"),
                                       report_id=request.args.get("report_id") or None,
                                       status=request.args.get("status") or None,
                                       limit=limit, offset=offset)
        except store.ReportRunError as exc:
            return err(exc)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        return jsonify(dict(page, success=True))

    @app.route("/api/reports/runs/<int:run_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_run(run_id):
        try:
            with get_connection() as conn:
                run = store.get_run(conn, run_id, user=current_user())
        except store.ReportRunError as exc:
            return err(exc)
        return jsonify({"success": True, "run": run})

    @app.route("/api/reports/runs/<int:run_id>/datasets/<dataset_key>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_run_rows(run_id, dataset_key):
        try:
            limit, offset = _int_arg("limit", 100), _int_arg("offset", 0)
            with get_connection() as conn:
                page = store.dataset_rows(conn, run_id, dataset_key, user=current_user(),
                                          limit=limit, offset=offset)
        except store.ReportRunError as exc:
            return err(exc)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        return jsonify(dict(page, success=True))
