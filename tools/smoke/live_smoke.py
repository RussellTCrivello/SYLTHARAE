"""Live end-to-end smoke test against a running SYLTHARAE server.

Drives the real HTTP API the way a browser behind a TLS-terminating reverse
proxy does (X-Forwarded-Proto/Host, same-origin Referer, CSRF token), so it
exercises the production configuration: Waitress, FLASK_ENV=production,
TRUSTED_PROXY_COUNT=1. See docs/TESTING.md ("Live smoke test").

    python tools/smoke/live_smoke.py install   # first run: setup wizard
    python tools/smoke/live_smoke.py run1      # full functional pass
    # restart the server, then
    python tools/smoke/live_smoke.py run2      # repeat after restart

Configuration (environment):
    SMOKE_BASE_URL     server URL                     (http://127.0.0.1:5055)
    SMOKE_PUBLIC_HOST  host the "proxy" presents      (syl.example.test)
    SMOKE_EVIDENCE     directory for generated files
    must be inside the
                       server's INGESTION_ROOTS       (./smoke-evidence)
    SMOKE_ADMIN_PASSWORD  admin password to install / sign in with (required)
    SMOKE_DB_HOST, SMOKE_DB_PORT, SMOKE_DB_USER, SMOKE_DB_PASSWORD, SMOKE_DB
                       database the wizard installs into (install only)

Needs ``requests`` and ``python-docx``. Exit status is non-zero on any FAIL.
"""
import json
import os
import sys
import time
import zipfile
from pathlib import Path
import requests

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:5055").rstrip("/")
PUBLIC = os.environ.get("SMOKE_PUBLIC_HOST", "syl.example.test")
EV = Path(os.environ.get("SMOKE_EVIDENCE", "smoke-evidence")).resolve()
ADMIN = (os.environ.get("SMOKE_ADMIN_USER", "admin"), os.environ.get("SMOKE_ADMIN_PASSWORD", ""))
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f" -- {str(detail)[:300]}" if detail else ""), flush=True)
    return ok


class ProxySession(requests.Session):
    """Behaves like a browser behind a TLS-terminating proxy."""
    def __init__(self):
        super().__init__()
        self.headers["X-Forwarded-Proto"] = "https"
        self.headers["X-Forwarded-Host"] = PUBLIC
        self.token = None

    def request(self, method, url, **kw):
        if not url.startswith("http"):
            url = BASE + url
        if method.upper() not in ("GET", "HEAD") and self.token:
            kw.setdefault("headers", {})["X-CSRFToken"] = self.token
        if method.upper() not in ("GET", "HEAD"):
            kw.setdefault("headers", {}).setdefault("Referer", f"https://{PUBLIC}/")
        r = super().request(method, url, **kw)
        for c in self.cookies:
            c.secure = False
        return r

    def refresh_csrf(self):
        r = self.get("/api/csrf-token")
        self.token = r.json().get("csrf_token")
        return r


def make_evidence():
    EV.mkdir(exist_ok=True)
    (EV / "memo.txt").write_text("Internal memo: project zephyrine kickoff on 2026-03-14.\n")
    (EV / "ledger.csv").write_text("id,item,amount\n1,zephyrine licence,1200\n2,travel,340\n")
    import docx
    d = docx.Document()
    d.add_paragraph("Minutes: the zephyrine steering group met.")
    d.save(EV / "minutes.docx")
    with zipfile.ZipFile(EV / "bundle.zip", "w") as z:
        z.writestr("copy_of_memo.txt", (EV / "memo.txt").read_text())  # duplicate content
        z.writestr("inner/notes.txt", "Nested notes mentioning zephyrine and a budget.\n")


def login(s, user, pw):
    s.refresh_csrf()
    r = s.post("/auth/login", json={"username": user, "password": pw})
    s.refresh_csrf()  # session identity changes at login
    return r


def install():
    s = ProxySession()
    r = s.get("/health", allow_redirects=False)
    check("pre-install /health redirects to /setup", r.status_code == 302 and "/setup" in r.headers.get("Location", ""), r.status_code)
    s.refresh_csrf()
    db = {"host": os.environ.get("SMOKE_DB_HOST", "localhost"),
          "port": int(os.environ.get("SMOKE_DB_PORT", "5432")),
          "user": os.environ.get("SMOKE_DB_USER", "postgres"),
          "password": os.environ.get("SMOKE_DB_PASSWORD", ""),
          "database": os.environ.get("SMOKE_DB", "syl_smoke")}
    r = s.post("/api/setup/test-database", json=db)
    check("wizard: test-database", r.status_code == 200, r.text)
    body = {"db_host": db["host"], "db_port": 5432, "db_user": "postgres", "db_password": db["password"],
            "db_name": db["database"], "admin_username": ADMIN[0], "admin_password": ADMIN[1],
            "environment": "production", "flask_host": "0.0.0.0", "log_level": "INFO",
            "ingestion_roots": str(EV)}
    t = time.time()
    r = s.post("/api/setup/install", json=body, timeout=600)
    check("wizard: install", r.status_code == 200, f"{r.status_code} {time.time()-t:.0f}s {r.text[:200]}")
    r = s.post("/api/setup/install", json=body)
    check("wizard: second install refused", r.status_code in (403, 409), r.status_code)


