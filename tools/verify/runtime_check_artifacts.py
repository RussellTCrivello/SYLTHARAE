"""Runtime check of report artifacts against a running server (step 15).

    python tools/verify/runtime_check_artifacts.py <base-url> <state-dir>

Requires a server started against the database from ``runtime_provision.py``
(same ``<state-dir>``). HTTP only - no application imports: what is verified
is the running process, its background job worker included (each file is a
``report_artifact`` job, answered 202 and polled). Every downloaded file and
manifest is saved under ``<state-dir>/evidence/artifacts/`` and checked with
the offline verifier (``verify_artifact.py``, a separate process).

Central checks: a completed run is rendered in every format as a background
job; the downloaded bytes' SHA-256 equals the recorded digest, the response
header and the manifest; the manifest's own SHA-256 equals the record; the
manifest names the run's snapshot, fingerprints, counts and truncation; a
repeated request returns the same file without a job; the offline verifier
accepts every pair and rejects a modified file; visibility follows the run;
viewers cannot create files; unavailable formats are refused with a reason;
each download carries a DATA_EXPORTED audit id (read back through PostgreSQL
by the caller, see EXECUTION_STATUS.md).
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_check_signals import Client, check, failures  # noqa: E402

TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}
VERIFIER = Path(__file__).resolve().parent / "verify_artifact.py"


def main(base, state_dir):
    state = Path(state_dir)
    info = json.loads((state / "env.json").read_text())
    ids = info["ids"]
    out_dir = state / "evidence" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

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
            return status, {"raw": raw[:200].decode("utf-8", "replace")}

    def wait(client, job_id):
        deadline = time.time() + 90
        job = {}
        while time.time() < deadline:
            _, body = call(client, "GET", f"/api/jobs/{job_id}")
            job = body.get("job", {})
            if job.get("status") in TERMINAL:
                break
            time.sleep(0.5)
        return job

    payload = {"report_id": "search_results",
               "parameters": {"criteria": {"sources": [ids["source_alpha"], ids["source_beta"]]}}}
    status, body = call(owner, "POST", "/api/reports/runs", payload)
    check(status == 202, f"POST /api/reports/runs -> {status}")
    wait(owner, body["job"]["job_id"])
    _, got = call(owner, "GET", f"/api/reports/runs/{body['run']['id']}")
    run = got["run"]
    check(run["status"] == "completed", f"run {run['id']} completed ({run['status']})")
    rid = run["id"]

    status, listed = call(owner, "GET", f"/api/reports/runs/{rid}/artifacts")
    check(status == 200 and listed["items"] == []
          and {f["format"] for f in listed["formats"]} == {"csv", "html", "json", "xlsx"},
          f"no files yet; formats {[f['format'] for f in listed.get('formats', [])]}")

    requests = [{"format": "json"}, {"format": "html"}, {"format": "xlsx"},
                {"format": "csv", "dataset_key": "search_results.matches@1"},
                {"format": "csv", "dataset_key": "search_results.count@1"}]
    made = []
    for req in requests:
        status, body = call(owner, "POST", f"/api/reports/runs/{rid}/artifacts", req)
        check(status == 202 and body.get("job"), f"POST artifacts {req} -> {status} (202, job)")
        job = wait(owner, body["job"]["job_id"])
        check(job.get("status") == "COMPLETED", f"report_artifact job {req['format']} -> "
                                                f"{job.get('status')}")
        made.append(req)

    status, again = call(owner, "POST", f"/api/reports/runs/{rid}/artifacts", {"format": "json"})
    check(status == 200 and again.get("existing") is True and again.get("job") is None,
          f"repeated request returns the existing file without a job -> {status}")

    _, listed = call(owner, "GET", f"/api/reports/runs/{rid}/artifacts")
    items = listed["items"]
    check(len(items) == len(made), f"{len(items)} files listed")
    (out_dir / "list.json").write_text(json.dumps(listed, indent=2, ensure_ascii=False))
    audit_ids = []
    for item in items:
        aid = item["id"]
        status, raw, headers = owner.request("GET", f"/api/reports/artifacts/{aid}/download")
        digest = hashlib.sha256(raw).hexdigest()
        check(status == 200 and digest == item["sha256"] == headers.get("X-Artifact-SHA256"),
              f"download {item['filename']}: sha256 {digest[:12]} matches record and header")
        check(f'filename="{item["filename"]}"' in (headers.get("Content-Disposition") or ""),
              f"attachment named {item['filename']}")
        audit = headers.get("X-Disclosure-Audit-Id")
        check(bool(audit), f"DATA_EXPORTED audit id {audit}")
        audit_ids.append(audit)
        (out_dir / item["filename"]).write_bytes(raw)
        status, mraw, mheaders = owner.request("GET", f"/api/reports/artifacts/{aid}/manifest")
        check(status == 200 and hashlib.sha256(mraw).hexdigest() == item["manifest_sha256"],
              f"manifest of {item['filename']} hashes to the recorded manifest_sha256")
        audit_ids.append(mheaders.get("X-Disclosure-Audit-Id"))
        manifest_path = out_dir / (item["filename"] + ".manifest.json")
        manifest_path.write_bytes(mraw)
        manifest = json.loads(mraw)
        check(manifest["snapshot"]["id"] == run["snapshot"]
              and manifest["run"]["criteria_fingerprint"] == run["criteria_fingerprint"]
              and manifest["report"]["definition_fingerprint"] == run["definition_fingerprint"]
              and [d["query_fingerprint"] for d in manifest["datasets"]]
              == [d["query_fingerprint"] for d in run["datasets"]],
              "manifest names the run's snapshot and fingerprints")
        verified = subprocess.run([sys.executable, str(VERIFIER), str(out_dir / item["filename"]),
                                   str(manifest_path), "--manifest-sha256",
                                   item["manifest_sha256"]], capture_output=True, text=True)
        check(verified.returncode == 0, f"offline verifier accepts {item['filename']}")
        status, v = call(owner, "GET", f"/api/reports/artifacts/{aid}/verify")
        check(status == 200 and v.get("ok") is True, f"server verification of {aid}: {v.get('checks')}")

    first = items[0]
    tampered = out_dir / ("tampered_" + first["filename"])
    data = (out_dir / first["filename"]).read_bytes()
    tampered.write_bytes(data[:-2] + b"X\n")
    rejected = subprocess.run([sys.executable, str(VERIFIER), str(tampered),
                               str(out_dir / (first["filename"] + ".manifest.json"))],
                              capture_output=True, text=True)
    check(rejected.returncode == 1 and "MISMATCH" in rejected.stdout,
          "offline verifier rejects a modified file")

    aid = first["id"]
    for path in (f"/api/reports/artifacts/{aid}", f"/api/reports/artifacts/{aid}/download"):
        status, _ = call(clients["analyst2"], "GET", path)
        check(status == 404, f"another analyst: GET {path} -> {status}")
    status, _, _ = clients["admin"].request("GET", f"/api/reports/artifacts/{aid}/download")
    check(status == 200, f"administrator downloads -> {status}")
    status, _ = call(clients["viewer"], "POST", f"/api/reports/runs/{rid}/artifacts",
                     {"format": "json"})
    check(status == 403, f"viewer cannot create a file -> {status}")
    status, body = call(owner, "POST", f"/api/reports/runs/{rid}/artifacts", {"format": "pdf"})
    check(status == 400 and "step 18" in json.dumps(body), f"pdf refused with its reason -> {status}")
    status, _ = call(owner, "POST", f"/api/reports/runs/{rid}/artifacts", {"format": "csv"})
    check(status == 400, f"csv without a dataset -> {status}")

    (out_dir / "audit_ids.json").write_text(json.dumps({"run": rid, "audit_ids": audit_ids}))
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
