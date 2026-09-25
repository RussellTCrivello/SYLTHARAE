#!/usr/bin/env bash
# Rebuild the measurement environment for the scalability harness.
#
# The sandbox rebuilds /.venv between sessions, so this script recreates it
# reproducibly. It deliberately skips two optional requirements that need
# system toolchains not present here (libpff-python needs Python headers and a
# source build of libpff); everything else in requirements.txt is installed.
#
#   tools/scalability/setup_env.sh [venv-path]
set -euo pipefail

VENV="${1:-/home/user/.venv}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --disable-pip-version-check --upgrade pip

# Requirements minus the source-build-only extras.
grep -v -e '^libpff-python' -e '^rapidocr-onnxruntime' "$REPO_ROOT/requirements.txt" \
    > /tmp/requirements_scalability.txt
"$VENV/bin/pip" install -q --disable-pip-version-check -r /tmp/requirements_scalability.txt

# Test + measurement tooling.
"$VENV/bin/pip" install -q --disable-pip-version-check pytest pytest-timeout \
    pgserver psycopg2-binary psutil pandas

"$VENV/bin/python" - <<'PY'
import importlib
required = ("flask", "psycopg2", "psutil", "pgserver", "pytest", "PIL", "fitz",
            "docx", "numpy", "pytesseract", "openpyxl", "ebooklib", "py7zr",
            "pandas")
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name} ({type(exc).__name__})")
print("environment ready" if not missing else f"MISSING: {', '.join(missing)}")
PY
