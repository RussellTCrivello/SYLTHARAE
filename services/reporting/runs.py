"""Report runs: submit, execute (a JobManager job), read (tables of m0022).

A run executes one registered report definition (``core/reporting``) for one
requester:

1. **Submit** (request thread, :func:`submit_run`). The definition is looked
   up by id (and version, or the active one); the requester's role must be
   allowed by the report *and every dataset* - otherwise the report does not
   exist for them (404, same as absent). Parameters are normalised by the
   definition (unknown names refused); criteria may come from a saved search
   the requester can read. Every dataset is bound once (pure, executes
   nothing) so an invalid request fails here, not in the job. A ``queued``
   run row is written with the definition, parameter and criteria
   fingerprints.
2. **Execute** (job ``report_run``, :func:`run_report_job`). The run moves to
   ``running``. All datasets are read inside **one** ``REPEATABLE READ, READ
   ONLY`` transaction; ``pg_current_snapshot()`` and its timestamp are
   recorded, so every dataset of the run describes the same moment. Inside
   that snapshot the requester is re-read: an inactive account or a role the
   report no longer allows makes the run ``refused`` (no rows are read).
   Each dataset binds ``row_limit + 1`` and fetches at most that many rows:
   ``exact`` overflow fails the whole run, ``capped`` overflow keeps
   ``row_limit`` rows and records ``truncated``, ``top_n`` keeps the ranked
   prefix and records that more rows existed. The cursor's columns must be
   exactly the declared columns, and a non-nullable column must not be NULL -
   a mismatch fails the run rather than storing something the definition
   does not describe. Results are written in a separate transaction, once.
3. **Read** (:func:`get_run`, :func:`list_runs`, :func:`dataset_rows`). A run
   is visible to its requester and to administrators, and only while the
   reader's role is still allowed by the report. Rows are paged in SQL.

No silent outcomes: a run ends ``completed``, ``failed`` (with the error),
``refused`` (with the reason) or ``cancelled``. A run whose job ended without
recording a result (the process died) is reconciled to ``failed`` when read.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import psycopg2
import psycopg2.errors
import psycopg2.extras

from core.criteria.model import sha256_hex
from core.reporting import REGISTRY
from core.reporting.model import ReportParameterError
from core.reporting.registry import ReportNotFound

logger = logging.getLogger(__name__)

#: Recorded on every run; bump when the execution semantics change.
GENERATOR_VERSION = "report-runner/1"
ISOLATION = "repeatable read, read only"
#: Per statement, inside the run's transaction.
RUN_STATEMENT_TIMEOUT_MS = 120_000
MAX_LIST = 200
MAX_ROW_PAGE = 500
TERMINAL = ("completed", "failed", "refused", "cancelled")
#: Creating a run is a write. The platform policy (SEC-02, docs/SECURITY.md:
#: mutating requests need analyst or admin) is not loosened for reports, so a
#: report that declares ``viewer`` is listed for viewers but not runnable by
#: them - stated in the ledger as a decision for the product owner.
RUN_ROLES = ("admin", "analyst")
STATUSES = ("queued", "running") + TERMINAL

_RUN_COLUMNS = (
    "id, report_id, report_version, definition_fingerprint, parameters,"
    " parameters_fingerprint, criteria_fingerprint, saved_search_id, requested_by,"
    " requester_username, requester_role, job_id, status, refusal_reason, error, snapshot,"
    " snapshot_at, isolation_level, generator_version, requested_at, started_at, finished_at")


class ReportRunError(Exception):
    """A request refused for a stated reason. ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _not_found(what: str = "report") -> ReportRunError:
    # Absent and not permitted are the same answer.
    return ReportRunError("NOT_FOUND", f"{what} not found", 404)


class _RunFailed(Exception):
    """Execution stopped for a reason recorded on the run."""


def _dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------

def allowed(definition, role: Optional[str], registry=REGISTRY) -> bool:
    """The report and every dataset it reads allow ``role``."""
    if role not in definition.roles:
        return False
    return all(role in registry.dataset(key).roles for key in definition.datasets)


def can_run(role: Optional[str]) -> bool:
    return role in RUN_ROLES


