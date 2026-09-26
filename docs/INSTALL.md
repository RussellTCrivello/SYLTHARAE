# Installation, build and run guide

This guide takes a machine from nothing to a running, verified SYLTHARAE
server. Steps are numbered because other parts of the product refer to them
(for example `scripts/reset_admin_password.py` points at Steps 7-8 and E11).

- [Requirements](#requirements)
- Steps 1-10: [prerequisites](#step-1---install-the-prerequisites) →
  [get the code](#step-2---get-the-code) → [virtual environment](#step-3---create-a-virtual-environment) →
  [dependencies](#step-4---install-the-python-dependencies) → [configure](#step-5---configure) →
  [start](#step-6---start-the-server) → [initial administrator](#step-7---the-initial-administrator) →
  [first sign-in](#step-8---first-sign-in) → [verify](#step-9---verify-the-installation) →
  [production hardening](#step-10---production-hardening)
- [Offline installation](#offline-installation-air-gapped-hosts)
- [Running the tests](#running-the-tests)
- [Troubleshooting E1-E12](#troubleshooting)

## Requirements

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.10 or newer (3.11 recommended) | `requires-python = ">=3.10"` in `pyproject.toml`. |
| PostgreSQL | 13 or newer (tested on 16) | Only `plpgsql` is needed; no extensions (the `pg_trgm` dependency was removed in migration 0004). |
| OS | Windows 10/11 or Linux | The `.bat` launchers are Windows-only; everything else is cross-platform. |
| Tesseract OCR | 5.x, optional | OCR for scanned PDFs and images. Without it the RapidOCR engine is used; with neither, OCR is reported as unavailable. |
| FFmpeg | optional | Needed by `moviepy` for video metadata and frames. |
| RAM / disk | 4 GB / 2 GB + your evidence | Extraction scratch space lives under the application data directory. |

## Step 1 - Install the prerequisites

**Windows:** install Python from python.org (tick *Add python.exe to PATH*),
PostgreSQL from the EnterpriseDB installer (remember the `postgres` password),
and optionally Tesseract (UB Mannheim build) and FFmpeg. Set `TESSERACT_CMD`
if Tesseract is not on `PATH`.

**Debian/Ubuntu:**

```bash
sudo apt-get install -y python3 python3-venv python3-dev build-essential \
    postgresql postgresql-contrib libpq-dev \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara tesseract-ocr-heb \
    ffmpeg libgl1 libglib2.0-0
```

`libgl1` is needed because `rapidocr-onnxruntime` pulls in the desktop
`opencv-python` wheel (see E9).

## Step 2 - Get the code

```bash
git clone https://github.com/RussellTCrivello/SYLTHARAE.git
cd SYLTHARAE
git checkout v2.2.0        # or the latest release tag
```

## Step 3 - Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
```

On Windows `setup.bat` does Steps 3-4 for you: it finds Python (`py -3` or
`python`), creates `.venv`, and installs from `wheels\` or
`offline-bundle\wheels\` without network access when those exist.

## Step 4 - Install the Python dependencies

```bash
pip install -r requirements.txt
```

Every line in `requirements.txt` is tagged with the platform concern it has
(`[WIN-BINARY]`, `[WIN-NATIVE]`, ...). The same dependencies are available as
extras for a package-style install:

```bash
pip install -e ".[pdf,office,ocr,ebook,audio,media]"      # add ,dev for tooling
```

Notes:

* **PST/OST mailboxes** need `libpff-python`, which compiles C code and fails
  on most hosts without libpff headers. It is therefore commented out of the
  requirements and offered as the `pst` extra: `pip install -e ".[pst]"`.
  Without it, a PST file is recorded with the error "pypff package not
  installed" instead of stopping the run.
* **Verified clean install** (Python 3.11, Linux): `pip install -r
  requirements.txt` resolves in under a minute with `pip check` clean,
  selecting `Pillow 12.3`, `pypdf 6.x` and `moviepy 1.0.3`.
* **moviepy and Pillow:** Pillow is pinned to `>=12.3` for security fixes.
  `moviepy 2.2.x` declares `pillow<12`, so pip resolves to `moviepy 1.0.3` on
  a fresh install. Both work: the video reader imports either API
  (`reader_file/readers/read_video.py`). An upgrade from an older environment
  can keep `moviepy 2.2.1` next to Pillow 12.3; pip prints a conflict warning
  that is safe to ignore for the video-reading paths SYLTHARAE uses.
* **Headless servers:** if `import rapidocr_onnxruntime` fails with
  `libGL.so.1`, either install `libgl1` or replace the desktop wheel:
  `pip uninstall -y opencv-python && pip install --force-reinstall --no-deps opencv-python-headless`.
  `pip check` then reports that `rapidocr-onnxruntime requires opencv-python`:
  expected, since the headless wheel provides the same `cv2` module.

## Step 5 - Configure

Configuration is resolved in this order (first wins):

1. OS environment variables
2. `.env` in the project root (loaded at startup, never overrides 1)
3. `data/settings.json` - the runtime settings store edited from the Settings page
4. `config.example.json` - project defaults
5. dataclass defaults in `settings/`

You can either let the web setup wizard write `.env` (Step 6) or create it
yourself from the template:

```bash
cp .env.example .env
```

The settings that matter on day one:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | `localhost`, `5432`, `analysis`, `postgres`, empty | PostgreSQL connection. The database is created if it does not exist. |
| `FLASK_SECRET_KEY` | generated into `.flask_secret_key` | Signs sessions and CSRF tokens. Set a long random value in production. |
| `FLASK_ENV` | `production` | `production` refuses debug mode, marks cookies `Secure` and enables HSTS on HTTPS requests. Use `development` for local work over plain HTTP. |
| `FLASK_HOST`, `FLASK_PORT` | `0.0.0.0`, `5000` | Listening address. |
| `TRUSTED_PROXY_COUNT` | `0` | Set to the number of reverse proxies in front of the app (see Step 10). |
| `APP_DATA_DIR` | `~/.local/share/file-analysis` (Windows: `%LOCALAPPDATA%\file-analysis`) | Logs, checkpoints, extraction scratch, uploads, cache, runtime state. |
| `APP_ADMIN_USERNAME`, `APP_ADMIN_PASSWORD` | `admin`, generated | Initial administrator (Step 7). |
| `INGESTION_ROOTS` | unset | Server-side folders the Input page may ingest from (path-safety allow-list). |
| `TESSERACT_CMD` | `tesseract` on `PATH` | Tesseract binary. |
| `COMPUTE_MODE` | `auto` | `cpu`, `gpu`, `cpu+gpu` or `auto`. |

Security tuning (`SECURITY_MAX_FAILED_LOGINS=5`, `SECURITY_LOCKOUT_MINUTES=15`,
`SECURITY_SESSION_HOURS=12`, `SECURITY_SESSION_IDLE_HOURS=6`,
`PASSWORD_MIN_LENGTH=12`, `RATE_LIMIT_PER_MINUTE=60`, `RATE_LIMIT_PER_HOUR=600`,
`RATELIMIT_STORAGE_URI`) is described in [SECURITY.md](SECURITY.md).
`.env.example` documents every variable.

## Step 6 - Start the server

```bash
python run_web.py          # Windows: start.bat
```

`run_web.py`:

1. installs missing requirements from a local `wheels/` directory when one is
   present (disable with `AUTO_INSTALL=0`);
2. runs `core.init` - settings, the resource coordinator, and an idempotent
   schema upgrade (all pending migrations) when the database is reachable;
3. refuses to start with `FLASK_DEBUG=true` while `FLASK_ENV=production`;
4. serves on `FLASK_HOST:FLASK_PORT` (default `0.0.0.0:5000`) - with
   Flask's built-in server by default, which is fine for development and a
   single workstation, or with **Waitress** when `WSGI_SERVER=waitress`
   (the production setting; see Step 10 and [OPERATIONS.md](OPERATIONS.md)).

Open `http://127.0.0.1:5000`. On a fresh installation every page redirects
to the **setup wizard** at `/setup`, which:

* checks the system (Python, packages, Tesseract, disk);
* tests the database connection you enter;
* runs the installation: **A** writes `.env`, **B** creates the database and
  applies migrations `m0001`-`m0014`, **C** creates the administrator,
  **D** creates the runtime directories, **E** writes the
  `.system_initialized` marker.

After step E the wizard closes for good: `/setup` redirects home, the
install endpoint answers 409, and the diagnostics are admin-only.

**Headless alternative:** `python install.py --non-interactive` performs the
same sequence from environment variables (`install.py --check`, `--verify`
and `--configure` are also available).

## Step 7 - The initial administrator

There are three ways the first administrator is created, and exactly one
applies:

1. **Setup wizard** - the username and password you typed in Step 6.
2. **`APP_ADMIN_PASSWORD` set** - on first start with an empty `users` table,
   the server creates `APP_ADMIN_USERNAME` (default `admin`) with that password.
3. **Neither** - the server generates a random password, creates `admin` with
   *must change password* set, and writes the credentials to
   `APP_DATA_DIR/runtime/initial_admin_password.txt` (mode 0600). The log
   says so. Retrieve it, sign in and change the password; the file is
   deleted automatically on the next server start once any account exists.

If the database has no users and none of these ran, `/auth/first-admin`
offers a one-time form that works only while zero users exist.

## Step 8 - First sign-in

Sign in at `/auth/login`. An account created by path 3 must change its
password before anything else is reachable. Then create the other accounts
under **Administration → User Management** (`g u`) and give each the narrowest role:

| Role | Can |
| --- | --- |
| `viewer` | read everything a normal user sees (no settings, no diagnostics) |
| `analyst` | everything a viewer can, plus ingest, import, categorise, edit |
| `admin` | everything, including settings, users, backups and diagnostics |

## Step 9 - Verify the installation

```bash
python verify_readiness.py          # human-readable
python verify_readiness.py --json   # for CI
```

It checks behaviour, not the presence of methods: environment and
executables; database connectivity, migration state, tables and indexes;
that the application builds and refuses anonymous requests; a
representative ingestion, storage and search; injection defences, archive
safety and debug-mode protection; rollback and retry wiring; and deployment
settings. Exit code 0 means every critical check passed.

A quick manual smoke test: `GET /health` returns JSON, `/` redirects an
anonymous browser to the login page, and ingesting a small folder from the
**Input** page produces a job that reaches *completed*.

## Step 10 - Production hardening

* Keep `FLASK_ENV=production` and `FLASK_DEBUG=false`, and set a long
  random `FLASK_SECRET_KEY`.
* **Use the production server:** `pip install -e ".[server]"` and set
  `WSGI_SERVER=waitress` (optionally `WAITRESS_THREADS`, default 32). Do not
  run production on the built-in development server. Run it as a service
  (systemd or NSSM examples in [OPERATIONS.md](OPERATIONS.md#1-production-serving)).
* **Serve over HTTPS.** Production marks the session cookie `Secure`, so a
  browser will not send it back over plain HTTP to any host other than
  `localhost`: sign-in appears to succeed and then bounces back to the login
  page (E5). Put a TLS-terminating reverse proxy in front of the app and set
  `TRUSTED_PROXY_COUNT=1`, so client addresses (rate limits, lockouts, audit
  log) and HTTPS detection (HSTS) are correct. Never set it on a directly
  exposed server - clients could then forge their address.
* For a closed LAN without TLS, use `FLASK_ENV=staging` and accept that
  session cookies travel in clear text.
* Run one server process per database (background jobs run inside it), so
  the default in-memory rate-limit counters are shared by every request.
  `RATELIMIT_STORAGE_URI` (Redis or Memcached) exists for other layouts.
* Give the database role only the privileges on its own database, and back
  up both PostgreSQL (`pg_dump`) and `APP_DATA_DIR`. See
  [OPERATIONS.md](OPERATIONS.md).

**Reverse proxy and service files ship with the release**:
[`deploy/nginx/syltharae.conf`](../deploy/nginx/syltharae.conf) (HTTPS with
HTTP/2, HTTP→HTTPS redirect, the proxy headers the application trusts, 2 GB
uploads) and [`deploy/systemd/syltharae.service`](../deploy/systemd/syltharae.service).
Install them as described in
[OPERATIONS.md](OPERATIONS.md#reverse-proxy-and-tls), then run the
[deployment verification](OPERATIONS.md#deployment-verification). If you
write your own proxy configuration, it must *set* `X-Forwarded-For` to
`$remote_addr`: appending with `$proxy_add_x_forwarded_for` passes on
whatever address the client claims.

## Offline installation (air-gapped hosts)

`offline-bundle/` holds `requirements.offline.txt` and a `wheels/` directory
for installing without network access.

1. On a connected machine with the **same OS, architecture and Python
   version** as the target:
   `pip download -r offline-bundle/requirements.offline.txt -d offline-bundle/wheels`
2. Copy the repository, including `offline-bundle/wheels/`, to the target.
3. Run `setup.bat` (Windows) or
   `pip install --no-index --find-links offline-bundle/wheels -r offline-bundle/requirements.offline.txt`.
4. Continue at Step 5.

## Running the tests

The suite starts its own throw-away PostgreSQL server through `pgserver`, so
it needs no database configuration:

```bash
pip install -e ".[dev]"          # pytest, pgserver, ruff, bandit
python -m pytest tests/unit                  # fast, mostly without a database
python -m pytest tests/integration tests/security
```

Documentation is tested too; after changing code that feeds a generated
document, regenerate it as described in [README.md](../README.md#generated-documents).

## Troubleshooting

**E1 - `Cannot connect to PostgreSQL server`.** The server is not running,
or `DB_HOST`/`DB_PORT` is wrong. `pg_isready -h HOST -p PORT` must answer
*accepting connections*. On Windows check the *postgresql-x64-NN* service.

**E2 - `password authentication failed for user`.** `DB_USER`/`DB_PASSWORD`
do not match; remember the OS environment beats `.env` (Step 5). The wizard's
*Test connection* button shows the exact server message.

**E3 - `permission denied to create database`.** The configured role may not
`CREATE DATABASE`. Create it once as a superuser -
`createdb -O <role> analysis` - and run the installer again; it continues
with the existing database.

**E4 - Every page redirects to `/setup`.** The installation did not finish:
the `.system_initialized` marker or the core tables are missing. Finish the
wizard, or run `python install.py --verify` to see which part is missing.

**E5 - Sign-in succeeds, then returns to the login page.** The session cookie
is not coming back: `FLASK_ENV=production` over plain HTTP on a non-localhost
address. Serve over HTTPS (Step 10) or use `FLASK_ENV=staging` on a trusted LAN.

**E6 - `The CSRF token is missing` / `400 Bad Request` on a form.** The page
was open across a server restart with a new `FLASK_SECRET_KEY`, or a proxy
strips cookies. Reload the page; set a fixed `FLASK_SECRET_KEY`.
*Every* POST failing with `CSRF token is missing or invalid` right after you
put the app behind HTTPS usually means the host check failed. Over HTTPS
Flask-WTF also requires the browser's `Referer` to match the host the
application sees, so the proxy must pass the public host
(`proxy_set_header Host $host;` and `X-Forwarded-Host`, as in the Step 10
example), `TRUSTED_PROXY_COUNT` must be set, and no proxy or browser
extension may strip `Referer`. (Verified: a proxy that drops the forwarded
host causes exactly this error, and a cross-site `Referer` is rejected.)

**E7 - `429 Too Many Requests`.** The per-client rate limit. Behind a proxy
without `TRUSTED_PROXY_COUNT` every user shares one address and one limit -
set it (Step 10). Otherwise raise `RATE_LIMIT_PER_MINUTE`/`_PER_HOUR`.

**E8 - `Account locked`.** Too many failed sign-ins
(`SECURITY_MAX_FAILED_LOGINS`); it unlocks by itself after
`SECURITY_LOCKOUT_MINUTES`. For an administrator who cannot wait, use E11.

**E9 - OCR unavailable / `libGL.so.1: cannot open shared object file`.**
Install Tesseract (and set `TESSERACT_CMD`), or fix RapidOCR's OpenCV
dependency as described under Step 4.

**E10 - PDF previews or video metadata missing.** Install `pypdf` and
PyMuPDF (`.[pdf]`) and FFmpeg; `python verify_readiness.py` lists the
readers that could not load.

**E11 - Locked out of the administrator account / lost the admin password.**
Stop nothing - the server may keep running. On the server:

```bash
python scripts/reset_admin_password.py --list                 # see the accounts
python scripts/reset_admin_password.py                        # reset 'admin' to a one-time password
python scripts/reset_admin_password.py --username alice --promote --activate
```

(`reset_password.bat` on Windows.) The one-time password is never printed:
it is written to `APP_DATA_DIR/runtime/recovery_admin_password.txt` (mode
0600) unless you pass `--password`. The account's sessions are revoked, its
lockout counters cleared, *must change password* is set, and the action is
written to the audit log (`user.password_recovery`). The script refuses non-admin targets unless `--promote` is given and
inactive ones unless `--activate` is given. If no accounts exist at all it
changes nothing - start the server and follow Step 7.

**E12 - `pip` fails building `libpff-python`, `psycopg2` or `PyMuPDF`.**
`libpff-python` is optional (Step 4). For the others use a Python version
with published wheels (3.10-3.12 are safest on Windows) or install the build
dependencies (`build-essential`, `libpq-dev`).
