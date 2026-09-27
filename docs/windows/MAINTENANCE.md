# Backups, upgrades, recovery, troubleshooting — Windows offline

## Backups

Everything that must survive is under `<DataDir>`:

- `<DataDir>\pgdata` — the database (documents, metadata, analysis, users)
- `<DataDir>\files` — imported source files
- `<DataDir>\backups` — application-produced backups/exports
- `<DataDir>\logs` — operational logs
- `<InstallDir>\app\.env` — configuration (admin credentials, secret key)

**Offline backup procedure** (stop the app and the database first so the
cluster is consistent):

```powershell
powershell -ExecutionPolicy Bypass -File `
  "<InstallDir>\app\offline-bundle\windows\stop_syltharae.ps1"
Copy-Item -Recurse <DataDir> D:\Backups\SYLTHARAE-2026-09-26
Copy-Item <InstallDir>\app\.env D:\Backups\SYLTHARAE-2026-09-26\.env
```

Restore: stop everything, replace `<DataDir>` with the backup copy, restore
the `.env`, then start again. A cold file copy of a stopped cluster is a
valid restore point for the bundled deployment.

## Offline upgrades (data survives)

Upgrades use the same bundle format as installation, over an existing one:

1. Back up `<DataDir>` and the `.env` (above).
2. Stop the application and the database.
3. Run the new bundle's `install.ps1` with the **same** `-InstallDir` and
   `-DataDir`.
4. The installer replaces application files, recreates the venv from the new
   wheelhouse, refreshes Tesseract/language data, **leaves `<DataDir>\pgdata`
   untouched** (step 5 of the install is idempotent: an existing cluster is
   reused, never deleted) and preserves the `.env`'s admin credentials.
5. Application-managed schema migrations run locally at startup against the
   existing cluster — no external migration service, no network.
6. Start, sign in, and confirm the previous documents are present.

If an upgrade must be rolled back, restore from the backup taken in step 1.

## Recovery

- **Application does not start** — start order matters: the bundled database
  must come up first; `start_syltharae.ps1` enforces this and fails loudly if
  it does not. Check `local_postgres.py --data-dir <DataDir>\pgdata --status`.
- **Database does not start** — inspect `<DataDir>\pgdata\log\*.log`.
  If the cluster is damaged beyond repair: restore the `<DataDir>\pgdata`
  backup (above). The tooling never deletes a data directory; any reset is a
  deliberate manual action on a backed-up system.
- **Interrupted upgrade** — the old `<DataDir>` was never touched; re-run the
  installer, or restore `<InstallDir>` from the previous bundle and start.
- **Power loss / hard crash** — start again with `start_syltharae.ps1`;
  PostgreSQL's WAL recovery runs on cluster start. Verify with the
  persistence step of the acceptance test
  ([OFFLINE_VALIDATION.md](OFFLINE_VALIDATION.md)).

## Troubleshooting

| Symptom | Check | Fix |
| --- | --- | --- |
| Install aborts at step 1 | a bundle file failed `Get-FileHash` | re-copy the bundle; never bypass the hash check |
| `pip check` fails at install | incomplete/corrupt wheelhouse | rebuild the bundle on the build machine; do not mix bundles |
| Login fails after upgrade | `.env` admin credentials | the installer preserves them; check `<InstallDir>\app\.env` |
| OCR returns nothing | `TESSERACT_CMD`, `TESSDATA_PREFIX` | re-run the OCR self-check (OPERATIONS.md); confirm the three `.traineddata` files exist |
| "Missing language data" on a document | language file absent | add the `.traineddata` offline (OPERATIONS.md) |
| Search finds nothing | indexing backlog | check the background jobs view; restart to resume processing |
| Page renders unstyled or hangs | a runtime resource tried to reach the network | this is a build defect — `tests/unit/test_no_remote_runtime_resources.py` must be failing on the release; report it, do not "fix" the machine |
| Unexpected outbound connection observed | see OFFLINE_VALIDATION.md | classify it; the acceptance run must end with zero external attempts |

Collect for a support request: the last 200 lines of `<DataDir>\logs`, the
output of `install.py --check`, and the OCR self-check JSON.
