"""Runtime check of report runs against a running server (step 14).

    python tools/verify/runtime_check_reports.py <base-url> <state-dir>

Requires a server started against the database from ``runtime_provision.py``
(same ``<state-dir>``). HTTP only - no application imports: what is verified
is the running process, its background job worker included (the run is a
``report_run`` job, answered 202 and polled). Responses are saved under
``<state-dir>/evidence/reports_*.json``, the rendered /reports page under
``evidence/reports_page.html`` and the owner's session for
``reports_page_runtime.mjs``.

Central checks: a run executes in the background and records one snapshot,
its fingerprints and the generator; the exact count equals the listing when
nothing was cut; two identical runs agree on every fingerprint and on the
rows; runs are invisible to other users and readable by administrators;
viewers are read-only; the submission is in the audit log (read back through
PostgreSQL by the caller, see EXECUTION_STATUS.md).
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_check_signals import Client, check, failures  # noqa: E402

TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}


def main(base, state_dir):
    state = Path(state_dir)
    info = json.loads((state / "env.json").read_text())
    ids = info["ids"]
    evidence = state / "evidence"
    evidence.mkdir(exist_ok=True)

    def save(name, body):
        (evidence / f"reports_{name}.json").write_text(
            json.dumps(body, indent=2, ensure_ascii=False))

    anon = Client(base)
    status, _, _ = anon.request("GET", "/api/reports/definitions")
    check(status == 401, f"anonymous GET /api/reports/definitions -> {status} (401 expected)")

    clients = {}
    for key in ("analyst", "analyst2", "admin", "viewer"):
        c = Client(base)
        username, password = info["users"][key]
        status, _, _ = c.login(username, password)
        check(status == 200 and c.cookie, f"{key} login -> {status}")
        _, raw, _ = c.request("GET", "/api/csrf-token")
        c.csrf = json.loads(raw)["csrf_token"]
        clients[key] = c
    owner = clients["analyst"]

    def call(client, method, path, body=None, **params):
        status, raw, _ = client.request(method, path, body, params or None)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"raw": raw[:200].decode(errors="replace")}

    status, defs = call(owner, "GET", "/api/reports/definitions")
    save("definitions", defs)
    keys = [d["key"] for d in defs.get("items", [])]
    check(status == 200 and "search_results@1" in keys, f"definitions -> {status} {keys}")

    def run(client, payload):
        status, body = call(client, "POST", "/api/reports/runs", payload)
        check(status == 202, f"POST /api/reports/runs -> {status} (202, job)")
        job_id = body["job"]["job_id"]
        deadline = time.time() + 90
        job = {}
        while time.time() < deadline:
            _, job = call(client, "GET", f"/api/jobs/{job_id}")
            if job.get("job", {}).get("status") in TERMINAL:
                break
            time.sleep(0.5)
        _, got = call(client, "GET", f"/api/reports/runs/{body['run']['id']}")
        return job.get("job", {}), got["run"]

    payload = {"report_id": "search_results",
               "parameters": {"criteria": {"sources": [ids["source_alpha"], ids["source_beta"]]}}}
    job, first = run(owner, payload)
    save("run_first", first)
    check(job.get("status") == "COMPLETED", f"report_run job -> {job.get('status')}")
    check(first["status"] == "completed" and first["snapshot"] and first["snapshot_at"],
          f"run completed with a snapshot ({first['status']}, {first['snapshot']})")
    check(first["isolation_level"] == "repeatable read, read only",
          f"isolation recorded: {first['isolation_level']}")
    check(first["job_id"] == job.get("job_id"), "the run names its job")
    listing, count = first["datasets"]
    status, rows = call(owner, "GET", f"/api/reports/runs/{first['id']}/datasets/"
                                      "search_results.matches@1", limit=500)
    status, counted = call(owner, "GET", f"/api/reports/runs/{first['id']}/datasets/"
                                         "search_results.count@1")
    save("rows_first", rows)
    matched = counted["rows"][0]["matched"]
    check(listing["truncated"] is False and matched == len(rows["rows"]) == listing["row_count"]
          and matched >= 2,
          f"exact count {matched} equals the untruncated listing ({len(rows['rows'])})")

    _, second = run(owner, payload)
    save("run_second", second)
    same = all(first[k] == second[k] for k in ("definition_fingerprint", "parameters_fingerprint",
                                                "criteria_fingerprint"))
    same_q = [d["query_fingerprint"] for d in first["datasets"]] == \
             [d["query_fingerprint"] for d in second["datasets"]]
    _, rows2 = call(owner, "GET", f"/api/reports/runs/{second['id']}/datasets/"
                                  "search_results.matches@1", limit=500)
    check(same and same_q and rows2["rows"] == rows["rows"],
          "identical requests: identical fingerprints and rows")
    check(first["snapshot"] != second["snapshot"] or first["snapshot_at"] != second["snapshot_at"],
          "each run records its own snapshot")

    status, _ = call(clients["analyst2"], "GET", f"/api/reports/runs/{first['id']}")
    check(status == 404, f"another analyst cannot read the run -> {status}")
    status, _ = call(clients["analyst2"], "GET",
                     f"/api/reports/runs/{first['id']}/datasets/search_results.count@1")
    check(status == 404, f"...nor its rows -> {status}")
    status, _ = call(clients["admin"], "GET", f"/api/reports/runs/{first['id']}")
    check(status == 200, f"an administrator can read it -> {status}")
    status, everyone = call(clients["admin"], "GET", "/api/reports/runs", all="1")
    check(status == 200 and first["id"] in [r["id"] for r in everyone["items"]],
          "administrator ?all=1 lists it")
    status, _ = call(owner, "GET", "/api/reports/runs", all="1")
    check(status == 403, f"non-administrator ?all=1 -> {status}")

    status, body = call(clients["viewer"], "POST", "/api/reports/runs", payload)
    check(status == 403, f"a viewer cannot run a report -> {status}")
    status, body = call(owner, "POST", "/api/reports/runs",
                        {"report_id": "search_results", "parameters": {"criteria": {"x": 1}}})
    check(status == 400, f"invalid criteria refused before any job -> {status}")

    no_csrf = Client(base)
    no_csrf.cookie = owner.cookie
    status, _, _ = no_csrf.request("POST", "/api/reports/runs", payload)
    check(status in (400, 403), f"POST without CSRF token -> {status}")

    status, raw, _ = owner.request("GET", "/reports")
    check(status == 200 and b'id="reportsPage"' in raw, f"GET /reports -> {status}")
    (evidence / "reports_page.html").write_bytes(raw)
    (state / "reports_session.txt").write_text(owner.cookie)
    status, raw, _ = clients["viewer"].request("GET", "/reports")
    check(status == 200, f"GET /reports as a viewer -> {status}")
    (evidence / "reports_ids.json").write_text(json.dumps({
        "first": first["id"], "second": second["id"],
        "sources": [ids["source_alpha"], ids["source_beta"]]}))

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
