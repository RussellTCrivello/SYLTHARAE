# Operations

Running SYLTHARAE day to day: serving it in production, health checks,
logs, backups, upgrades, rollback and recovery. Installation is in
[INSTALL.md](INSTALL.md); every setting is in [CONFIGURATION.md](CONFIGURATION.md).

## 1. Production serving

The production layout, verified end to end for every release
([TESTING.md](TESTING.md#live-smoke-test)):

```text
browser --HTTPS/2--> nginx :443 (TLS; :80 only redirects)
        --HTTP/1.1--> Waitress 127.0.0.1:5000 (one process: web + background jobs)
        --> Flask --> PostgreSQL
```

| File | Purpose |
|---|---|
| [`deploy/nginx/syltharae.conf`](../deploy/nginx/syltharae.conf) | TLS reverse proxy: HTTP→HTTPS redirect, TLS 1.2/1.3, HTTP/2, proxy headers, upload size, timeouts |
| [`deploy/systemd/syltharae.service`](../deploy/systemd/syltharae.service) | The application as a sandboxed Linux service |

`tests/integration/test_deploy_nginx.py` runs `nginx -t` on the nginx file,
and `tests/unit/test_deploy_configs.py` checks both files against the
settings they depend on.

### Waitress

`python run_web.py` serves with Flask's built-in server unless told
otherwise. That server is for development and single-workstation use only.
**In production use Waitress**, a pure-Python WSGI server that runs on
Windows and Linux:

```bash
pip install -e ".[server]"          # or: pip install "waitress>=3.0"
```

```ini
# .env
FLASK_ENV=production
FLASK_DEBUG=false
WSGI_SERVER=waitress
WAITRESS_THREADS=32        # each open Jobs page holds one thread for its live stream
TRUSTED_PROXY_COUNT=1      # one TLS-terminating reverse proxy in front
FLASK_HOST=127.0.0.1       # listen only for the proxy
FLASK_PORT=5000
```

The setup wizard writes `WSGI_SERVER` and `TRUSTED_PROXY_COUNT` to `.env`
from the environment the server was started with (`waitress` by default in
production when it is installed; the proxy count is never guessed), so a
restart keeps them.

`run_web.py` then prints `[OK] Serving with Waitress (32 threads)`. It
still runs `core.init` first (settings, resource coordinator, pending
migrations), so the start-up behaviour is identical to development.

Background jobs run on threads inside this one process, so run **one**
application process per database. Scale with `WAITRESS_THREADS` and the
processing worker settings, not with more processes (Gunicorn or uWSGI
workers would each run their own jobs).

### Start-up checks

Before serving, `run_web.py` checks its settings (`apps/web/deployment.py`).
Settings that would make the server unsafe or wrong **stop it**: it prints
what to change and exits with status 2, which the systemd unit does not
retry (`RestartPreventExitStatus=2`).

| Refused (exit 2) | Why |
|---|---|
| `FLASK_DEBUG` on with `FLASK_ENV=production` | the debugger runs code sent by any client |
| `TRUSTED_PROXY_COUNT` not a whole number ≥ 0 | it used to be ignored silently, trusting no proxy |
| `FLASK_PORT` not a port number | |

Warnings do not stop the server; each says what to change. All but the
last apply only with `FLASK_ENV=production`:

| Warning | When |
|---|---|
| development server in production | `WSGI_SERVER` is not `waitress` |
| Waitress missing | `WSGI_SERVER=waitress` but the package is not installed (the built-in server is used) |
| proxy can be bypassed | `TRUSTED_PROXY_COUNT` > 0 while `FLASK_HOST` accepts outside connections: clients could then forge their address |
| no TLS proxy | `TRUSTED_PROXY_COUNT=0` and `FLASK_HOST` accepts outside connections: browsers talk to the server directly over plain HTTP, so `Secure` cookies and HSTS cannot work |
| short secret key | `FLASK_SECRET_KEY` shorter than 32 characters |
| invalid thread count | `WAITRESS_THREADS` is not a number (32 is used); in every environment |

`python verify_readiness.py` reports the same findings (refusals as
critical), and `--json` prints them as JSON for automation.

### Reverse proxy and TLS

Use [`deploy/nginx/syltharae.conf`](../deploy/nginx/syltharae.conf):

1. Copy it to `/etc/nginx/sites-available/` (and link it from
   `sites-enabled/`) or to `/etc/nginx/conf.d/`.
2. Replace `syltharae.example.org` and the two certificate paths. For Let's
   Encrypt, the HTTP server already serves `/.well-known/acme-challenge/`
   from `/var/www/letsencrypt`.
3. `sudo nginx -t && sudo systemctl reload nginx`.

It is tested with nginx 1.24 (Ubuntu 24.04, in CI) and 1.26. On 1.25.1 and
later, `nginx -t` warns that `listen ... http2` is deprecated; the directive
still works, and it is kept because Debian 12 and Ubuntu 24.04 ship versions
without the newer `http2 on;`.

What the application relies on:

* **Proxy headers.** With `TRUSTED_PROXY_COUNT=1` the application believes
  one proxy's `X-Forwarded-For`, `-Proto` and `-Host`. The proxy must
  **set** `X-Forwarded-For` to the connecting address (`$remote_addr`), not
  append to what the client sent (`$proxy_add_x_forwarded_for`), and must
  pass `X-Forwarded-Proto` and `X-Forwarded-Host` (or the original `Host`).
  Count every proxy in the chain: behind a load balancer *and* nginx, set
  `TRUSTED_PROXY_COUNT=2`.
* **Only the proxy reaches the application**: `FLASK_HOST=127.0.0.1` (or a
  firewall). Otherwise a client can talk to Waitress directly and claim any
  address.
* **Security headers come from the application**, including HSTS, which it
  sends only in production over HTTPS. Do not add them in nginx: browsers
  combine duplicated headers.
* **Sizes and timeouts.** `client_max_body_size` must be at least
  `OPERATIONS_MAX_UPLOAD_MB` (2 GB by default); browsers send larger files in
  chunks. `proxy_read_timeout 600s` covers long exports; the job event
  streams send `X-Accel-Buffering: no`, so nginx does not buffer them.

### Deployment verification

After installing or changing the proxy, check from a client machine
(replace the host name):

1. **HTTP redirects to HTTPS**: `curl -sI http://syltharae.example.org/`
   answers `301` with `Location: https://…`.
2. **Sign-in works.** If it fails with **400 "CSRF token is missing or
   invalid"**, the proxy is not passing the public host name and scheme
   (`X-Forwarded-Host`/`Host`, `X-Forwarded-Proto`): the application checks
   that a form comes from its own HTTPS origin, and without those headers it
   sees `http://127.0.0.1:5000` instead. Fix the proxy headers; do not turn
   CSRF protection off. `tests/security/test_proxy_deployment.py` keeps this
   behaviour under test.
3. **HSTS is sent**: `curl -sI https://syltharae.example.org/health` shows
   `Strict-Transport-Security`. If not, the application does not see HTTPS:
   check `X-Forwarded-Proto` and `TRUSTED_PROXY_COUNT`.
4. **Real client addresses are recorded**: after signing in,
   `SELECT action, ip_address FROM audit_log ORDER BY id DESC LIMIT 5;`
   shows your address, not the proxy's (`127.0.0.1`). If it shows the
   proxy's, set `TRUSTED_PROXY_COUNT`.
5. **Optional, complete**: run the live smoke test through the proxy
   (`SMOKE_BASE_URL=https://syltharae.example.org`, see
   [TESTING.md](TESTING.md#live-smoke-test)) against a test installation.

### Linux: systemd

Use [`deploy/systemd/syltharae.service`](../deploy/systemd/syltharae.service).
It expects the application in `/opt/syltharae` with its virtual environment
in `/opt/syltharae/.venv`, and data in `/var/lib/syltharae`; edit the paths
if yours differ.

```bash
sudo useradd --system --home /var/lib/syltharae --shell /usr/sbin/nologin syltharae
sudo install -d -o syltharae -g syltharae -m 0750 /var/lib/syltharae
sudo chown -R syltharae:syltharae /opt/syltharae
sudo cp deploy/systemd/syltharae.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now syltharae
journalctl -u syltharae -f
```

* Settings come from `/opt/syltharae/.env`, which the application reads
  itself. The unit has no `EnvironmentFile=`: systemd parses quotes and `$`
  differently, so a password written by the setup wizard could be read
  wrongly.
* The unit is sandboxed (`ProtectSystem=strict`, no capabilities, private
  `/tmp` and devices, …): `systemd-analyze security syltharae` rates it 3.2,
  "OK". It may write only `/var/lib/syltharae` and `/opt/syltharae` (the
  wizard's `.env`, `.flask_secret_key`, `.system_initialized`). Remove
  `PrivateDevices=true` if `COMPUTE_MODE` uses a GPU.
* A refused start-up (exit status 2) is not restarted; read the reason with
  `journalctl -u syltharae`.

### Windows: service

Use NSSM (or the Task Scheduler "at startup" trigger):

```bat
nssm install SYLTHARAE C:\syltharae\.venv\Scripts\python.exe run_web.py
nssm set SYLTHARAE AppDirectory C:\syltharae
nssm set SYLTHARAE AppEnvironmentExtra WSGI_SERVER=waitress
nssm start SYLTHARAE
```

## 2. Health checks

`GET /health` (public) returns

```json
{"status": "healthy", "uptime_seconds": 55, "checks": {"database": "ok", "settings": "ok"}}
```

with `200`, or `503` and `"status": "degraded"` when the database or the
settings store is unavailable. Before installation is finished it redirects
to `/setup` (302), like every other page. Point load balancers and
monitoring at it; `python verify_readiness.py --json` is the deeper check to
run after deployments.

## 3. Logs and monitoring

* Application logs: `APP_DATA_DIR/logs/` and standard output (under systemd:
  the journal). Rotate them with the platform's tool (logrotate, or a
  scheduled task on Windows).
* Errors are classified and collected by `Hdg_Err_Ex_Log`; administrators see
  them on the Error Dashboard (`/api/errors/recent`).
* Jobs: **Operations → Jobs** shows live progress, errors per file, and
  pause / resume / cancel / retry.
* Security events: `audit_log` table ([SECURITY.md §7](SECURITY.md#7-audit-trail)).
* Resource use: the Concurrency dashboard (admin) and the performance
  endpoints under `/api/performance`.

## 4. Backups

Back up **both** the database and the data directory:

```bash
pg_dump -Fc -h DBHOST -U DBUSER -f /backup/syltharae-$(date +%F).dump analysis
tar -czf /backup/syltharae-data-$(date +%F).tgz -C /var/lib syltharae   # APP_DATA_DIR
cp /opt/syltharae/.env /backup/   # store it as a secret: it holds the DB password and secret key
```

`APP_DATA_DIR` holds uploads, extraction scratch, checkpoints and runtime
state; the evidence itself is whatever you ingested from, which you should
already keep. Test a restore at least once:

```bash
createdb -h DBHOST -U DBUSER analysis_restore
pg_restore -h DBHOST -U DBUSER -d analysis_restore /backup/syltharae-2026-09-25.dump
```

The admin-only **Backup export** in the UI (`/api/import-export`) exports the
evidence tables as a ZIP; it is convenient for moving a case, but it is not
a substitute for `pg_dump`.

## 5. Upgrades

1. Read `CHANGELOG.md` for the target version.
2. Back up (§4).
3. Stop the service.
4. Update the code: `git fetch --tags && git checkout vX.Y.Z`.
5. Update dependencies: `pip install -r requirements.txt` (offline: from the
   new `offline-bundle/wheels`).
6. Start the service. Pending migrations are applied automatically on start;
   the log lists each one (`Applying migration 0014 ...`).
7. Run `python verify_readiness.py`.

Migrations only move forward. Recompile translations only if you edited
`.po` files yourself (`pybabel compile -d translations`).

## 6. Rollback

Code rolls back with `git checkout <previous tag>` and
`pip install -r requirements.txt`. A release that added migrations also
needs the database restored from the backup taken in step 2 of the upgrade -
older code does not know newer schema versions. Releases that add no
migration (the CHANGELOG says so) roll back with code alone.

## 7. Recovery

| Situation | Action |
| --- | --- |
| Server crashed during a job | Restart. Jobs that stopped updating are marked `FAILED`; **Retry** resumes from the checkpoint, and deduplication prevents files from being stored twice. |
| Lost administrator password / locked out | `python scripts/reset_admin_password.py` ([INSTALL.md E11](INSTALL.md#troubleshooting)). |
| Database unreachable | `/health` returns 503; the application keeps serving an error state and reconnects when the database returns. |
| Disk full in `APP_DATA_DIR` | Stop ingestion, clear `APP_DATA_DIR/extracted/` and `cache/` (both are regenerated), then retry failed jobs. |
| Suspected compromise | Rotate `FLASK_SECRET_KEY` (signs everyone out), reset passwords, review `audit_log`, restore from a known-good backup if data was altered. |

## 8. Routine maintenance

* Weekly: check backups completed; skim the Error Dashboard.
* Monthly: `pip-audit -r requirements.txt`; apply patch releases.
* After very large imports or deletions: `VACUUM (ANALYZE);`.
* Remove accounts of people who have left (User Management); sessions end
  immediately.
