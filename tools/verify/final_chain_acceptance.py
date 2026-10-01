"""Final End-to-End Acceptance: The Complete SYLTHARAE Intelligence Chain (§37).

Runs the ingestion, signal, search, analysis, monitoring, report, artifact, and
export paths against a live application and PostgreSQL database. A link passes
only when its persisted outcome is verified; HTTP success or object existence
alone is not acceptance evidence.
"""

import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5055"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SMOKE_ADMIN_PASSWORD", "")
USER = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("SMOKE_ADMIN_USER", "admin")
if not PASSWORD:
    raise SystemExit("Pass the administrator password as argument 2 or SMOKE_ADMIN_PASSWORD")

results = []


def check(link_num, name, ok, detail=""):
    results.append({"link": link_num, "name": name, "ok": bool(ok), "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{link_num:02d}] {status} - {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        print(f"     CRITICAL FAILURE AT LINK {link_num}: {detail}")
    return bool(ok)


def body(response):
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except (ValueError, requests.RequestException):
        return {}


def poll_job(session, job_id, timeout_seconds=60):
    """Wait for a real terminal job state; return (status, job payload)."""
    if not job_id:
        return "missing", {}
    deadline = time.monotonic() + timeout_seconds
    last = {}
    while time.monotonic() < deadline:
        response = session.get(f"{BASE_URL}/api/jobs/{job_id}", timeout=10)
        last = body(response).get("job", {})
        status = str(last.get("status", "")).lower()
        if status in {"completed", "completed_with_warnings", "failed", "cancelled", "canceled"}:
            return status, last
        time.sleep(0.25)
    return "timeout", last


def run_report(session, csrf, report_id, version, parameters):
    response = session.post(
        f"{BASE_URL}/api/reports/runs",
        json={"report_id": report_id, "version": version, "parameters": parameters},
        headers={"X-CSRFToken": csrf, "Referer": f"{BASE_URL}/reports"}, timeout=20,
    )
    payload = body(response)
    run = payload.get("run") or {}
    job = payload.get("job") or {}
    run_id = run.get("id") or job.get("entity_id")
    job_id = job.get("job_id")
    job_status = "not_created"
    if job_id:
        job_status, _ = poll_job(session, job_id)
    run_payload = {}
    run_status = "missing"
    if run_id:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            fetched = session.get(f"{BASE_URL}/api/reports/runs/{run_id}", timeout=10)
            run_payload = body(fetched).get("run", {})
            run_status = str(run_payload.get("status", "")).lower()
            if run_status in {"completed", "failed", "refused", "cancelled"}:
                break
            time.sleep(0.25)
    return {
        "http": response.status_code,
        "payload": payload,
        "run_id": run_id,
        "job_id": job_id,
        "job_status": job_status,
        "run_status": run_status,
        "run": run_payload,
    }


def main():
    os.environ.setdefault(
        "DB_HOST", str(Path(tempfile.gettempdir()) / "accept_run" / "pg")
    )
    os.environ.setdefault("DB_NAME", "syltharae_runtime")

    print("=== SYLTHARAE FINAL END-TO-END CHAIN ACCEPTANCE (§37) ===")
    print(f"Target: {BASE_URL}")
    session = requests.Session()

    # Link 00: health and an authenticated, server-established identity.
    r_health = session.get(f"{BASE_URL}/health", timeout=10)
    health_json = body(r_health)
    check(0, "HEALTH", r_health.status_code == 200 and health_json.get("status") == "healthy",
          f"status={r_health.status_code}, body={health_json}")

    r_login_page = session.get(f"{BASE_URL}/auth/login", timeout=10)
    match = (re.search(r'name="csrf_token" value="([^"]+)"', r_login_page.text)
             or re.search(r'content="([^"]+)" name="csrf-token"', r_login_page.text))
    csrf = match.group(1) if match else ""
    r_login = session.post(
        f"{BASE_URL}/auth/login",
        data={"username": USER, "password": PASSWORD, "csrf_token": csrf},
        headers={"Referer": f"{BASE_URL}/auth/login"}, timeout=10,
    )
    r_me = session.get(f"{BASE_URL}/auth/me", timeout=10)
    identity = body(r_me).get("user") or {}
    authenticated = (r_login.status_code == 200 and body(r_me).get("authenticated") is True
                     and identity.get("username") == USER and identity.get("id") is not None)
    check(0, "AUTHENTICATE", authenticated,
          f"status={r_login.status_code}, user={identity.get('username')}, role={identity.get('role')}")
    user_id = identity.get("id")
    role = identity.get("role")

    r_home = session.get(f"{BASE_URL}/", timeout=10)
    match = (re.search(r'<meta name="csrf-token" content="([^"]+)"', r_home.text)
             or re.search(r'name="csrf_token" value="([^"]+)"', r_home.text))
    if match:
        csrf = match.group(1)

    # Links 01-02: ingest real, hash-identified files through the production
    # ContentDBService. A second document supplies an actual reference corpus
    # for the data-backed keyness analysis; it does not match the target query.
    from Api.utils.utils import get_connection
    from database.services.contents_db_service import ContentDBService

    run_token = uuid.uuid4().hex[:10]
    event_date = datetime.date.today() + datetime.timedelta(days=7)
    date_surface = event_date.strftime("%d %B %Y")
    doc_text = (
        f"The bilateral summit in Cairo will convene on {date_surface}.\n"
        "المحادثات في القاهرة ستعقد في 15 نوفمبر 2026.\n"
        f"All diplomatic delegates from Zagreb and Amsterdam submit terms for record {run_token}."
    )
    doc_bytes = doc_text.encode("utf-8")
    doc_sha = hashlib.sha256(doc_bytes).hexdigest()
    file_name = f"bilateral_summit_{run_token}.txt"
    source_dir = Path(tempfile.mkdtemp(prefix="syltharae-e2e-files-"))
    file_path = source_dir / file_name
    file_path.write_bytes(doc_bytes)

    reference_text = (
        f"Reference material {run_token}: peaceful delegation discusses regional protocol.\n"
        "A separate diplomatic record contains common vocabulary for comparison."
    )
    reference_bytes = reference_text.encode("utf-8")
    reference_name = f"comparison_{run_token}.txt"
    reference_path = source_dir / reference_name
    reference_path.write_bytes(reference_bytes)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM sources ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row:
                source_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO sources (name, job, importance, country, date_creation) "
                    "VALUES ('E2E Source', 'Acceptance', 0.8, 'US', CURRENT_DATE) RETURNING id"
                )
                source_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM sides ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row:
                side_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO sides (name, importance, date_creation) "
                    "VALUES ('E2E Side', 0.8, CURRENT_DATE) RETURNING id"
                )
                side_id = cur.fetchone()[0]
        conn.commit()

    service = ContentDBService()
    stored = service.process_full_document(
        hash_value=doc_sha, source_id=source_id, side_id=side_id,
        file_name=file_name, file_path=str(file_path), file_size=len(doc_bytes),
        file_type="txt", file_status="Read", file_date=datetime.date.today(),
        content_words=["bilateral", "summit", "cairo", "zagreb", "diplomatic", run_token],
        raw_text=doc_text, attempts=1,
    )
    hash_id = stored.get("hash_id")
    path_id = stored.get("path_id")
    ingest_ok = bool(hash_id and stored.get("success") is True and path_id)
    check(1, "INGEST", ingest_ok,
          f"hash_id={hash_id}, path_id={path_id}, bytes={len(doc_bytes)}, sha256={doc_sha}")
    check(2, "PROCESS", stored.get("success") is True and path_id is not None,
          f"success={stored.get('success')}, path_id={path_id}, context_id={stored.get('context_id')}")

    reference = service.process_full_document(
        hash_value=hashlib.sha256(reference_bytes).hexdigest(),
        source_id=source_id, side_id=side_id,
        file_name=reference_name, file_path=str(reference_path),
        file_size=len(reference_bytes), file_type="txt", file_status="Read",
        file_date=datetime.date.today(),
        content_words=["peaceful", "delegation", "regional", "protocol", "diplomatic", "common"],
        raw_text=reference_text, attempts=1,
    )
    reference_ok = reference.get("success") is True and reference.get("hash_id") is not None
    if not reference_ok:
        print(f"Reference corpus ingest failed: {reference}")

    # Link 03: detector outputs must exist. Link 04 checks persisted signal
    # rows, including complete source offsets and evidence provenance.
    signal_result = stored.get("signals") or {}
    temporal_count = signal_result.get("signals", 0)
    place_result = stored.get("place_signals") or {}
    place_count = place_result.get("signals", 0)
    check(3, "DETECT", temporal_count > 0 and place_count > 0,
          f"temporal_signals={temporal_count}, place_signals={place_count}")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), detector FROM content_signals WHERE hash_id = %s "
                "GROUP BY detector ORDER BY detector", (hash_id,)
            )
            signal_groups = cur.fetchall()
            cur.execute(
                "SELECT count(*) FROM content_signal_places csp "
                "JOIN content_signals cs ON cs.id = csp.signal_id WHERE cs.hash_id = %s",
                (hash_id,),
            )
            place_rows = cur.fetchone()[0]
            cur.execute(
                "SELECT method, confidence, confidence_basis, evidence_sentence, "
                "char_start, char_end, sentence_start, sentence_end, date_from, language "
                "FROM content_signals WHERE hash_id = %s AND signal_type = 'date_reference' "
                "ORDER BY char_start LIMIT 1", (hash_id,),
            )
            provenance = cur.fetchone()
    provenance_ok = False
    if provenance:
        (method, confidence, confidence_basis, sentence, char_start, char_end,
         sentence_start, sentence_end, signal_date, language) = provenance
        provenance_ok = bool(
            method and confidence in {"high", "medium", "low"} and confidence_basis
            and sentence and sentence_start is not None and sentence_end is not None
            and sentence_start <= char_start < char_end <= sentence_end
            and len(sentence) == sentence_end - sentence_start
            and doc_text[sentence_start:sentence_end] == sentence
            and signal_date is not None and language
        )
    signal_storage_ok = bool(signal_groups and place_rows > 0 and provenance_ok)
    check(4, "STORE SIGNALS", signal_storage_ok,
          f"groups={signal_groups}, place_links={place_rows}, provenance={provenance_ok}")

    # Link 05: compile the same authorized criteria shape used by search and
    # reporting, with the authenticated user's real identity and role.
    from core.criteria.compiler import AccessScope, compile_criteria
    from core.criteria.model import Criteria

    criteria = Criteria(text="bilateral summit")
    scope = AccessScope.unrestricted(user_id=int(user_id or 0), role=role or "")
    compiled = compile_criteria(criteria, scope)
    criteria_fingerprint = criteria.fingerprint()
    criteria_ok = bool(compiled.where_sql and criteria_fingerprint and user_id)
    check(5, "COMPILE CRITERIA", criteria_ok,
          f"fingerprint={criteria_fingerprint[:12]}, params={len(compiled.params)}, user_id={user_id}")

    # Link 06: successful HTTP is not enough; the actual ingested path must be
    # present in the authenticated search response.
    r_search = session.get(
        f"{BASE_URL}/api/search", params={"query": "bilateral summit"}, timeout=20
    )
    search_json = body(r_search)
    results_list = search_json.get("results") or search_json.get("items") or []
    search_serialized = json.dumps(search_json, ensure_ascii=False, default=str)
    search_hit = r_search.status_code == 200 and file_name in search_serialized
    check(6, "SEARCH", search_hit,
          f"http={r_search.status_code}, hits={len(results_list)}, target_path={str(path_id) in search_serialized if path_id else False}")

    # Link 07: invoke report analysis v2 over the freshly persisted target and
    # reference corpus. Require measured counts and returned terms, not a local
    # illustrative calculation.
    analysis_result = run_report(
        session, csrf, "term_keyness", 2,
        {"criteria": {"text": "bilateral summit"}, "direction": "over"},
    )
    analysis_run = analysis_result["run"]
    analyses = analysis_run.get("analyses") or []
    analysis = next((item for item in analyses
                     if item.get("analysis_key") == "term_keyness@2"), {})
    measures = analysis.get("measures") or {}
    analysis_ok = bool(
        reference_ok and analysis_result["http"] in {200, 201, 202}
        and analysis_result["job_status"] == "completed"
        and analysis_result["run_status"] == "completed"
        and analysis.get("state") == "measured"
        and measures.get("target_contents", 0) >= 1
        and measures.get("reference_contents", 0) >= 1
        and measures.get("target_tokens", 0) > 0
        and measures.get("reference_tokens", 0) > 0
        and analysis.get("rows")
    )
    check(7, "ANALYZE", analysis_ok,
          f"run={analysis_result['run_id']}, job={analysis_result['job_status']}, "
          f"status={analysis_result['run_status']}, state={analysis.get('state')}, "
          f"target={measures.get('target_contents')}/{measures.get('target_tokens')} tokens, "
          f"reference={measures.get('reference_contents')}/{measures.get('reference_tokens')} tokens, "
          f"terms={len(analysis.get('rows') or [])}")

    # Links 08-10: evaluate a rule whose date signal is deliberately seven
    # days ahead, within its 30-day window. notify_existing is explicit so the
    # first evaluation can notify about this already-ingested acceptance item.
    rule_name = f"e2e_summit_monitor_{run_token}"
    r_rule = session.post(
        f"{BASE_URL}/api/rules",
        json={
            "name": rule_name,
            "definition": {
                "criteria": {"text": "bilateral summit"},
                "signals": {"signal_types": ["date_reference"]},
                "min_confidence": "medium",
                "event_window_days": {"from": 0, "to": 30},
                "unit": "content",
                "notify_existing": True,
            },
        },
        headers={"X-CSRFToken": csrf, "Referer": f"{BASE_URL}/monitoring"}, timeout=20,
    )
    rule_id = body(r_rule).get("rule", {}).get("id")
    rule_ok = r_rule.status_code == 201 and rule_id is not None
    check(8, "MONITOR", rule_ok, f"rule_id={rule_id}, http={r_rule.status_code}")

    from services.monitoring.rule_engine import run_rule_evaluation

    evaluation = None
    evaluation_outcome = {}
    evaluation_stats = {}
    if rule_id:
        evaluation = run_rule_evaluation(get_connection, rule_ids=[int(rule_id)], trigger="manual")
        evaluation_stats = getattr(evaluation, "stats", {}) or {}
        evaluations = getattr(evaluation, "evaluations", []) or []
        evaluation_outcome = evaluations[0] if evaluations else {}
    evaluation_ok = bool(
        evaluation is not None and not getattr(evaluation, "errors", [])
        and not getattr(evaluation, "cancelled", True)
        and evaluation_stats.get("by_status", {}).get("completed") == 1
        and evaluation_outcome.get("status") == "completed"
        and (evaluation_outcome.get("counts") or {}).get("new_subjects", 0) >= 1
    )
    check(9, "EVALUATE RULES", evaluation_ok,
          f"outcome={evaluation_outcome}, stats={evaluation_stats}")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, recipient_user_id, rule_id, rule_evaluation_id, file_id, metadata "
                "FROM alerts WHERE rule_id = %s ORDER BY id", (rule_id or -1,)
            )
            alerts = cur.fetchall()
    evaluation_id = evaluation_outcome.get("evaluation_id")
    linked_alerts = []
    for alert in alerts:
        metadata = alert[5] if isinstance(alert[5], dict) else {}
        subjects = metadata.get("subjects") or []
        refers_to_target = any(
            subject.get("hash_id") == hash_id
            and subject.get("subject_key") == f"content:{hash_id}"
            and subject.get("evidence_sentence")
            for subject in subjects if isinstance(subject, dict)
        )
        if (alert[1] == user_id and alert[2] == rule_id
                and alert[3] == evaluation_id
                and metadata.get("evaluation_id") == evaluation_id
                and refers_to_target):
            linked_alerts.append(alert[0])
    alert_ok = bool(
        evaluation_ok and linked_alerts
        and evaluation_stats.get("notifications", 0) >= 1
        and (evaluation_outcome.get("counts") or {}).get("notifications", 0) >= 1
    )
    check(10, "NOTIFY", alert_ok,
          f"persisted_alert_ids={linked_alerts}, alerts={alerts}, "
          f"notifications={evaluation_stats.get('notifications', 0)}, recipient_user_id={user_id}, "
          f"rule_id={rule_id}, evaluation_id={evaluation_id}, target_hash_id={hash_id}")

    # Link 11: run a real search-results report, wait for both job and report
    # state, then verify the stored dataset contains this target path.
    report_result = run_report(
        session, csrf, "search_results", 1,
        {"criteria": {"text": "bilateral summit"}},
    )
    report_id = report_result["run_id"]
    dataset_payload = {}
    dataset_has_target = False
    if report_id and report_result["run_status"] == "completed":
        response = session.get(
            f"{BASE_URL}/api/reports/runs/{report_id}/datasets/search_results.matches@1",
            timeout=15,
        )
        dataset_payload = body(response)
        rows = dataset_payload.get("rows") or []
        dataset_has_target = response.status_code == 200 and any(
            row.get("path_id") == path_id for row in rows if isinstance(row, dict)
        )
    report_ok = bool(
        report_result["http"] in {200, 201, 202}
        and report_result["job_status"] == "completed"
        and report_result["run_status"] == "completed"
        and dataset_has_target
    )
    check(11, "GENERATE REPORT", report_ok,
          f"run_id={report_id}, job={report_result['job_status']}, "
          f"status={report_result['run_status']}, dataset_rows={dataset_payload.get('row_count')}, "
          f"target_path_present={dataset_has_target}")

    # Link 12: create a stored HTML artifact only from the completed run and
    # verify its asynchronous job, manifest digests, and persisted metadata.
    artifact_id = None
    artifact_job = None
    artifact_job_status = "not_created"
    artifact_meta = {}
    artifact_verified = False
    artifact_metadata_http = 0
    artifact_verify_http = 0
    if report_id and report_ok:
        artifact_response = session.post(
            f"{BASE_URL}/api/reports/runs/{report_id}/artifacts",
            json={"format": "html", "language": "en"},
            headers={"X-CSRFToken": csrf, "Referer": f"{BASE_URL}/reports"}, timeout=20,
        )
        artifact_payload = body(artifact_response)
        artifact_job = (artifact_payload.get("job") or {}).get("job_id")
        artifact_job_status = "completed" if artifact_payload.get("existing") else "not_created"
        if artifact_job:
            artifact_job_status, _ = poll_job(session, artifact_job)
        listing = session.get(
            f"{BASE_URL}/api/reports/runs/{report_id}/artifacts", timeout=15
        )
        items = body(listing).get("items") or []
        if items:
            artifact_id = items[0].get("id")
        if artifact_id:
            metadata_response = session.get(
                f"{BASE_URL}/api/reports/artifacts/{artifact_id}", timeout=15
            )
            artifact_metadata_http = metadata_response.status_code
            artifact_meta = body(metadata_response).get("artifact") or {}
            verify_response = session.get(
                f"{BASE_URL}/api/reports/artifacts/{artifact_id}/verify", timeout=15
            )
            artifact_verify_http = verify_response.status_code
            artifact_verified = body(verify_response).get("ok") is True
        artifact_create_ok = artifact_response.status_code in {200, 201, 202}
        artifact_job_ok = artifact_job_status == "completed"
        artifact_ok = bool(
            artifact_create_ok and listing.status_code == 200 and artifact_id
            and artifact_metadata_http == 200 and artifact_verify_http == 200
            and artifact_meta.get("id") == artifact_id
            and artifact_meta.get("run_id") == report_id
            and artifact_meta.get("format") == "html"
            and artifact_meta.get("byte_size", 0) > 0
            and artifact_meta.get("sha256") and artifact_meta.get("manifest_sha256")
            and artifact_job_ok and artifact_verified
        )
    else:
        artifact_ok = False
    check(12, "CREATE ARTIFACT", artifact_ok,
          f"artifact_id={artifact_id}, job={artifact_job}, job_status={artifact_job_status}, "
          f"bytes={artifact_meta.get('byte_size')}, verified={artifact_verified}")

    # Link 13: fetch the bytes and require their digest to match the stored
    # metadata and the server's checksum header.
    export_ok = False
    audit_id = None
    download_response = None
    download_sha = None
    if artifact_id and artifact_ok:
        download_response = session.get(
            f"{BASE_URL}/api/reports/artifacts/{artifact_id}/download", timeout=20
        )
        download_sha = hashlib.sha256(download_response.content).hexdigest()
        server_sha = download_response.headers.get("X-Artifact-SHA256")
        audit_id = download_response.headers.get("X-Disclosure-Audit-Id")
        export_ok = bool(
            download_response.status_code == 200 and download_response.content
            and download_sha == server_sha == artifact_meta.get("sha256")
        )
    check(13, "EXPORT", export_ok,
          f"bytes={len(download_response.content) if download_response else 0}, "
          f"response_sha256={download_sha}, artifact_sha256={artifact_meta.get('sha256')}")

    # Link 14: independently query the persisted audit entry referenced by
    # the download response. Verify actor, export action, artifact/run linkage,
    # and the digest that describes the actual disclosed bytes.
    audit_row = None
    if audit_id and str(audit_id).isdigit():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT action, user_id, username, resource, detail FROM audit_log WHERE id = %s",
                    (int(audit_id),),
                )
                audit_row = cur.fetchone()
    audit_ok = False
    if audit_row:
        action, audit_user_id, actor, resource, detail = audit_row
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        audit_artifact = (detail or {}).get("artifact") or {}
        audit_ok = bool(
            action == "DATA_EXPORTED" and audit_user_id == user_id and actor == USER
            and f"report_run:{report_id}" in (resource or "")
            and (detail or {}).get("artifact_id") == artifact_id
            and (detail or {}).get("report_run_id") == report_id
            and audit_artifact.get("sha256") == download_sha
            and audit_artifact.get("bytes") == len(download_response.content)
        )
    check(14, "AUDIT", audit_ok,
          f"audit_id={audit_id}, action={audit_row[0] if audit_row else None}, "
          f"actor={audit_row[2] if audit_row else None}, artifact={artifact_id}, "
          f"report_run={report_id}, digest_match={download_sha == artifact_meta.get('sha256')}")

    print("\n" + "=" * 60)
    passed = sum(1 for result in results if result["ok"])
    total = len(results)
    print(f"CHAIN ACCEPTANCE RESULT: {passed}/{total} LINKS VERIFIED")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
