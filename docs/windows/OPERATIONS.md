# Operating SYLTHARAE on Windows

## Start

```powershell
powershell -ExecutionPolicy Bypass -File `
  "<InstallDir>\app\offline-bundle\windows\start_syltharae.ps1"
```

The start script boots in a fixed order with no network access:

1. **Bundled PostgreSQL first** — `tools/windows/local_postgres.py
   --data-dir <DataDir>\pgdata --uri` starts the cluster from its files and
   prints the `DB_*` settings; the script exports them for the application.
   If the cluster does not come up, starting fails loudly — the application
   is never left running against a missing database.
2. **The application** — `run_web.py` in production mode (Waitress via the
   `.env`; `http://127.0.0.1:5000`).

Open `http://127.0.0.1:5000` in the browser and sign in with the
`APP_ADMIN_USERNAME` / `APP_ADMIN_PASSWORD` from the `.env`.

## Shutdown

Close the application console (or Ctrl-C it — Waitress stops cleanly), then:

```powershell
powershell -ExecutionPolicy Bypass -File `
  "<InstallDir>\app\offline-bundle\windows\stop_syltharae.ps1"
```

which stops the PostgreSQL cluster (`local_postgres.py --stop`). Stopping the
database cleanly matters: it is what makes restarts crash-free (see
[MAINTENANCE.md](MAINTENANCE.md)).

## Configuration

All runtime configuration lives in the `.env` next to the application
(`<InstallDir>\app\.env`). Key settings written by the installer:

| Setting | Meaning |
| --- | --- |
| `FLASK_ENV=production` | production mode |
| `WSGI_SERVER=waitress` | the production HTTP server |
| `TRUSTED_PROXY_COUNT=0` | no reverse proxy in front (local service) |
| `APP_DATA_DIR` | the data directory (`<DataDir>`) |
| `SECRET_KEY` | session signing key (generated at install; keep it) |
| `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` | the bundled PostgreSQL cluster |
| `TESSERACT_CMD` | path to `tesseract.exe` under `<InstallDir>\tools\tesseract` |
| `TESSDATA_PREFIX` | the language-data directory |
| `AUTO_INSTALL=0` | never attempt to download/install anything at startup |
| `APP_ADMIN_USERNAME` / `APP_ADMIN_PASSWORD` | the administrator account (preserved across upgrades) |

Restart the application after editing the `.env`. `AUTO_INSTALL=0` is part of
the offline contract: the application must never try to fetch or install
components at startup.

## Logging

Logs live under `<DataDir>\logs`. Error monitoring (`Hdg_Err_Ex_Log`) is
present but inert offline: the `enable_sentry` / `enable_rollbar` integrations
default to **false** and must stay unconfigured on disconnected machines. A
clean machine shows no ERROR lines after install and normal operation; if
ERROR lines appear, see [MAINTENANCE.md](MAINTENANCE.md) (troubleshooting).

## The bundled database

PostgreSQL runs as a plain process owned by the application — no Windows
service, no administrator rights, no network beyond loopback:

- Server binaries come from the `pgserver` wheel (PostgreSQL 16, win_amd64),
  unpacked inside the venv at install time.
- The cluster is initialized once (`--init`) into `<DataDir>\pgdata` and is
  private to this installation.
- `local_postgres.py` is the single entry point: `--init`, `--uri` (start and
  print the `DB_*` settings), `--status`, `--stop`, `--json`.
- The data directory is **never deleted by tooling** (`cleanup_mode=None`):
  recovery is explicit and manual (see [MAINTENANCE.md](MAINTENANCE.md)).

PostgreSQL is retained deliberately (not swapped for an embedded database):
the whole application is built on it, and bundling it as a private,
loopback-only process keeps that compatibility while removing every external
dependency. Any change away from PostgreSQL would require a complete
compatibility assessment first.

## OCR

OCR runs entirely on the machine:

| Engine | Binaries | Models/language data |
| --- | --- | --- |
| Tesseract 5.5.0 | `<InstallDir>\tools\tesseract` (from the bundle) | `eng`, `ara`, `heb` `.traineddata` copied from the bundle into its `tessdata` directory (`TESSDATA_PREFIX`) |
| RapidOCR (fallback) | `rapidocr-onnxruntime` wheel, already inside the venv | PP-OCRv4 models are bundled inside the wheel itself |

Neither engine downloads anything at runtime. Verify after install:

```bat
"<InstallDir>\.venv\Scripts\python.exe" tools\ci\ocr_selfcheck.py --require tesseract --languages heb,eng,ara
```

If a document requests a language whose `.traineddata` is not installed, the
application detects the missing language, reports it, classifies the failure
and continues with the remaining pipeline — it does **not** attempt a
download. Adding a language offline: obtain the `.traineddata` file by an
offline channel, drop it into `<InstallDir>\tools\tesseract\tessdata`, and
re-run the self-check.

Engine preference is `(Tesseract, RapidOCR)`; when Tesseract is unavailable
the RapidOCR fallback serves English-family text with documented accuracy
limits on Hebrew.
