"""Report artifacts and their manifests (step 15; table of m0023).

An artifact is one rendering (``core/reporting/render.py``) of one
**completed** run, stored with a provenance manifest:

1. **Request** (:func:`request_artifact`, request thread). The run must be
   readable by the caller (the same rule as reading the run: requester or
   administrator, while their role is still allowed by the report) and
   completed; the format/dataset must be valid for the run. Creating an
   artifact is a write (analyst/admin, SEC-02). If the same rendering (run,
   format, dataset, renderer version) already exists it is returned - the
   renderers are deterministic, so a second copy would be byte-identical.
   Otherwise a ``report_artifact`` JobManager job is created.
2. **Render** (:func:`create_artifact`, in the job). The creator is re-read
   from ``users`` (deleted / inactive / role no longer allowed -> refused,
   nothing rendered); the run document is built from the stored run and its
   stored datasets only; the bytes are rendered, measured (size, SHA-256) and
   bounded (``MAX_ARTIFACT_BYTES``); the manifest is built and digested; the
   row is inserted once (``ON CONFLICT DO NOTHING`` on the rendering key: a
   concurrent identical request yields the one existing artifact).
3. **Read / download / verify.** Visible exactly when its run is visible.
   Before bytes leave, their SHA-256 is recomputed and compared with the
   recorded digest (and PostgreSQL's own CHECK); a mismatch refuses the
   download. Downloads carry the digest and the manifest digest in headers
   and go through the fail-closed ``DATA_EXPORTED`` hook with the report,
   run, criteria and query fingerprints, format, scope, row count and
   truncation.

The manifest answers: which definition and version (with its pinned
fingerprint), which parameters and criteria (with fingerprints), which
database snapshot and isolation, which datasets (with query fingerprints,
semantics, limits, counts, truncation, columns), who requested the run and
who generated the artifact, when, with which runner and renderer, and the
artifact's size and SHA-256. ``manifest_sha256`` is the SHA-256 of the
manifest's canonical JSON (``render.canonical_json``); the downloaded
manifest file *is* those bytes, so ``sha256sum`` on it reproduces the value
and ``tools/verify/verify_artifact.py`` checks a downloaded pair offline.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import psycopg2
import psycopg2.extras

from core.reporting import REGISTRY
from core.reporting import render as renderers
from core.reporting.registry import ReportNotFound
from services.reporting import runs

logger = logging.getLogger(__name__)

#: 2: records the run's analyses (key, fingerprint, state, template set).
MANIFEST_VERSION = "report-manifest/3"   # 3: the rendering states its language
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

_ARTIFACT_COLUMNS = (
    "id, run_id, format, dataset_key, renderer_version, language, filename, media_type,"
    " byte_size, sha256, manifest_sha256, created_by, creator_username, creator_role,"
    " job_id, created_at")


class ArtifactError(runs.ReportRunError):
    pass


def _dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def artifact_to_api(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: row[k] for k in ("id", "run_id", "format", "dataset_key", "renderer_version",
                               "language", "filename", "media_type", "byte_size", "sha256",
                               "manifest_sha256", "creator_username", "creator_role",
                               "job_id")}
    out["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return out


def manifest_digest(manifest: Dict[str, Any]) -> str:
    return hashlib.sha256(renderers.canonical_json(manifest)).hexdigest()


# ---------------------------------------------------------------------------
# The run document (stored data only)
# ---------------------------------------------------------------------------

def run_document(conn, run_row: Dict[str, Any], registry=REGISTRY) -> Dict[str, Any]:
    """The run's provenance and stored rows, as rendered. No live query."""
    api = runs.run_to_api(run_row, registry=registry)
    try:
        definition = registry.report(run_row["report_id"], run_row["report_version"])
        param = runs._criteria_parameter(definition)
        report = {"id": definition.report_id, "version": definition.version,
                  "title": definition.title, "unit": definition.unit,
                  "description": definition.description,
                  "criteria_parameter": param.name if param else None}
    except ReportNotFound:
        report = {"id": run_row["report_id"], "version": run_row["report_version"],
                  "title": None, "unit": None, "description": None,
                  "criteria_parameter": None}
    with _dict_cur(conn) as cur:
        cur.execute("SELECT dataset_key, dataset_fingerprint, query_fingerprint, semantics,"
                    " row_limit, row_count, truncated, columns, rows, baseline"
                    " FROM report_run_datasets WHERE run_id = %s ORDER BY position",
                    (run_row["id"],))
        datasets = [dict(r) for r in cur.fetchall()]
    conn.rollback()
    analyses = []
    for a in runs.run_analyses(conn, run_row["id"]):
        try:
            title = registry.analysis(a["analysis_key"]).title
        except ReportNotFound:
            title = None
        analyses.append(dict({k: a[k] for k in (
            "analysis_key", "analysis_fingerprint", "kind", "state", "reason", "inputs",
            "measures", "rows", "narrative", "template_set", "template_version")},
            title=title))
    run_fields = {k: api[k] for k in (
        "id", "report_id", "report_version", "report_key", "definition_fingerprint",
        "parameters", "parameters_fingerprint", "criteria_fingerprint", "saved_search_id",
        "requester_username", "requester_role", "job_id", "snapshot", "snapshot_at",
        "isolation_level", "generator_version", "requested_at", "started_at", "finished_at")}
    return {"report": report, "run": run_fields, "datasets": datasets, "analyses": analyses}