def visible_definitions(role: Optional[str], registry=REGISTRY):
    return tuple(d for d in registry.visible_to(role) if allowed(d, role, registry))


def definition_fingerprint(definition, registry=REGISTRY) -> str:
    return definition.fingerprint(registry.dataset_fingerprints())


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def _criteria_parameter(definition):
    params = [p for p in definition.parameters if p.type == "criteria"]
    return params[0] if len(params) == 1 else None


def _saved_search_criteria(cur, search_id: Any, user_id: int, is_admin: bool) -> Dict[str, Any]:
    from Api.services.saved_searches_repository import can_read

    if isinstance(search_id, bool) or not isinstance(search_id, int):
        raise ReportRunError("VALIDATION_FAILED", "saved_search_id must be a whole number")
    cur.execute("SELECT id, owner_user_id, criteria FROM saved_searches WHERE id = %s",
                (search_id,))
    row = cur.fetchone()
    if not can_read(row, user_id, is_admin):
        raise _not_found("saved search")
    return dict(row["criteria"] or {})


def submit_run(conn, *, user, report_id: Any, version: Any = None,
               parameters: Any = None, saved_search_id: Any = None,
               registry=REGISTRY) -> Dict[str, Any]:
    """Validate and record a ``queued`` run. Commits. Returns the API shape."""
    from core.criteria.access import scope_for

    if not isinstance(report_id, str) or not report_id:
        raise ReportRunError("VALIDATION_FAILED", "report_id is required")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ReportRunError("VALIDATION_FAILED", "version must be a whole number")
    if parameters is not None and not isinstance(parameters, dict):
        raise ReportRunError("VALIDATION_FAILED", "parameters must be an object")
    role = getattr(user, "role", None)
    user_id = getattr(user, "id", None)
    is_admin = bool(user is not None and user.has_role("admin"))
    try:
        definition = registry.report(report_id, version)
    except ReportNotFound:
        raise _not_found() from None
    if not allowed(definition, role, registry):
        raise _not_found()
    if definition.status != "active" and version is None:
        raise _not_found()
    if role not in RUN_ROLES:
        raise ReportRunError("FORBIDDEN", "viewers can read reports but not run them", 403)

    supplied = dict(parameters or {})
    conn.rollback()
    with _dict_cur(conn) as cur:
        if saved_search_id is not None:
            param = _criteria_parameter(definition)
            if param is None:
                raise ReportRunError("VALIDATION_FAILED",
                                     f"{definition.key} does not take search criteria")
            if param.name in supplied:
                raise ReportRunError(
                    "VALIDATION_FAILED",
                    f"give either saved_search_id or the {param.name} parameter, not both")
            supplied[param.name] = _saved_search_criteria(cur, saved_search_id, user_id,
                                                          is_admin)
    conn.rollback()
    try:
        normalized = definition.normalize_parameters(supplied)
        scope = scope_for(user)
        criteria_fps = {registry.dataset(key).bind(normalized, scope).criteria_fingerprint
                        for key in definition.datasets} - {None}
    except ReportParameterError as exc:
        raise ReportRunError("VALIDATION_FAILED", str(exc)) from None
    if len(criteria_fps) > 1:     # a definition fault, not a request fault
        raise RuntimeError(f"{definition.key}: datasets disagree on the criteria fingerprint")
    with _dict_cur(conn) as cur:
        cur.execute(
            "INSERT INTO report_runs (report_id, report_version, definition_fingerprint,"
            " parameters, parameters_fingerprint, criteria_fingerprint, saved_search_id,"
            " requested_by, requester_username, requester_role, generator_version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING " + _RUN_COLUMNS,
            (definition.report_id, definition.version,
             definition_fingerprint(definition, registry),
             psycopg2.extras.Json(normalized), sha256_hex(normalized),
             next(iter(criteria_fps), None), saved_search_id, user_id,
             getattr(user, "username", None) or "unknown", role, GENERATOR_VERSION))
        row = cur.fetchone()
    conn.commit()
    return run_to_api(row, registry=registry)


