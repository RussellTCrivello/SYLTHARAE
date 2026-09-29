"""Runtime check of the Audit Log viewer against a running server.

    python tools/verify/runtime_check_audit.py <base-url> <state-dir>

HTTP only (no application imports), against a server started on the
database from ``runtime_provision.py``. Best run after
``runtime_check_artifacts.py`` so that the log holds real ``DATA_EXPORTED``
entries written by the running process; their ids are read from
``<state-dir>/evidence/artifacts/audit_ids.json`` when present and must all be
visible, with the digest the download reported.

Checks: 401 before sign-in; analysts and viewers refused (page and API);
the page renders with the production CSP; entries come newest first; keyset
paging with a small page returns every matching entry exactly once and ends
with has_more false; the total is reported as not counted; the resource
prefix and the time window narrow on the server; the action menu lists the
actions the application wrote; invalid filters are 400 with a message that
names the parameter; an unknown id is 404. Saves the page HTML and the
admin session for ``audit_page_runtime.mjs`` (run it next).
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_check_signals import Client, check, failures  # noqa: E402


def main(base, state_dir):
    state = Path(state_dir)
    info = json.loads((state / "env.json").read_text())

    anon = Client(base)
    status, _, _ = anon.request("GET", "/api/audit")
    check(status == 401, f"anonymous GET /api/audit -> {status}")

    clients = {}
    for key in ("admin", "analyst", "viewer"):
        c = Client(base)
        username, password = info["users"][key]
        status, _, _ = c.login(username, password)
        check(status == 200 and c.cookie, f"{key} login -> {status}")
        clients[key] = c
    admin = clients["admin"]

    def call(client, path, **params):
        status, raw, headers = client.request("GET", path, None, params or None)
        try:
            return status, json.loads(raw), headers
        except ValueError:
            return status, {"raw": raw[:200].decode("utf-8", "replace")}, headers

    for key in ("analyst", "viewer"):
        for path in ("/api/audit", "/api/audit/actions", "/api/audit/1"):
            status, _, _ = call(clients[key], path)
            check(status == 403, f"{key} GET {path} -> {status}")
        status, _, _ = clients[key].request("GET", "/admin/audit")
        check(status in (302, 403), f"{key} GET /admin/audit -> {status}")

    status, raw, headers = admin.request("GET", "/admin/audit")
    html = raw.decode("utf-8", "replace")
    csp = headers.get("Content-Security-Policy", "")
    check(status == 200 and 'id="auditPage"' in html and "audit-page.js" in html,
          f"admin GET /admin/audit -> {status}")
    (state / "evidence").mkdir(parents=True, exist_ok=True)
    (state / "evidence" / "audit_page.html").write_text(html, encoding="utf-8")
    (state / "audit_session.txt").write_text(admin.cookie, encoding="utf-8")
    check("unsafe-inline" not in csp.split("script-src", 1)[-1].split(";", 1)[0] and "script-src" in csp,
          "page CSP has script-src without unsafe-inline")

    status, body, _ = call(admin, "/api/audit/actions")
    names = body.get("items", [])
    check(status == 200, f"GET /api/audit/actions -> {status}")
    for expected in ("login.success", "DATA_EXPORTED", "report.run", "report.artifact"):
        check(expected in names, f"action menu lists {expected}")

    status, body, _ = call(admin, "/api/audit", limit="50")
    ids = [e["id"] for e in body.get("items", [])]
    check(status == 200 and ids == sorted(ids, reverse=True) and ids, "entries newest first")
    check(body.get("total") is None and "not counted" in (body.get("total_reason") or ""),
          "total reported as not counted")

    # Keyset paging over DATA_EXPORTED with a page of 3: every entry once.
    seen, before, pages = [], None, 0
    while pages < 100:
        params = {"action": "DATA_EXPORTED", "limit": "3"}
        if before:
            params["before_id"] = str(before)
        status, body, _ = call(admin, "/api/audit", **params)
        pages += 1
        seen += [e["id"] for e in body["items"]]
        if not body["has_more"]:
            break
        before = body["next_before_id"]
    status, full, _ = call(admin, "/api/audit", action="DATA_EXPORTED", limit="200")
    full_ids = [e["id"] for e in full["items"]]
    check(seen == full_ids and len(seen) == len(set(seen)) and seen,
          f"paging by 3 returned all {len(full_ids)} DATA_EXPORTED entries once ({pages} pages)")
    check(all(e["action"] == "DATA_EXPORTED" for e in full["items"]), "action filter applied by the server")

    evidence = state / "evidence" / "artifacts" / "audit_ids.json"
    if evidence.exists():
        wanted_ids = sorted({int(x) for x in json.loads(evidence.read_text())["audit_ids"]})
        check(set(wanted_ids) <= set(full_ids),
              f"the {len(wanted_ids)} DATA_EXPORTED ids from the artifact check are all listed")
        # Each file download's entry names the file and its digest; the digest
        # must equal the SHA-256 of the bytes the artifact check saved.
        matched = 0
        for entry_id in wanted_ids:
            status, one, _ = call(admin, f"/api/audit/{entry_id}")
            detail = (one.get("entry") or {}).get("detail") or {}
            if detail.get("kind") != "report_artifact":
                continue
            saved = state / "evidence" / "artifacts" / detail.get("filename", "")
            if saved.is_file():
                measured = hashlib.sha256(saved.read_bytes()).hexdigest()
                ok = detail.get("artifact", {}).get("sha256") == measured
                check(ok, f"entry {entry_id}: digest equals the saved {saved.name}")
                matched += ok
        check(matched > 0, f"{matched} file download entries matched saved files")

    status, body, _ = call(admin, "/api/audit", resource="export:report_manifest", limit="200")
    check(status == 200 and body["items"]
          and all(e["resource"].startswith("export:report_manifest") for e in body["items"]),
          f"resource prefix narrows on the server ({len(body.get('items', []))} manifest downloads)")
    status, body, _ = call(admin, "/api/audit", resource="export:report_%", limit="200")
    check(status == 200 and body["items"] == [], "'%' in the prefix is literal")

    status, body, _ = call(admin, "/api/audit", since="2999-01-01T00:00:00Z")
    check(status == 200 and body["items"] == [] and body["has_more"] is False,
          "a future window is empty and says so")

    for params, needle in (({"limit": "500"}, "limit"), ({"since": "tomorrow"}, "since"),
                           ({"sort": "asc"}, "unknown filter")):
        status, body, _ = call(admin, "/api/audit", **params)
        message = (body.get("error") or {}).get("message", "")
        check(status == 400 and needle in message, f"{params} -> {status} {message!r}")
    status, _, _ = call(admin, "/api/audit/999999999")
    check(status == 404, f"unknown entry -> {status}")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