def build_manifest(document: Dict[str, Any], rendering, *, content_sha256: str,
                   byte_size: int, creator: Dict[str, Any], generated_at: str,
                   job_id: Optional[str]) -> Dict[str, Any]:
    run = document["run"]
    return {
        "manifest_version": MANIFEST_VERSION,
        "artifact": {
            "format": rendering.format, "dataset_key": rendering.dataset_key,
            "filename": rendering.filename, "media_type": rendering.media_type,
            "renderer_version": rendering.renderer_version,
            "language": rendering.notes.get("language", "en"),
            "bytes": byte_size, "sha256": content_sha256, "notes": rendering.notes,
        },
        "report": {"id": run["report_id"], "version": run["report_version"],
                   "key": run["report_key"], "title": document["report"]["title"],
                   "unit": document["report"]["unit"],
                   "definition_fingerprint": run["definition_fingerprint"]},
        "run": {"id": run["id"], "parameters": run["parameters"],
                "parameters_fingerprint": run["parameters_fingerprint"],
                "criteria": ((run["parameters"] or {}).get(document["report"]["criteria_parameter"])
                             if document["report"]["criteria_parameter"] else None),
                "criteria_fingerprint": run["criteria_fingerprint"],
                "saved_search_id": run["saved_search_id"],
                "requested_by": {"username": run["requester_username"],
                                 "role": run["requester_role"]},
                "requested_at": run["requested_at"], "started_at": run["started_at"],
                "finished_at": run["finished_at"], "job_id": run["job_id"],
                "runner_version": run["generator_version"]},
        "snapshot": {"id": run["snapshot"], "taken_at": run["snapshot_at"],
                     "isolation": run["isolation_level"]},
        "datasets": [dict({k: ds[k] for k in ("dataset_key", "dataset_fingerprint",
                                              "query_fingerprint", "semantics", "row_limit",
                                              "row_count", "truncated", "columns")},
                          **({"baseline": ds["baseline"]}
                             if ds.get("baseline") is not None else {}))
                     for ds in document["datasets"]],
        "analyses": [dict({k: a[k] for k in ("analysis_key", "analysis_fingerprint", "kind",
                                              "state", "reason", "inputs", "template_set",
                                              "template_version")},
                          template_fingerprint=a["narrative"].get("template_fingerprint"),
                          included=rendering.format in renderers.WITH_ANALYSES)
                     for a in document.get("analyses", ())],
        "row_count": sum(ds["row_count"] for ds in document["datasets"]
                         if rendering.dataset_key in (None, ds["dataset_key"])),
        "truncated": any(ds["truncated"] for ds in document["datasets"]
                         if rendering.dataset_key in (None, ds["dataset_key"])),
        "generated": {"at": generated_at, "by": creator, "job_id": job_id},
    }


# ---------------------------------------------------------------------------
# Request / create
# ---------------------------------------------------------------------------

def _completed_run(conn, run_id: int, user, registry) -> Dict[str, Any]:
    row = runs._fetch(conn, run_id, user, registry)
    if row["status"] != "completed":
        raise ArtifactError("RUN_NOT_COMPLETED",
                            f"only a completed run has results to render (this run is "
                            f"{row['status']})", 409)
    return row


def _dataset_keys(conn, run_id: int) -> List[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT dataset_key FROM report_run_datasets WHERE run_id = %s"
                    " ORDER BY position", (run_id,))
        keys = [r[0] for r in cur.fetchall()]
    conn.rollback()
    return keys


def _existing(conn, run_id, fmt, dataset_key,
              language: str = "en") -> Optional[Dict[str, Any]]:
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + _ARTIFACT_COLUMNS + " FROM report_artifacts"  # nosec B608 # _ARTIFACT_COLUMNS is a constant, optional columns are fixed literals; values are bound parameters
                    " WHERE run_id = %s AND format = %s AND COALESCE(dataset_key, '') = %s"
                    " AND renderer_version = %s AND language = %s",
                    (run_id, fmt, dataset_key or "", renderers.RENDERERS[fmt], language))
        row = cur.fetchone()
    conn.rollback()
    return row