def main(phase):
    s = ProxySession()
    r = s.get("/health")
    check(f"[{phase}] /health healthy", r.status_code == 200 and r.json().get("status") == "healthy", r.text[:120])
    check(f"[{phase}] HSTS on proxied HTTPS", "max-age" in r.headers.get("Strict-Transport-Security", ""), r.headers.get("Strict-Transport-Security"))
    plain = requests.get(BASE + "/health")
    check(f"[{phase}] no HSTS on plain HTTP", "Strict-Transport-Security" not in plain.headers)
    check(f"[{phase}] anonymous API -> 401", s.get("/api/search", params={"query": "x"}).status_code == 401)
    r = login(s, "admin", "wrong-password-123")
    check(f"[{phase}] bad password rejected", r.status_code in (400, 401, 403), r.status_code)
    r = login(s, *ADMIN)
    check(f"[{phase}] admin login", r.status_code == 200, r.text[:150])
    s2 = ProxySession()
    s2.cookies.update(s.cookies)
    r = s2.post("/api/input/sides", json={"name": "nocsrf"})
    check(f"[{phase}] POST without CSRF token rejected", r.status_code in (400, 403), r.status_code)
    r = s.post("/api/input/sides", json={"name": "xsite"}, headers={"Referer": "https://evil.example/"})
    check(f"[{phase}] cross-site Referer rejected", r.status_code == 400 and "CSRF" in r.text, r.status_code)
    r = s.post("/api/input/sides", json={"name": "nohost"}, headers={"X-Forwarded-Host": ""})
    check(f"[{phase}] proxy dropping Host -> CSRF 400 (documented)", r.status_code == 400 and "CSRF" in r.text, r.status_code)

    src, side = f"SmokeSource{phase}", f"SmokeSide{phase}"
    r = s.post("/api/input/sources", json={"name": src, "job": "Audit", "importance": 0.8, "country": "NL", "city": "Amsterdam"})
    check(f"[{phase}] create source", r.status_code in (200, 201), r.text[:200])
    r = s.post("/api/input/sides", json={"name": side, "importance": 0.8})
    check(f"[{phase}] create side", r.status_code in (200, 201), r.text[:200])
    r = s.post("/api/input/jobs", json={"path": "/etc", "source": src, "side": side, "recursive": True})
    check(f"[{phase}] path outside INGESTION_ROOTS rejected", r.status_code in (400, 403), f"{r.status_code} {r.text[:120]}")
    r = s.post("/api/input/jobs", json={"path": str(EV), "source": src, "side": side, "recursive": True})
    ok = check(f"[{phase}] submit ingestion job", r.status_code in (200, 201, 202), r.text[:200])
    job = {}
    if ok:
        jid = r.json().get("job_id") or r.json().get("job", {}).get("job_id")
        for _ in range(240):
            job = s.get(f"/api/jobs/{jid}").json()
            job = job.get("job", job)
            if job.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"):
                break
            time.sleep(1)
        check(f"[{phase}] job finished", job.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS"),
              {k: job.get(k) for k in ("status", "processed_files", "total_files", "failed_files", "errors")})
        print("   job:", json.dumps({k: v for k, v in job.items() if k not in ("options",)}, default=str)[:700])
    r = s.get("/api/search", params={"query": "zephyrine"})
    txt = r.text
    check(f"[{phase}] search finds keyword", r.status_code == 200 and "zephyrine" in txt.lower(), txt[:200])
    for fn in ("memo.txt", "ledger.csv", "minutes.docx", "notes.txt"):
        check(f"[{phase}] search result includes {fn}", fn in txt)
    r = s.get("/api/notifications")
    check(f"[{phase}] notifications endpoint", r.status_code == 200, r.text[:150])
    r = s.get("/api/import-export/settings/export")
    check(f"[{phase}] settings export", r.status_code == 200, r.status_code)
    r = s.post("/api/search/export", json={"query": "zephyrine", "format": "csv"})
    check(f"[{phase}] search export", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    user = f"analyst{phase}"
    r = s.post("/api/auth/users", json={"username": user, "password": "Analyst-Temp-Pass1!", "role": "analyst"})
    check(f"[{phase}] admin creates analyst", r.status_code in (200, 201), r.text[:150])
    a = ProxySession()
    login(a, user, "Analyst-Temp-Pass1!")
    r = a.get("/api/search", params={"query": "zephyrine"})
    check(f"[{phase}] analyst gated until password change", r.status_code == 403, r.status_code)
    r = a.post("/auth/change-password", json={"new_password": "Analyst-Real-Pass2!"})
    check(f"[{phase}] password change without the current password refused",
          r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = a.post("/auth/change-password", json={"current_password": "Analyst-Temp-Pass1!",
                                              "new_password": "Analyst-Real-Pass2!"})
    check(f"[{phase}] analyst changes password", r.status_code == 200, r.text[:150])
    a.refresh_csrf()
    r = a.get("/api/search", params={"query": "zephyrine"})
    check(f"[{phase}] analyst can search after change", r.status_code == 200, r.status_code)
    r = a.get("/api/settings/database")
    check(f"[{phase}] analyst denied admin settings", r.status_code == 403, r.status_code)
    r = s.post("/auth/logout")
    check(f"[{phase}] logout", r.status_code in (200, 302), r.status_code)
    check(f"[{phase}] session invalid after logout", s.get("/api/search", params={"query": "x"}).status_code == 401)


if __name__ == "__main__":
    if len(sys.argv) != 2 or not ADMIN[1]:
        sys.exit(__doc__)
    mode = sys.argv[1]
    if mode == "install":
        make_evidence()
        install()
    else:
        main(mode)
    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"SUMMARY {mode}: {len(RESULTS)-len(fails)} pass, {len(fails)} fail")
    sys.exit(1 if fails else 0)
