# Installing SYLTHARAE on Windows — fully offline

Installation happens in two phases, both offline on their target machines:
a **connected build machine** assembles the offline bundle, and the
**disconnected target machine** installs from it.

## 1. Build the offline bundle (connected machine)

Run from a checkout of the release tag:

```bat
python tools\windows\build_offline_bundle.py --output SYLTHARAE-offline-win64
```

The builder stages, into the output directory:

| Stage | Contents |
| --- | --- |
| `python/` | the pinned CPython 3.11 Windows installer (`python-3.11.9-amd64.exe`) |
| `wheels/` | the full win_amd64 wheelhouse — base requirements, runtime extras (`pdf,office,ocr,ebook,audio,media,server`), `pip/setuptools/wheel`; development-only packages (pytest, pytest-cov, ruff, bandit) are excluded |
| `tesseract/` | the pinned Tesseract 5.5.0 Windows build (UB-Mannheim `tesseract-ocr-w64-setup-5.5.0.20241111.exe`) |
| `tessdata/` | `eng.traineddata`, `ara.traineddata`, `heb.traineddata` — byte-identical to `offline-bundle/windows/tessdata/` in the repository, covered by `MANIFEST.sha256` |
| `app-source.zip` | `git archive` of the release commit (application, templates, static assets, tools) |
| `manifest.json` | SHA-256 of every staged file; `--verify` re-checks it offline |

`--plan` stages what is already local (tessdata, app archive) and records the
remaining component URLs and hashes without network access; until a pinned
artifact has been fetched once its hash is `null` and the builder prints
`PIN THIS HASH` — record the hash of the artifact you downloaded, then rebuild.
The builder refuses mismatched artifacts.

The database needs no separate download: `pgserver` (already part of the
wheelhouse) bundles the PostgreSQL 16 server binaries for Windows inside its
wheel. `pgserver==0.1.4` is verified on the build machine with
`pip download --platform win_amd64` before pinning.

Copy the whole output directory to the target machine (USB drive, sealed
share — any offline channel).

## 2. Install (disconnected target machine)

In a PowerShell on the target machine:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 `
    -BundleDir D:\SYLTHARAE-offline-win64
```

`offline-bundle/windows/install.ps1` performs, in order:

1. **Verify the bundle** — every file in `manifest.json` is re-hashed
   (`Get-FileHash`); a mismatch aborts the install.
2. **Python 3.11** — silently installs the bundled CPython into
   `<InstallDir>\runtime` (skipped when a suitable Python 3.11 already exists).
3. **Application environment** — creates `<InstallDir>\.venv` and installs the
   wheelhouse with `pip install --no-index --find-links <bundle>\wheels`
   (base requirements, then runtime extras, then the pinned runtime pins),
   followed by `pip check` — all from local wheels, no index. The application
   source is unpacked from `app-source.zip` into `<InstallDir>\app`.
4. **Tesseract + language data** — installs Tesseract silently to
   `<InstallDir>\tools\tesseract` and copies the three `.traineddata` files
   from the bundle.
5. **Database** — initializes the private PostgreSQL cluster at
   `<DataDir>\pgdata` via `tools/windows/local_postgres.py --init` (idempotent;
   never deletes an existing cluster).
6. **Configuration** — writes the production `.env` next to the application
   (`FLASK_ENV=production`, `WSGI_SERVER=waitress`, `TRUSTED_PROXY_COUNT=0`,
   `APP_DATA_DIR`, generated `SECRET_KEY`, the `DB_*` values of the bundled
   cluster, `TESSERACT_CMD`, `TESSDATA_PREFIX`, `AUTO_INSTALL=0`). An existing
   `.env`'s `APP_ADMIN_USERNAME`/`APP_ADMIN_PASSWORD` are preserved across
   upgrades.
7. **Check** — runs `install.py --check`, which must pass before the installer
   reports success.

The installation itself makes no network request: every component comes from
the bundle and is hash-verified first.

Parameters: `-InstallDir` (default `%LOCALAPPDATA%\Programs\SYLTHARAE`) and
`-DataDir` (default `%LOCALAPPDATA%\SYLTHARAE`).

## 3. Directory layout

| Path | Contents | Survives upgrades? |
| --- | --- | --- |
| `<InstallDir>\app\` | application source, templates, static assets, tools | replaced |
| `<InstallDir>\.venv\` | the application's Python environment | replaced |
| `<InstallDir>\runtime\` | the bundled CPython 3.11 | kept (reused) |
| `<InstallDir>\tools\tesseract\` | Tesseract engine + language data | replaced |
| `<DataDir>\pgdata\` | the bundled PostgreSQL cluster (all imported data) | kept |
| `<DataDir>\files\`, `logs\`, `backups\` | imported files, logs, backups | kept |

Application files and user data are strictly separated: an upgrade replaces
`<InstallDir>` and never touches `<DataDir>`. The one file inside the
application tree that persists is `<InstallDir>\app\.env` — the installer
re-reads its admin credentials before rewriting it.

## 4. Uninstall

1. Stop the application and the database (`stop_syltharae.ps1`; see
   [OPERATIONS.md](OPERATIONS.md)).
2. Delete `<InstallDir>` — removes the application, venv and Tesseract.
3. Delete `<DataDir>` **only if** you do not want the data. It contains the
   database cluster, imported files, logs and backups; deleting it is
   irreversible. Back it up first (see [MAINTENANCE.md](MAINTENANCE.md)).

No registry keys, services or PATH entries are created by the default
per-user install; uninstalling is removing those directories.
