# Offline validation and the Windows release gate

A release may be called offline-ready **only** after the full acceptance test
has passed on a disconnected Windows machine with the exact release bundle.
Anything less is not offline-ready.

## Preparation (connected machine)

1. Build the bundle (`build_offline_bundle.py`, see [INSTALL.md](INSTALL.md))
   from the release commit and `--verify` it.
2. Obtain a Windows target machine that has **no network connection** (air-
   gapped, or network disabled after the bundle is transferred).

## Acceptance test (disconnected machine)

Install per [INSTALL.md](INSTALL.md), then start the application
([OPERATIONS.md](OPERATIONS.md)) and run:

```powershell
powershell -ExecutionPolicy Bypass -File `
  "<InstallDir>\app\tools\windows\offline_acceptance.py" `
  --base-url http://127.0.0.1:5000 `
  --admin-password <the APP_ADMIN_PASSWORD from the .env> `
  --pause-for-restart
```

(`--restart-command` can automate the restart step; `--no-monitor` disables
the network monitor only for debugging the test itself — never for a release
sign-off.)

The tool executes the thirteen directive steps, in order, recording evidence
into `acceptance-evidence/` and `acceptance-report.json`:

1. **Start** — the application and bundled database come up; `/health` is OK.
2. **Interface** — the local web UI renders over loopback.
3. **Authentication** — sign-in works.
4. **Ingest** — TXT, DOCX, PDF, XLSX and PNG fixtures are imported.
5. **Extraction** — text extraction per format; the ingested content is
   searchable.
6. **OCR** — the PNG is processed through the local OCR stack with provenance
   recorded (`derived` content, engine ∈ {tesseract, rapidocr}, confidence in
   (0, 1]).
7. **Search** — keyword search finds each ingested document.
8. **Search + filter** — combined queries behave.
9. **Analysis** — the analysis endpoints respond over the ingested corpus.
10. **Export** — CSV and settings exports are produced.
11. **Restart** — the application is stopped and started again (`--pause-for-
    restart` waits for the operator, `--restart-command` runs a command).
12. **Re-authentication** — sign-in works after the restart.
13. **Persistence + repeat** — previously ingested documents are still there
    (id sets compared), and the critical operations are repeated
    successfully on the restarted system.

During the whole run a network monitor samples the process tree's
connections once per second and classifies every endpoint: loopback,
unspecified, link-local and multicast addresses are local; anything else is
**external**.

## Pass criteria — all of them

- All thirteen steps pass.
- **Zero** outbound connection attempts to non-local endpoints were observed.
  Any single external attempt fails the run, is listed in
  `acceptance-report.json`, and must be eliminated (code or configuration
  defect) before the release.
- The OCR self-check passes for `heb,eng,ara` on the installed Tesseract.
- No `ERROR` lines accumulate in `<DataDir>\logs` during the run.

## The release gate

The Windows release is declared offline-ready only when **all** of the
following hold on the release bundle:

- clean offline **install** (hash-verified, `--no-index`, `pip check` clean);
- clean **start** (database first) and **UI/auth/DB** operation;
- **OCR** with local engines and language data, no silent downloads;
- **local models** only (PP-OCRv4 in-wheel, `.traineddata` from the bundle);
- **ingestion → search → analysis → export** all function offline;
- **restart & recovery** with data intact (acceptance steps 11–13);
- **no external services**, no unexpected network dependency (monitor clean);
- **offline upgrade** from the previous release with data survival;
- the full acceptance test above passes on a disconnected machine.

Repository-side regression protection (runs in CI, on every change):
`tests/unit/test_no_remote_runtime_resources.py` and
`tests/unit/test_windows_offline_kit.py`.
