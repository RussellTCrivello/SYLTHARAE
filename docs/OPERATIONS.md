# Operations

Running SYLTHARAE day to day: serving it in production, health checks,
logs, backups, upgrades, rollback and recovery. Installation is in
[INSTALL.md](INSTALL.md); every setting is in [CONFIGURATION.md](CONFIGURATION.md).

## 1. Production serving

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

`run_web.py` then prints `[OK] Serving with Waitress (32 threads)`. It
still runs `core.init` first (settings, resource coordinator, pending
migrations), so the start-up behaviour is identical to development. If
Waitress is selected but not installed, the server says so and falls back
to the built-in server rather than refusing to start.

Background jobs run on threads inside this one process, so run **one**
application process per database. Scale with `WAITRESS_THREADS` and the
processing worker settings, not with more processes.

Put a reverse proxy in front for TLS (nginx example in
[INSTALL.md Step 10](INSTALL.md#step-10---production-hardening)). Keep
`proxy_read_timeout` long (600 s) for large uploads and the job stream.

### Linux: systemd

```ini
# /etc/systemd/system/syltharae.service
[Unit]
Description=SYLTHARAE
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=syltharae
Group=syltharae
WorkingDirectory=/opt/syltharae
EnvironmentFile=/opt/syltharae/.env
Environment=APP_DATA_DIR=/var/lib/syltharae
ExecStart=/opt/syltharae/.venv/bin/python run_web.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now syltharae
journalctl -u syltharae -f
```

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
