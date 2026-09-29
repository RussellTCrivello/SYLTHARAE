"""Reports API (step 14: runs; step 15: artifacts).

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
``GET  /api/reports/runs/<id>/artifacts``                      the files made from a run
``POST /api/reports/runs/<id>/artifacts``                      make one ``{format, dataset_key?}``
                                                               (a ``report_artifact`` job; 202;
                                                               200 with the existing file when
                                                               that rendering already exists)
``GET  /api/reports/artifacts/<id>``                           one file's record and manifest
``GET  /api/reports/artifacts/<id>/verify``                    recompute and compare both digests
``GET  /api/reports/artifacts/<id>/download``                  the bytes (``DATA_EXPORTED``)
``GET  /api/reports/artifacts/<id>/manifest``                  the manifest file (canonical JSON;
                                                               its SHA-256 is ``manifest_sha256``)
============================================================  =====================================

Creating a run is a write: viewers are read-only (SEC-02), so they see the
reports their role may read (``can_run: false``) and get 403 on submission.
A report the caller's role may not read, and a run the caller may not read,
are both 404 (absent and not permitted are the same answer). Submitting a run
is audited (``report.run``). Viewing stored rows in the application is not an
export; downloading an artifact or its manifest is, and records DATA_EXPORTED
(fail-closed hook, ``core/security/disclosure.py``) with the report, run,
criteria and query fingerprints. Creating a file is a write (analyst/admin)
and is audited (``report.artifact``). Files are visible exactly when their run
is; a file whose bytes no longer match the recorded SHA-256 is never sent.
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
        "parameters": [dict(p.semantic(), label=_(p.label),
                            choice_labels=[_(c) for c in p.choice_labels])
                       for p in definition.parameters],
        "datasets": datasets,
        "analyses": [{"key": key, "title": _(registry.analysis(key).title),
                      "kind": registry.analysis(key).kind} for key in definition.analyses],
        "can_run": bool(can_run),
    }


def analysis_to_api(analysis, registry):
    """A stored analysis with its narrative rendered in the caller's language
    from the stored template references (the references are returned too)."""
    from flask_babel import format_decimal, get_translations, gettext as _

    from core.analytics.narrative import render as render_narrative
    from core.reporting.registry import ReportNotFound

    def number(value):
        if isinstance(value, int):
            return format_decimal(value)
        return format_decimal(value, format="#,##0.00")

    try:
        title = _(registry.analysis(analysis["analysis_key"]).title)
    except ReportNotFound:
        title = analysis["analysis_key"]
    return dict(analysis, title=title,
                # The raw catalog lookup, not flask_babel.ngettext: that one
                # formats with {"num": n} itself; render fills the named
                # parameters (numbers through the locale) after choosing the
                # plural form by the catalog's Plural-Forms.
                text=render_narrative(analysis["narrative"], _, number,
                                      ngettext=get_translations().ngettext))


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
        run["analyses"] = [analysis_to_api(a, REGISTRY) for a in run.get("analyses", ())]
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


def register_report_artifact_routes(app):
    """Step 15: artifacts of completed runs (services/reporting/artifacts.py)."""
    from flask import Response

    from Api.utils.utils import get_connection
    from core.security.disclosure import note_disclosure
    from services.reporting import artifacts as files

    def err(exc):
        return _error(exc.code, exc.message, exc.status)

    @app.route("/api/reports/runs/<int:run_id>/artifacts", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_artifacts(run_id):
        try:
            with get_connection() as conn:
                items = files.list_artifacts(conn, run_id, user=current_user())
        except files.runs.ReportRunError as exc:
            return err(exc)
        from core.reporting.render import NOT_YET, RENDERERS, SINGLE_DATASET
        return jsonify({"success": True, "items": items, "formats": [
            {"format": f, "renderer_version": v, "single_dataset": f in SINGLE_DATASET}
            for f, v in sorted(RENDERERS.items())],
            "unavailable": [{"format": f, "reason": r} for f, r in sorted(NOT_YET.items())]})

    @app.route("/api/reports/runs/<int:run_id>/artifacts", methods=["POST"])
    @login_required
    @limiter.limit("30 per minute")
    def api_report_artifact_create(run_id):
        from core.security.service import get_auth_service
        from services.jobs.manager import JobManager
        from services.jobs.models import job_to_api

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "a JSON object is required", 400)
        unknown = sorted(set(data) - {"format", "dataset_key"})
        if unknown:
            return _error("VALIDATION_FAILED", f"unknown field(s): {', '.join(unknown)}", 400)
        user = current_user()
        try:
            with get_connection() as conn:
                plan = files.request_artifact(conn, run_id, user=user, fmt=data.get("format"),
                                              dataset_key=data.get("dataset_key"))
        except files.runs.ReportRunError as exc:
            return err(exc)
        if "existing" in plan:
            return jsonify({"success": True, "existing": True, "artifact": plan["existing"],
                            "job": None}), 200
        spec = plan["create"]
        get_auth_service().audit(
            "report.artifact", user_id=getattr(user, "id", None),
            username=getattr(user, "username", None), resource=f"report_run:{run_id}",
            detail={"format": spec["format"], "dataset_key": spec["dataset_key"]},
            ip_address=request.remote_addr)
        manager = JobManager.get_instance()
        try:
            job = manager.create_job(
                "report_artifact", source=f"reports:run:{run_id}:{spec['format']}",
                options={"run_id": run_id, "format": spec["format"],
                         "dataset_key": spec["dataset_key"],
                         "creator_id": getattr(user, "id", None)},
                created_by=getattr(user, "username", None) or "system")
        except Exception:
            logger.exception("report_artifact job creation failed for run %s", run_id)
            return _error("JOB_CREATE_FAILED", "The job could not be created", 500)
        current = manager.get(job["job_id"]) or job
        artifact = None
        if manager.synchronous:
            with get_connection() as conn:
                found = files._existing(conn, run_id, spec["format"], spec["dataset_key"])
            artifact = files.artifact_to_api(found) if found else None
        return jsonify({"success": True, "existing": False, "artifact": artifact,
                        "job": job_to_api(current)}), (200 if manager.synchronous else 202)

    @app.route("/api/reports/artifacts/<int:artifact_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_artifact(artifact_id):
        try:
            with get_connection() as conn:
                item = files.get_artifact(conn, artifact_id, user=current_user())
        except files.runs.ReportRunError as exc:
            return err(exc)
        return jsonify({"success": True, "artifact": item})

    @app.route("/api/reports/artifacts/<int:artifact_id>/verify", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_artifact_verify(artifact_id):
        try:
            with get_connection() as conn:
                outcome = files.verify_artifact(conn, artifact_id, user=current_user())
        except files.runs.ReportRunError as exc:
            return err(exc)
        return jsonify(dict(outcome, success=True))

    def _disclose(row, what):
        manifest = row["manifest"]
        note_disclosure(
            kind="report_artifact" if what == "content" else "report_manifest",
            scope=f"report_run:{row['run_id']}", format=row["format"] if what == "content"
            else "json", row_count=manifest.get("row_count"),
            truncated=manifest.get("truncated"),
            criteria_fingerprint=manifest["run"].get("criteria_fingerprint"),
            query_fingerprint=[d["query_fingerprint"] for d in manifest["datasets"]
                               if row["dataset_key"] in (None, d["dataset_key"])],
            report=manifest["report"]["key"],
            definition_fingerprint=manifest["report"]["definition_fingerprint"],
            report_run_id=row["run_id"], artifact_id=row["id"],
            artifact_content_sha256=row["sha256"],
            manifest_sha256=row["manifest_sha256"],
            snapshot=manifest["snapshot"]["id"])

    def _attachment(body, media_type, filename, row):
        response = Response(body, mimetype=media_type)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Artifact-SHA256"] = row["sha256"]
        response.headers["X-Manifest-SHA256"] = row["manifest_sha256"]
        return response

    @app.route("/api/reports/artifacts/<int:artifact_id>/download", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_artifact_download(artifact_id):
        try:
            with get_connection() as conn:
                row = files.artifact_for_download(conn, artifact_id, user=current_user())
        except files.runs.ReportRunError as exc:
            return err(exc)
        _disclose(row, "content")
        return _attachment(row["content"], row["media_type"], row["filename"], row)

    @app.route("/api/reports/artifacts/<int:artifact_id>/manifest", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_report_artifact_manifest(artifact_id):
        from core.reporting.render import canonical_json

        try:
            with get_connection() as conn:
                row = files.artifact_for_download(conn, artifact_id, user=current_user())
        except files.runs.ReportRunError as exc:
            return err(exc)
        _disclose(row, "manifest")
        body = canonical_json(row["manifest"])
        return _attachment(body, "application/json", row["filename"] + ".manifest.json", row)