def request_artifact(conn, run_id: int, *, user, fmt: Any, dataset_key: Any = None,
                     language: Any = None, registry=REGISTRY) -> Dict[str, Any]:
    """Validate an artifact request. Returns ``{"existing": artifact}`` or
    ``{"create": {"run_id", "format", "dataset_key", "language"}}``. The
    language is part of the rendering: explicit (the requester's choice,
    defaulting to their interface language), validated against the shipped
    catalogs, and recorded on the artifact and in its manifest."""
    from core.reporting.i18n import available_languages

    if getattr(user, "role", None) not in runs.RUN_ROLES:
        raise ArtifactError("FORBIDDEN", "viewers can read reports but not create files", 403)
    _completed_run(conn, run_id, user, registry)
    try:
        fmt, dataset_key = renderers.check_request(_dataset_keys(conn, run_id), fmt,
                                                   dataset_key)
    except renderers.RenderError as exc:
        raise ArtifactError("VALIDATION_FAILED", str(exc)) from None
    lang = language if language not in (None, "") else "en"
    if lang not in available_languages():
        raise ArtifactError(
            "VALIDATION_FAILED",
            f"language must be one of: {', '.join(sorted(available_languages()))}")
    row = _existing(conn, run_id, fmt, dataset_key, lang)
    if row is not None:
        return {"existing": artifact_to_api(row)}
    return {"create": {"run_id": run_id, "format": fmt, "dataset_key": dataset_key,
                       "language": lang}}


def create_artifact(conn, *, run_id: int, fmt: str, dataset_key: Optional[str],
                    creator_id: int, language: str = "en",
                    job_id: Optional[str] = None,
                    registry=REGISTRY) -> Dict[str, Any]:
    """Render and store (job side). Returns ``{"status": "created" | "existing" |
    "refused" | "failed", ...}``; never raises for a stated refusal."""
    from types import SimpleNamespace

    conn.rollback()
    with _dict_cur(conn) as cur:
        cur.execute("SELECT id, username, role, is_active FROM users WHERE id = %s",
                    (creator_id,))
        creator = cur.fetchone()
    conn.rollback()
    if creator is None or not creator["is_active"] or creator["role"] not in runs.RUN_ROLES:
        return {"status": "refused", "reason": "creator_not_permitted"}
    user = SimpleNamespace(id=creator["id"], username=creator["username"],
                           role=creator["role"],
                           has_role=lambda r, _role=creator["role"]: _role == r)
    try:
        run_row = _completed_run(conn, run_id, user, registry)
    except runs.ReportRunError as exc:
        return {"status": "refused", "reason": exc.code.lower(), "message": exc.message}
    existing = _existing(conn, run_id, fmt, dataset_key, language)
    if existing is not None:
        return {"status": "existing", "artifact": artifact_to_api(existing)}
    document = run_document(conn, run_row, registry)
    conn.rollback()
    try:
        rendering = renderers.render(document, fmt, dataset_key, language)
    except renderers.RenderError as exc:
        return {"status": "failed", "error": str(exc)}
    size = len(rendering.content)
    if size > MAX_ARTIFACT_BYTES:
        return {"status": "failed",
                "error": f"the {fmt} rendering is {size} bytes, above the "
                         f"{MAX_ARTIFACT_BYTES}-byte artifact limit; nothing was stored"}
    digest = hashlib.sha256(rendering.content).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC',"
                    " 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')")
        generated_at = cur.fetchone()[0]
    conn.rollback()
    manifest = build_manifest(document, rendering, content_sha256=digest, byte_size=size,
                              creator={"id": creator["id"], "username": creator["username"],
                                       "role": creator["role"]},
                              generated_at=generated_at, job_id=job_id)
    with _dict_cur(conn) as cur:
        cur.execute(
            "INSERT INTO report_artifacts (run_id, format, dataset_key, renderer_version,"  # nosec B608 # _ARTIFACT_COLUMNS is a constant, optional columns are fixed literals; values are bound parameters
            " language, filename, media_type, byte_size, sha256, content, manifest,"
            " manifest_sha256, created_by, creator_username, creator_role, job_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING RETURNING " + _ARTIFACT_COLUMNS,
            (run_id, fmt, dataset_key, rendering.renderer_version, language,
             rendering.filename, rendering.media_type, size, digest,
             psycopg2.Binary(rendering.content),
             psycopg2.extras.Json(manifest), manifest_digest(manifest), creator["id"],
             creator["username"], creator["role"], job_id))
        row = cur.fetchone()
    conn.commit()
    if row is None:      # a concurrent identical request stored it first
        existing = _existing(conn, run_id, fmt, dataset_key, language)
        return {"status": "existing", "artifact": artifact_to_api(existing)}
    return {"status": "created", "artifact": artifact_to_api(row)}


