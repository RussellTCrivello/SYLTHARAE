"""Step 14: the Reports page renders what the server recorded, safely.

Runs the real page module (static/js/pages/reports-page.js) against a DOM
stub and a fake server (tests/js/reports_page_smoke.mjs): user and server
text is inserted as text, never markup, and innerHTML is never assigned;
NULL is shown as "none" and an empty string is not; a truncated dataset says
so; a failed run shows the server's error verbatim; a viewer's Run button is
disabled with the reason; a saved search is submitted by id.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_reports_page_renders_server_state_as_text():
    proc = subprocess.run(["node", "tests/js/reports_page_smoke.mjs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("ok ") == 16, proc.stdout
