# Configuration reference

SYLTHARAE has two configuration layers:

| Layer | Where | Holds | Changed by |
|---|---|---|---|
| **Environment** | `.env` in the project root (loaded by `core/init.py` with `override=False`, so real environment variables win), or the process environment | Secrets, database connection, bind address, security policy, server choice, paths | Editing `.env` / the service definition, then restarting |
| **Runtime settings** | `data/settings.json` (backups in `data/settings_backups/`) | Processing (workers, timeouts, chunk sizes), analysis options, compute policy, database pool size, UI preferences | The **Settings** and **Operations** pages (admin), or the file itself |

The setup wizard writes `.env` on first run (`core/installer.py`) and generates
`FLASK_SECRET_KEY`. `.env.example` is the annotated template. Never commit
`.env`: it holds the database password and the secret key.

The tables below list **every variable the code reads**, taken from the source
(grep for `os.environ` / `os.getenv` / `_env_*`). Defaults are the values the
code uses when the variable is unset.

## Application and server

| Variable | Default | Effect |
|---|---|---|
| `FLASK_ENV` | `production` | `production` enables secure cookies, HSTS on HTTPS requests and production error pages. Use `development` only on a trusted workstation. |
| `FLASK_DEBUG` | `False` | Werkzeug debugger and reloader. **Never enable in production**, because it allows code execution. It also forces the built-in server. |
| `FLASK_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a reverse proxy on the same host. |
| `FLASK_PORT` | `5000` | Bind port. |
| `FLASK_SECRET_KEY` | none (the wizard generates one) | Signs sessions and CSRF tokens. Changing it logs everyone out. |
| `WSGI_SERVER` | `flask` | `flask` is the built-in development server. `waitress` is the production server and needs `pip install -e ".[server]"`. If Waitress is missing, the app logs a warning and falls back. See [OPERATIONS.md](OPERATIONS.md). |
| `WAITRESS_THREADS` | `32` (minimum `4`) | Worker threads when `WSGI_SERVER=waitress`. SSE job streams hold a thread each. |
| `TRUSTED_PROXY_COUNT` | `0` | How many reverse proxies to trust for `X-Forwarded-For/Proto/Host`. `0` ignores the headers. Set it to `1` behind a single nginx/IIS/Caddy that **overwrites** those headers. This is the only place proxy trust is decided. |
| `AUTO_INSTALL` | `1` | When `1`, `run_web.py` tries to install missing core dependencies from `wheels/` (inside a venv only). Set it to `0` on managed hosts. |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Invalid values fall back to `INFO`. |

## Database

| Variable | Default | Effect |
|---|---|---|
| `DB_HOST` | `localhost` | PostgreSQL host, or a Unix-socket directory. |
| `DB_PORT` | `5432` | |
| `DB_NAME` | `analysis` | |
| `DB_USER` | `postgres` | Prefer a dedicated role that owns the database (see [INSTALL.md](INSTALL.md)). |
| `DB_PASSWORD` | empty | The wizard rejects an empty password. |
| `DB_TEST_CONNECT_TIMEOUT` | `5` | Seconds allowed for connection tests (wizard and Settings → Database). |
| `DB_POOL_MIN_CONNECTIONS` / `DB_MIN_CONNECTIONS` | `1` | Pool minimum, used **only when the settings store is unavailable**. Normally the pool size comes from Settings → Database and is tuned by the resource coordinator. Both names are accepted; the `DB_*` form wins. |
| `DB_POOL_MAX_CONNECTIONS` / `DB_MAX_CONNECTIONS` | `20` | Pool maximum; same rules as above. |

## Security policy

| Variable | Default | Effect |
|---|---|---|
| `APP_ADMIN_USERNAME` | `admin` | First administrator, created by the wizard / bootstrap. |
| `APP_ADMIN_PASSWORD` | empty | Used once, to create that account. Remove it from `.env` afterwards. |
| `PASSWORD_MIN_LENGTH` | `12` | Minimum length for new passwords. |
| `SECURITY_MAX_FAILED_LOGINS` | `5` | Failed attempts before the account is locked. |
| `SECURITY_LOCKOUT_MINUTES` | `15` | Lockout duration. |
| `SECURITY_SESSION_HOURS` | `12` | Absolute session lifetime. |
| `SECURITY_SESSION_IDLE_HOURS` | `6` | Idle timeout. |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_PER_HOUR` | `60` / `600` | Default per-client API limits. Some endpoints are stricter: login is 10/min, and source/side creation is 20/min. |
| `RATELIMIT_STORAGE_URI` | `memory://` | Flask-Limiter storage. Memory is per process, which is correct for a single Waitress process. Use `redis://…` if you run several processes. |

See [SECURITY.md](SECURITY.md) for the model these settings feed.

