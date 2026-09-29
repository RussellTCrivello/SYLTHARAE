# Development guide

## Set up

Requirements: Python 3.10+ (3.11 is what CI-equivalent runs use), PostgreSQL
14+ or `pgserver`, Git, and optionally Node.js 18+ for the front-end tests.

    git clone https://github.com/RussellTCrivello/SYLTHARAE.git
    cd SYLTHARAE
    python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    pip install -e ".[dev,server]" pgserver
    cp .env.example .env                              # or let the setup wizard write it

Run the app in development mode on the built-in server:

    FLASK_ENV=development FLASK_DEBUG=true python run_web.py

The first request opens the setup wizard (`/setup`). For a throwaway
database, point it at `pgserver`
(`python -c "import pgserver; print(pgserver.get_server('/tmp/pg', cleanup_mode=None).get_uri())"`
prints the socket directory to use as the host).

**Headless Linux and OpenCV.** `rapidocr-onnxruntime` pulls in the full
`opencv-python`, which needs `libGL`. Without a desktop stack, `import cv2`
fails and OCR, image and video readers degrade. Use the headless build:

    pip uninstall -y opencv-python && pip install --force-reinstall --no-deps opencv-python-headless

Windows and desktop Linux do not need this.

## Where things go

[ARCHITECTURE.md](ARCHITECTURE.md) §2 describes the layout, and §11 lists the
recipes for adding a file format, a screen, an endpoint, a table or a
setting. [PRODUCTIZATION_MAP.md](PRODUCTIZATION_MAP.md) maps each capability
to its code, tests and docs. The rules that matter most:

* **Screens** are registered in `core/interfaces/registry.py`. Navigation,
  help, shortcuts and role visibility derive from it
  ([INTERFACE_REGISTRY.md](INTERFACE_REGISTRY.md)).
* **Endpoints** are authenticated and role-checked by default
  (`core/security/flask_ext.py`). `PUBLIC_ENDPOINTS` needs a written reason.
  Mutations need the CSRF token.
* **SQL** is always parameterised. Identifiers go through `core/sql_safety.py`.
* **Paths from users** go through `core/path_safety.py` (`INGESTION_ROOTS`).
  Archive members go through `core/archive_safety.py`.
* **Schema changes** are new numbered migrations in `database/migrations/`.
  Never edit an applied migration. Regenerate the schema reference
  ([TESTING.md](TESTING.md#generated-documents-are-tested)).
* **Errors** returned to clients are generic. Details go to the log via
  `Hdg_Err_Ex_Log`.
* **Front end**: one script per screen in `static/js/pages/`, shared code
  in `static/js/modules/`. The CSP runs no inline script
  ([SECURITY.md](SECURITY.md)): wire controls with `data-on-click="fn(…)"`
  (any `data-on-<event>`), never `onclick=`; pass server values in a
  `<script type="application/json" id="…-page-data">` block, never an inline
  `<script>`. A function called from `data-on-*` must be reachable from
  `window` (a module must assign `window.fn = fn`).
  `tests/unit/test_csp_no_inline_script.py` enforces all of this.
* **Strings** shown to users go through Babel (`_()` / `gettext`). Keep RTL
  layouts working (`ar`, `fa`, `he`).

## Quality gates

Run these before pushing. They are what the audit used:

    python -m ruff check .                      # config in pyproject.toml (E, F, W, B; E501 ignored)
    python -m bandit -q -r . -x ./tests,./.venv,./offline-bundle
    python -m pytest tests/unit
    python -m pytest tests/integration tests/security tests/e2e
    python tools/docs/generate_reference.py --check
    pip-audit -r requirements.txt

Do not silence a warning just to get a clean run. Fix it, or justify it
inline (`# noqa: <code> - reason`, `# nosec <id> # reason`). Put a second `#`
before a nosec reason: bandit reads every word after `nosec` as a test id
and logs a warning per word, but stops at the next `#`. Put the comment on a
line inside the flagged expression (bandit ignores it on a later line of the
same statement), never inside a multi-line SQL string.
[AUDIT_REPORT.md](../AUDIT_REPORT.md) lists the known pre-existing findings.

## Commits, versions, releases

* Commit subjects follow a Conventional-Commits style: `fix(scope): …`,
  `feat(scope): …`, `docs: …`, `test: …`. Keep each commit focused and put
  the reason in the body.
* Every bug fix ships with a regression test that fails on the old code.
* Versions follow semver. The version lives in `version.py` and
  `pyproject.toml` (both must agree), and `CHANGELOG.md` gets an entry per
  release.

To release:

1. Update `version.py`, `pyproject.toml` and `CHANGELOG.md`.
2. Run all quality gates.
3. `git tag -a vX.Y.Z -m "…"` and `git push origin vX.Y.Z`.
4. `gh release create vX.Y.Z --notes-file <notes>`.

Never commit `.env`, `data/settings.json`, virtual environments, logs,
database dumps or the contents of `APP_DATA_DIR`. `.gitignore` covers `.env`,
`data/settings.json`, virtual environments, logs and the install markers
(`.system_initialized`, `.install_state.json`). Keep dumps and `pgserver`
data directories outside the repository.
