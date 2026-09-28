"""Runtime check of monitoring rules against a running server.

    python tools/verify/runtime_check_rules.py <base-url> <state-dir>

Requires a server started against the database from ``runtime_provision.py``
(same ``<state-dir>``). HTTP only - no application imports: what is verified
is the running process, its background job worker included. Responses are
saved under ``<state-dir>/evidence/rules_*.json``.
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
        (evidence / f"rules_{name}.json").write_text(json.dumps(body, indent=2,
                                                                ensure_ascii=False))

    anon = Client(base)
    status, _, _ = anon.request("GET", "/api/rules")
    check(status == 401, f"anonymous GET /api/rules -> {status} (401 expected)")

    clients = {}
    for key in ("analyst", "analyst2", "admin", "viewer"):
        c = Client(base)
        username, password = info["users"][key]
        status, _, _ = c.login(username, password)
        check(status == 200 and c.cookie, f"{key} login -> {status}")
        # Login starts a new session; its CSRF token is fetched afterwards,
        # as the browser does when the next page loads.
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

    definition = {"criteria": {"sources": [ids["source_alpha"]]},
                  "signals": {"signal_types": ["date_reference"]},
                  "notify_existing": True}
    status, body = call(clients["viewer"], "POST", "/api/rules",
                        {"name": "viewer rule", "definition": definition})
    check(status == 403, f"viewer POST /api/rules -> {status} (403 expected)")

    status, body = call(owner, "POST", "/api/rules", {"name": "priority", "definition":
                                                      definition, "priority": "critical"})
    check(status == 400, f"a caller-chosen priority is refused -> {status}")

    name = f"runtime watch {int(time.time())}"
    status, body = call(owner, "POST", "/api/rules", {"name": name, "definition": definition})
    save("created", body)
    check(status == 201, f"analyst POST /api/rules -> {status}")
    rule = body["rule"]
    check(rule["version"] == 1 and len(rule["definition_fingerprint"]) == 64,
          "rule stored at version 1 with a fingerprint")

    def evaluate():
        status, body = call(owner, "POST", f"/api/rules/{rule['id']}/evaluate")
        check(status == 202, f"POST /api/rules/{rule['id']}/evaluate -> {status} (202, job)")
        job_id = body["job"]["job_id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            status, job = call(owner, "GET", f"/api/jobs/{job_id}")
            if job.get("job", {}).get("status") in TERMINAL:
                return job
            time.sleep(0.5)
        return job

    job = evaluate()
    save("job_first", job)
    check(job["job"]["status"] == "COMPLETED", f"evaluation job -> {job['job']['status']}")

    status, log = call(owner, "GET", f"/api/rules/{rule['id']}/evaluations")
    save("evaluations_first", log)
    first = log["evaluations"][0]
    check(status == 200 and first["status"] == "completed", "evaluation recorded as completed")
    check(first["owner_role"] == "analyst" and first["access_scope"]["role"] == "analyst",
          "evaluation records the owner's role and scope")
    check(first["counts"].get("notifications", 0) >= 1,
          f"notify_existing delivered notifications ({first['counts']})")

    status, listed = call(owner, "GET", "/api/notifications", type="rule_match", limit=200)
    mine = [n for n in listed["notifications"] if n["rule_id"] == rule["id"]]
    save("owner_notifications", mine)
    check(bool(mine), f"owner sees the rule's notifications ({len(mine)})")
    check(all(n["addressed"] and n["priority"] in ("high", "medium", "low") for n in mine),
          "every notification is addressed and has a derived (non-critical) priority")
    subjects = [s for n in mine for s in n["metadata"]["subjects"]]
    check(any(s.get("evidence_sentence") == "The review will be held on 5 October 2026."
              for s in subjects), "a notification carries the evidence sentence")
    check(all(n["metadata"]["priority_basis"]["rule"] == "priority-1" for n in mine),
          "priority basis stored with each notification")

    alert_id = mine[0]["id"] if mine else 0
    for key in ("analyst2", "admin", "viewer"):
        c = clients[key]
        status, _ = call(c, "GET", f"/api/notifications/{alert_id}")
        check(status == 404, f"{key} GET another user's rule notification -> {status} (404)")
        status, other = call(c, "GET", "/api/notifications", limit=1000)
        check(alert_id not in {n["id"] for n in other.get("notifications", [])},
              f"{key}'s notification list excludes it")
        status, page = call(c, "GET", "/api/notifications/paginated", read_status="all",
                            per_page=1000)
        check(alert_id not in {n["id"] for n in page.get("notifications", [])},
              f"{key}'s paginated list excludes it")
    for key in ("analyst2", "admin"):
        status, _ = call(clients[key], "POST", f"/api/notifications/{alert_id}/read")
        check(status == 404, f"{key} cannot mark it read -> {status} (404)")
        status, _ = call(clients[key], "POST", f"/api/notifications/{alert_id}/dismiss")
        check(status == 404, f"{key} cannot dismiss it -> {status} (404)")
    status, detail = call(owner, "GET", f"/api/notifications/{alert_id}")
    check(status == 200 and not detail["notification"]["read"]
          and not detail["notification"]["dismissed"], "the owner's copy is untouched")
    status, _ = call(clients["analyst2"], "GET", f"/api/rules/{rule['id']}")
    check(status == 404, f"another analyst GET the rule -> {status} (404)")
    status, _ = call(clients["admin"], "GET", f"/api/rules/{rule['id']}")
    check(status == 200, f"administrator GET the rule -> {status}")

    job = evaluate()
    status, log = call(owner, "GET", f"/api/rules/{rule['id']}/evaluations")
    save("evaluations_second", log)
    second = log["evaluations"][0]
    check(second["counts"].get("new_subjects") == 0 and second["counts"].get(
        "notifications") == 0, f"re-evaluation is deduplicated ({second['counts']})")

    status, raw, _ = owner.request("GET", "/notifications")
    check(status == 200, f"GET /notifications page as the owner -> {status}")

    status, body = call(owner, "DELETE", f"/api/rules/{rule['id']}")
    check(status == 200 and body["rule"]["status"] == "archived", "rule archived")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
