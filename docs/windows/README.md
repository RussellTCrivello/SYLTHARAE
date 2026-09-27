# SYLTHARAE for native Windows — fully offline

SYLTHARAE on Windows is a first-class native deployment, not a ported Linux
one: no WSL, no Docker, no Linux subsystem, no nginx, no Unix shell, no cloud
services and no network access — neither at installation nor at runtime. After
the offline install every core function works with the machine disconnected:
startup, the local web interface, authentication, database operations,
ingestion (DOCX/PDF/XLSX/images and more), OCR with English, Hebrew and
Arabic, search, indexing, analysis, categorization, filtering, metadata,
exports, reporting, background processing, configuration, logging, restart and
recovery.

## Documents in this set

| Document | Topics |
| --- | --- |
| [README.md](README.md) | this overview, system requirements, the offline principle |
| [INSTALL.md](INSTALL.md) | building the offline bundle, installing, directory layout, uninstall |
| [OPERATIONS.md](OPERATIONS.md) | start/shutdown, configuration, logging, the bundled database, OCR and language data |
| [MAINTENANCE.md](MAINTENANCE.md) | backups, offline upgrades (data survives), recovery, troubleshooting |
| [OFFLINE_VALIDATION.md](OFFLINE_VALIDATION.md) | the offline acceptance test and the release gate |
| [DEPENDENCY_INVENTORY.md](DEPENDENCY_INVENTORY.md) | every runtime dependency, classified (generated, deterministic) |

## System requirements

| Component | Requirement |
| --- | --- |
| Operating system | Windows 10 22H2 / Windows 11 / Windows Server 2019 or newer, 64-bit |
| CPU | x86-64; 4 cores recommended (OCR and PDF rendering are CPU-bound) |
| Memory | 8 GB minimum, 16 GB recommended |
| Disk | 12 GB free for the installation plus space for imported files, the database, indexes and backups |
| Display/browser | any current Chromium- or Firefox-based browser (rendered assets are served locally; no CDN is contacted) |
| Network | **none required** — install and run fully offline; outbound connections are opt-in and off by default (see below) |
| Privileges | a per-user install needs no administrator rights (default paths are under `%LOCALAPPDATA%`); installing system-wide Python/Tesseract under `C:\Program Files` does |

## The offline principle, enforced

- **Nothing is downloaded after install.** The Python packages, the embedded
  PostgreSQL binaries (via the `pgserver` wheel), Tesseract, the OCR language
  data (`eng`/`ara`/`heb`) and all application assets are delivered inside the
  offline bundle and verified by SHA-256 before use. No engine silently
  downloads language packs or models at runtime.
- **No external services.** No CDN scripts/styles/fonts, no remote APIs, no
  cloud OCR, no remote AI models, no external authentication, no telemetry
  phones home. `tests/unit/test_no_remote_runtime_resources.py` fails the
  build if a template or static asset ever points at the network.
- **Telemetry is opt-in and off.** The error-monitoring integrations
  (`enable_sentry`, `enable_rollbar` in `Hdg_Err_Ex_Log/error_monitoring.py`)
  default to disabled; leave them unconfigured on offline machines.
- **Documentation links are not runtime dependencies.** Links to project
  documentation and source may point at GitHub; the interface never fetches
  them.
- **The release gate.** The version may not be declared offline-ready until
  the disconnected-machine acceptance test in
  [OFFLINE_VALIDATION.md](OFFLINE_VALIDATION.md) passes end to end.