@dataclass
class ArtifactJobResult:
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False


def run_artifact_job(get_connection: Callable, *, run_id: int, fmt: str,
                     dataset_key: Optional[str], creator_id: int,
                     language: str = "en", job_id: Optional[str] = None,
                     progress_cb: Optional[Callable] = None) -> ArtifactJobResult:
    """JobManager entry point (job type ``report_artifact``)."""
    result = ArtifactJobResult()
    with get_connection() as conn:
        out = create_artifact(conn, run_id=run_id, fmt=fmt, dataset_key=dataset_key,
                              creator_id=creator_id, language=language, job_id=job_id)
    result.stats = {"run_id": run_id, "format": fmt, "dataset_key": dataset_key,
                    "language": language, "status": out["status"]}
    if out.get("artifact"):
        result.stats["artifact_id"] = out["artifact"]["id"]
        result.stats["sha256"] = out["artifact"]["sha256"]
    if out["status"] == "refused":
        result.errors.append(f"report artifact for run {run_id}: refused ({out['reason']})")
    elif out["status"] == "failed":
        result.errors.append(f"report artifact for run {run_id}: {out['error']}")
    if progress_cb:
        progress_cb({"percent": 100, "current_phase": f"Report file {fmt}",
                     "files_processed": 1, "files_total": 1})
    return result


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_artifacts(conn, run_id: int, *, user, registry=REGISTRY) -> List[Dict[str, Any]]:
    runs._fetch(conn, run_id, user, registry)
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + _ARTIFACT_COLUMNS + " FROM report_artifacts"  # nosec B608 # _ARTIFACT_COLUMNS is a constant, optional columns are fixed literals; values are bound parameters
                    " WHERE run_id = %s ORDER BY id", (run_id,))
        rows = cur.fetchall()
    conn.rollback()
    return [artifact_to_api(r) for r in rows]


def _artifact_row(conn, artifact_id: int, user, registry, *, content: bool):
    columns = _ARTIFACT_COLUMNS + ", manifest" + (", content" if content else "")
    conn.rollback()
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + columns + " FROM report_artifacts WHERE id = %s",  # nosec B608 # _ARTIFACT_COLUMNS is a constant, optional columns are fixed literals; values are bound parameters
                    (artifact_id,))
        row = cur.fetchone()
    conn.rollback()
    if row is None:
        raise runs._not_found("report artifact")
    runs._fetch(conn, row["run_id"], user, registry)   # same visibility as the run
    return row


def get_artifact(conn, artifact_id: int, *, user, registry=REGISTRY) -> Dict[str, Any]:
    row = _artifact_row(conn, artifact_id, user, registry, content=False)
    return dict(artifact_to_api(row), manifest=row["manifest"])


def verify(row: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute both digests from what is stored."""
    content = bytes(row["content"]) if "content" in row else None
    measured = hashlib.sha256(content).hexdigest() if content is not None else None
    manifest_measured = manifest_digest(row["manifest"])
    checks = {
        "content_sha256": measured == row["sha256"],
        "content_size": content is not None and len(content) == row["byte_size"],
        "manifest_sha256": manifest_measured == row["manifest_sha256"],
        "manifest_names_content": (row["manifest"].get("artifact", {}).get("sha256")
                                   == row["sha256"]),
    }
    return {"ok": all(checks.values()), "checks": checks, "sha256": measured,
            "manifest_sha256": manifest_measured}


def verify_artifact(conn, artifact_id: int, *, user, registry=REGISTRY) -> Dict[str, Any]:
    row = _artifact_row(conn, artifact_id, user, registry, content=True)
    return dict(verify(row), artifact_id=artifact_id,
                recorded={"sha256": row["sha256"], "manifest_sha256": row["manifest_sha256"]})


def artifact_for_download(conn, artifact_id: int, *, user,
                          registry=REGISTRY) -> Dict[str, Any]:
    """The row with its bytes, only if both digests verify."""
    row = _artifact_row(conn, artifact_id, user, registry, content=True)
    outcome = verify(row)
    if not outcome["ok"]:
        logger.error("report artifact %s failed verification: %s", artifact_id,
                     outcome["checks"])
        raise ArtifactError("INTEGRITY_FAILED",
                            "the stored file does not match its recorded checksum; it was "
                            "not sent", 500)
    row = dict(row)
    row["content"] = bytes(row["content"])
    return row