def fail_unstarted(conn, run_id: int, error: str) -> None:
    """The job for a queued run could not be created."""
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE report_runs SET status = 'failed', error = %s, finished_at = NOW()"
                    " WHERE id = %s AND status = 'queued'", (error, run_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@dataclass
class ReportJobResult:
    """Shaped like the other JobManager results (stats/errors/warnings)."""
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False


def jsonable(value: Any) -> Any:
    """A stored cell. Dates and timestamps as ISO 8601; ``Decimal`` as its
    exact decimal string (never a float); JSON values as they are."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return value


def query_fingerprint(sql: str, params) -> str:
    return sha256_hex({"sql": sql, "params": jsonable(list(params))})


def _read_dataset(cur, dataset, bound) -> Dict[str, Any]:
    cur.execute(bound.sql, bound.params)
    names = [d[0] for d in cur.description]
    declared = [c.name for c in dataset.columns]
    if names != declared:
        raise _RunFailed(f"{dataset.key} returned columns {names}, declared {declared}")
    fetched = cur.fetchmany(bound.fetch_limit)
    overflow = len(fetched) > dataset.row_limit
    if overflow and dataset.semantics == "exact":
        raise _RunFailed(f"{dataset.key} is exact but returned more than its limit of "
                         f"{dataset.row_limit} rows; no partial result is kept")
    rows = []
    for raw in fetched[:dataset.row_limit]:
        row = {}
        for column in dataset.columns:
            value = raw[column.name]
            if value is None and not column.nullable:
                raise _RunFailed(f"{dataset.key}: column {column.name} is declared "
                                 "NOT NULL but a row has no value")
            row[column.name] = jsonable(value)
        rows.append(row)
    return {
        "dataset_key": dataset.key, "dataset_fingerprint": dataset.fingerprint(),
        "query_fingerprint": query_fingerprint(bound.sql, bound.params),
        "semantics": dataset.semantics, "row_limit": dataset.row_limit,
        "row_count": len(rows), "truncated": overflow,
        "columns": [dict(c.semantic(), label=c.label) for c in dataset.columns],
        "rows": rows, "criteria_fingerprint": bound.criteria_fingerprint,
    }


def _finish(conn, run_id: int, status: str, *, error: Optional[str] = None,
            refusal: Optional[str] = None, snapshot: Optional[Dict[str, Any]] = None,
            datasets: Optional[List[Dict[str, Any]]] = None) -> None:
    conn.rollback()
    with conn.cursor() as cur:
        for position, ds in enumerate(datasets or ()):
            cur.execute(
                "INSERT INTO report_run_datasets (run_id, position, dataset_key,"
                " dataset_fingerprint, query_fingerprint, semantics, row_limit, row_count,"
                " truncated, columns, rows) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (run_id, position, ds["dataset_key"], ds["dataset_fingerprint"],
                 ds["query_fingerprint"], ds["semantics"], ds["row_limit"], ds["row_count"],
                 ds["truncated"], psycopg2.extras.Json(ds["columns"]),
                 psycopg2.extras.Json(ds["rows"])))
        snapshot = snapshot or {}
        cur.execute(
            "UPDATE report_runs SET status = %s, error = %s, refusal_reason = %s,"
            " snapshot = %s, snapshot_at = %s, isolation_level = %s, finished_at = NOW()"
            " WHERE id = %s AND status = 'running'",
            (status, error, refusal, snapshot.get("snapshot"), snapshot.get("snapshot_at"),
             ISOLATION if snapshot else None, run_id))
        if cur.rowcount != 1:
            conn.rollback()
            raise RuntimeError(f"report run {run_id} was no longer running")
    conn.commit()


def execute_run(conn, run_id: int, *, job_id: Optional[str] = None,
                cancel_cb: Optional[Callable[[], bool]] = None,
                progress_cb: Optional[Callable] = None,
                registry=REGISTRY) -> Dict[str, Any]:
    """Execute a queued run. Returns ``{"run_id", "status", "error",
    "refusal_reason", "datasets": [...counts...]}``; the outcome is always
    recorded on the run."""
    from core.criteria.access import scope_for

    conn.rollback()
    with _dict_cur(conn) as cur:
        cur.execute("UPDATE report_runs SET status = 'running', started_at = NOW(),"
                    " job_id = COALESCE(%s, job_id) WHERE id = %s AND status = 'queued'"
                    " RETURNING " + _RUN_COLUMNS, (job_id, run_id))
        run = cur.fetchone()
    conn.commit()
    if run is None:
        raise LookupError(f"report run {run_id} does not exist or is not queued")

    def outcome(status, **kw):
        return {"run_id": run_id, "status": status, "error": kw.get("error"),
                "refusal_reason": kw.get("refusal"), "datasets": kw.get("datasets", [])}

    try:
        definition = registry.report(run["report_id"], run["report_version"])
    except ReportNotFound:
        error = f"{run['report_id']}@{run['report_version']} is no longer registered"
        _finish(conn, run_id, "failed", error=error)
        return outcome("failed", error=error)
    if definition_fingerprint(definition, registry) != run["definition_fingerprint"]:
        error = (f"{definition.key} changed since the run was requested "
                 "(definition fingerprint differs); request it again")
        _finish(conn, run_id, "failed", error=error)
        return outcome("failed", error=error)

    results: List[Dict[str, Any]] = []
    snapshot: Dict[str, Any] = {}
    total = len(definition.datasets)
    try:
        with _dict_cur(conn) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", (RUN_STATEMENT_TIMEOUT_MS,))
            cur.execute("SELECT pg_current_snapshot()::text AS snapshot,"
                        " transaction_timestamp() AS snapshot_at")
            snapshot = dict(cur.fetchone())
            refusal = None
            user = None
            if run["requested_by"] is None:
                refusal = "requester_deleted"
            else:
                cur.execute("SELECT id, username, role, is_active FROM users WHERE id = %s",
                            (run["requested_by"],))
                user = cur.fetchone()
                if user is None:
                    refusal = "requester_deleted"
                elif not user["is_active"]:
                    refusal = "requester_inactive"
                elif not allowed(definition, user["role"], registry):
                    refusal = "requester_role"
            if refusal is None:
                scope = scope_for(SimpleNamespace(id=user["id"], role=user["role"]))
                for index, key in enumerate(definition.datasets):
                    if cancel_cb and cancel_cb():
                        conn.rollback()
                        _finish(conn, run_id, "cancelled", snapshot=snapshot)
                        return outcome("cancelled")
                    dataset = registry.dataset(key)
                    bound = dataset.bind(run["parameters"], scope)
                    if bound.criteria_fingerprint != run["criteria_fingerprint"]:
                        raise _RunFailed(f"{key}: criteria fingerprint differs from the "
                                         "one recorded at submission")
                    results.append(_read_dataset(cur, dataset, bound))
                    if progress_cb:
                        progress_cb({"percent": int(100 * (index + 1) / total),
                                     "current_phase": f"Report dataset {key}",
                                     "files_processed": index + 1, "files_total": total})
        conn.rollback()
    except _RunFailed as exc:
        conn.rollback()
        _finish(conn, run_id, "failed", error=str(exc))
        return outcome("failed", error=str(exc))
    except psycopg2.errors.QueryCanceled:
        conn.rollback()
        error = (f"a dataset query exceeded the {RUN_STATEMENT_TIMEOUT_MS // 1000} s "
                 "statement timeout; narrow the parameters")
        _finish(conn, run_id, "failed", error=error)
        return outcome("failed", error=error)
    except Exception as exc:
        conn.rollback()
        ref = uuid.uuid4().hex[:12]
        logger.exception("report run %s failed (ref %s)", run_id, ref)
        error = f"the report could not be computed ({type(exc).__name__}, ref {ref})"
        _finish(conn, run_id, "failed", error=error)
        return outcome("failed", error=error)

    if refusal is not None:
        _finish(conn, run_id, "refused", refusal=refusal, snapshot=snapshot)
        return outcome("refused", refusal=refusal)
    _finish(conn, run_id, "completed", snapshot=snapshot, datasets=results)
    return outcome("completed", datasets=[
        {k: r[k] for k in ("dataset_key", "row_count", "truncated", "semantics")}
        for r in results])


def run_report_job(get_connection: Callable, *, run_id: int, job_id: Optional[str] = None,
                   progress_cb: Optional[Callable] = None,
                   cancel_cb: Optional[Callable[[], bool]] = None) -> ReportJobResult:
    """JobManager entry point (job type ``report_run``)."""
    result = ReportJobResult()
    with get_connection() as conn:
        try:
            out = execute_run(conn, run_id, job_id=job_id, cancel_cb=cancel_cb,
                              progress_cb=progress_cb)
        except Exception:
            # Anything execute_run could not record itself: make sure the run
            # does not stay 'running' (the job fails with the exception).
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("UPDATE report_runs SET status = 'failed', finished_at = NOW(),"
                                " error = 'the report job stopped unexpectedly'"
                                " WHERE id = %s AND status IN ('queued', 'running')", (run_id,))
                conn.commit()
            except Exception:
                logger.exception("could not mark report run %s failed", run_id)
            raise
    result.stats = {"run_id": run_id, "status": out["status"],
                    "datasets": out["datasets"]}
    if out["status"] == "cancelled":
        result.cancelled = True
    elif out["status"] == "failed":
        result.errors.append(f"report run {run_id}: {out['error']}")
    elif out["status"] == "refused":
        result.errors.append(f"report run {run_id}: refused ({out['refusal_reason']})")
    else:
        for ds in out["datasets"]:
            if ds["truncated"]:
                result.warnings.append(
                    f"report run {run_id}: {ds['dataset_key']} is {ds['semantics']} and "
                    f"was shortened to {ds['row_count']} rows")
    return result


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def run_to_api(row: Dict[str, Any], *, registry=REGISTRY) -> Dict[str, Any]:
    try:
        definition = registry.report(row["report_id"], row["report_version"])
        title, unit = definition.title, definition.unit
    except ReportNotFound:
        title, unit = None, None
    return {
        "id": row["id"], "report_id": row["report_id"], "report_version": row["report_version"],
        "report_key": f"{row['report_id']}@{row['report_version']}",
        "title": title, "unit": unit,
        "definition_fingerprint": row["definition_fingerprint"],
        "parameters": row["parameters"],
        "parameters_fingerprint": row["parameters_fingerprint"],
        "criteria_fingerprint": row["criteria_fingerprint"],
        "saved_search_id": row["saved_search_id"],
        "requested_by": row["requested_by"], "requester_username": row["requester_username"],
        "requester_role": row["requester_role"], "job_id": row["job_id"],
        "status": row["status"], "refusal_reason": row["refusal_reason"],
        "error": row["error"], "snapshot": row["snapshot"],
        "snapshot_at": _iso(row["snapshot_at"]), "isolation_level": row["isolation_level"],
        "generator_version": row["generator_version"],
        "requested_at": _iso(row["requested_at"]), "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["finished_at"]),
    }


def _reconcile(conn, row: Dict[str, Any]) -> Dict[str, Any]:
    """A non-terminal run whose job has ended without recording a result."""
    if row["status"] in TERMINAL or not row["job_id"]:
        return row
    from services.jobs import job_state

    with _dict_cur(conn) as cur:
        cur.execute("SELECT status FROM jobs WHERE job_id = %s", (row["job_id"],))
        job = cur.fetchone()
    ended = job is None or job["status"] in job_state.TERMINAL_STATES
    if not ended:
        conn.rollback()
        return row
    with _dict_cur(conn) as cur:
        cur.execute("UPDATE report_runs SET status = 'failed', finished_at = NOW(),"
                    " error = 'the report job ended without recording a result'"
                    " WHERE id = %s AND status IN ('queued', 'running')"
                    " RETURNING " + _RUN_COLUMNS, (row["id"],))
        updated = cur.fetchone()
    conn.commit()
    return updated or row


def _readable(row, user, registry) -> bool:
    if row is None:
        return False
    is_admin = user.has_role("admin")
    if not is_admin and row["requested_by"] != getattr(user, "id", None):
        return False
    try:
        definition = registry.report(row["report_id"], row["report_version"])
    except ReportNotFound:
        return is_admin       # history of a retired definition: administrators only
    return allowed(definition, getattr(user, "role", None), registry)


def _fetch(conn, run_id: int, user, registry) -> Dict[str, Any]:
    conn.rollback()
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + _RUN_COLUMNS + " FROM report_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
    conn.rollback()
    if not _readable(row, user, registry):
        raise _not_found("report run")
    return _reconcile(conn, row)


def get_run(conn, run_id: int, *, user, registry=REGISTRY) -> Dict[str, Any]:
    """One run with its dataset summaries (no rows)."""
    row = _fetch(conn, run_id, user, registry)
    with _dict_cur(conn) as cur:
        cur.execute("SELECT position, dataset_key, dataset_fingerprint, query_fingerprint,"
                    " semantics, row_limit, row_count, truncated, columns"
                    " FROM report_run_datasets WHERE run_id = %s ORDER BY position", (run_id,))
        datasets = [dict(r) for r in cur.fetchall()]
    conn.rollback()
    return dict(run_to_api(row, registry=registry), datasets=datasets)


def list_runs(conn, *, user, all_users: bool = False, report_id: Optional[str] = None,
              status: Optional[str] = None, limit: int = 50, offset: int = 0,
              registry=REGISTRY) -> Dict[str, Any]:
    if status is not None and status not in STATUSES:
        raise ReportRunError("VALIDATION_FAILED", f"status must be one of: {', '.join(STATUSES)}")
    if all_users and not user.has_role("admin"):
        raise ReportRunError("FORBIDDEN", "only administrators can list every user's runs", 403)
    limit = max(1, min(int(limit), MAX_LIST))
    offset = max(0, int(offset))
    where, params = [], []
    if not all_users:
        where.append("requested_by = %s")
        params.append(getattr(user, "id", None))
    if report_id:
        where.append("report_id = %s")
        params.append(report_id)
    if status:
        where.append("status = %s")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn.rollback()
    with _dict_cur(conn) as cur:
        cur.execute("SELECT count(*) AS n FROM report_runs" + clause, params)
        total = cur.fetchone()["n"]
        cur.execute("SELECT " + _RUN_COLUMNS + " FROM report_runs" + clause
                    + " ORDER BY requested_at DESC, id DESC LIMIT %s OFFSET %s",
                    params + [limit, offset])
        rows = cur.fetchall()
        summaries: Dict[int, List[Dict[str, Any]]] = {}
        if rows:
            # The page's dataset summaries in one query (no rows).
            cur.execute("SELECT run_id, dataset_key, semantics, row_count, truncated"
                        " FROM report_run_datasets WHERE run_id = ANY(%s)"
                        " ORDER BY run_id, position", ([r["id"] for r in rows],))
            for d in cur.fetchall():
                summaries.setdefault(d["run_id"], []).append(
                    {k: d[k] for k in ("dataset_key", "semantics", "row_count", "truncated")})
    conn.rollback()
    items = [dict(run_to_api(_reconcile(conn, r), registry=registry),
                  datasets=summaries.get(r["id"], []))
             for r in rows if _readable(r, user, registry)]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def dataset_rows(conn, run_id: int, dataset_key: str, *, user, limit: int = 100,
                 offset: int = 0, registry=REGISTRY) -> Dict[str, Any]:
    """A page of one dataset's stored rows, sliced in SQL."""
    _fetch(conn, run_id, user, registry)
    limit = max(1, min(int(limit), MAX_ROW_PAGE))
    offset = max(0, int(offset))
    with _dict_cur(conn) as cur:
        cur.execute(
            "SELECT d.dataset_key, d.semantics, d.row_limit, d.row_count, d.truncated, d.columns,"
            " COALESCE((SELECT jsonb_agg(e.value ORDER BY e.ordinality)"
            "           FROM jsonb_array_elements(d.rows) WITH ORDINALITY AS e(value, ordinality)"
            "           WHERE e.ordinality > %s AND e.ordinality <= %s), '[]'::jsonb) AS rows"
            " FROM report_run_datasets d WHERE d.run_id = %s AND d.dataset_key = %s",
            (offset, offset + limit, run_id, dataset_key))
        row = cur.fetchone()
    conn.rollback()
    if row is None:
        raise _not_found("dataset")
    return dict(row, limit=limit, offset=offset)