## Ingestion, uploads and paths

| Variable | Default | Effect |
|---|---|---|
| `INGESTION_ROOTS` | empty (server-path ingestion disabled) | Allowlist of directories the server may read, separated by `;`. Any path outside them is rejected, after resolving symlinks. Browser uploads work either way. |
| `APP_DATA_DIR` | `%LOCALAPPDATA%\file-analysis` (Windows) or `$XDG_DATA_HOME/file-analysis`, falling back to `~/.local/share/file-analysis` (POSIX) | Root for `logs/`, `checkpoints/`, `extracted/`, `uploads/`, `cache/`, `runtime/` and `config/`. Put it on a disk with room for extracted archives. |
| `EXTRACTION_FOLDER` | `APP_DATA_DIR/extracted` | Overrides where archives are unpacked. |
| `OPERATIONS_MAX_UPLOAD_MB` | `2048` | Largest single-request upload. |
| `OPERATIONS_MAX_CHUNKED_UPLOAD_MB` | `20480` | Largest chunked upload (reassembled size). |
| `OPERATIONS_UPLOAD_CHUNK_MB` | `8` | Chunk size offered to the browser. |
| `OPERATIONS_MAX_STAGED_PATH_CHARS` | `240` on Windows, `4096` elsewhere | Longest relative path accepted for staged uploads. |
| `TESSERACT_CMD` | autodetect | Path to `tesseract` if it is not on `PATH`. It is optional: RapidOCR is used when available. |

## Jobs and compute

| Variable | Default | Effect |
|---|---|---|
| `JOBS_MAX_CONCURRENT` | `2` (minimum 1) | Jobs that run at the same time; further jobs queue. |
| `JOBS_STALE_SECONDS` | `21600` (minimum 60) | A running job with no heartbeat for this long is marked `FAILED` at startup, so it can be retried. |
| `JOBS_PROGRESS_INTERVAL` | `1.0` (minimum 0.2) | Seconds between progress heartbeats. |
| `JOBS_EVENT_SAMPLE_THRESHOLD` | `500` | Above this many files, per-file job events are sampled. |
| `COMPUTE_GATEWAY` | `1` | `0` bypasses the compute gateway. Use this for diagnostics only. |
| `COMPUTE_MODE` | from settings | `cpu`, `gpu`, `cpu+gpu` or `auto`. It overrides the Operations page. |
| `COMPUTE_MAX_CONCURRENCY`, `COMPUTE_RESERVED_CORES`, `COMPUTE_QUEUE_DEPTH`, `COMPUTE_MEMORY_BUDGET_MB`, `COMPUTE_LATENCY_BUDGET_S` | from settings | Override the matching gateway limits. Invalid values are logged and ignored. |
| `COMPUTE_ISOLATION` | from settings | `0` or `false` disables process isolation for heavy tasks. |
| `COMPUTE_GPU_FALLBACK` | `0` | Allow falling back to the CPU when GPU-only mode has no GPU. |
| `COMPUTE_ADAPTIVE_SCHEDULING` | `0` | Experimental adaptive read scheduling. |

## Error monitoring (optional)

| Variable | Default | Effect |
|---|---|---|
| `SENTRY_ENABLED`, `SENTRY_DSN` | `false`, none | Send errors to Sentry. Requires `sentry-sdk`. |
| `ROLLBAR_ENABLED`, `ROLLBAR_TOKEN` | `false`, none | Send errors to Rollbar. Requires `rollbar`. |

## Development and test only

| Variable | Effect |
|---|---|
| `INFORAXIS_WRITE_EVIDENCE` | `1` regenerates the committed interface-coverage evidence during the test run. |
| `REPRO_REPO` | Repository path used by `tools/` reproducibility scripts. |
| `NO_COLOR`, `ANSICON` | Console colour detection. |
| `PROJECT_ROOT` | Set internally at startup; do not set it by hand. |

## Reserved variables (written, not read)

The installer writes these, and `.env.example` lists them, but **no code reads
them**. Changing them has no effect. Use the Settings page instead:

| Variable | Use instead |
|---|---|
| `MAX_WORKERS`, `FILE_CHUNK_SIZE`, `FILE_PROCESSING_TIMEOUT` | Settings → Processing |
| `DB_POOL_TIMEOUT`, `DB_QUERY_TIMEOUT` | none (no timeout is applied from the environment) |
| `ACTION_LOGGING_ENABLED`, `PERFORMANCE_MONITORING` | none (the audit log and monitoring are always on) |

Before v2.1.1, `LOG_LEVEL` and the `DB_POOL_*_CONNECTIONS` names were silently
ignored as well. Finding AUDIT-CONF-01 in [AUDIT_REPORT.md](../AUDIT_REPORT.md)
covers this; they are now read as described above.
