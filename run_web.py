"""
Entry point for the web application.

This script initializes the Flask web application and starts the development server.
For production deployment, use a WSGI server like Gunicorn or uWSGI.

Usage:
    python run_web.py
"""

import sys
import os
from pathlib import Path

# Windows-native console safety: when stdout/stderr are redirected (service
# install, pipe, scheduled task) Python falls back to the legacy ANSI code
# page and the engine's progress output (emoji) would raise
# UnicodeEncodeError inside worker threads. Force UTF-8 with replacement.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

# Auto-install check: Only run if AUTO_INSTALL is not disabled
if os.environ.get('AUTO_INSTALL', '1') == '1':
    try:
        try:
            import flask
            import psycopg2
        except ImportError:
            print("=" * 60)
            print("Dependencies not found. Attempting auto-install...")
            print("=" * 60)
            
            in_venv = hasattr(sys, 'real_prefix') or (
                hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
            )
            
            if not in_venv:
                print("WARNING: Not in virtual environment.")
                print("Please run: setup.bat or start.bat")
                print("=" * 60)
            else:
                wheels_dir = Path("wheels")
                if wheels_dir.exists():
                    import subprocess
                    print("Installing missing dependencies from local wheels...")
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--no-index", 
                         "--find-links=wheels", "flask", "psycopg2-binary", "sqlalchemy"],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        print("Dependencies installed successfully!")
                        print("=" * 60)
                        import importlib
                        importlib.invalidate_caches()
                    else:
                        print("WARNING: Auto-install failed. Please run setup.bat manually")
                        print("=" * 60)
                else:
                    print("ERROR: wheels\\ directory not found!")
                    print("Please run download_dependencies.bat first (with internet)")
                    print("=" * 60)
    except Exception as e:
        print(f"Auto-install check failed: {e}")
        print("Continuing with startup...")

# Simplified initialization
from core.init import (
    setup_project_path,
    initialize_settings,
    initialize_database_config,
    initialize_system
)

# Set up project path
project_root = setup_project_path(__file__)

# Initialize settings
initialize_settings(project_root)

# Load database configuration
initialize_database_config()

# Initialize system
initialize_system()

# Import and run the web app
from apps.web.app import app

def main():
    """Console entry point (pyproject: file-analysis-web = run_web:main)."""
    globals()['_run_main']()


def _run_main():
    # SEC-10: debug mode is environment-controlled and never hardcoded.
    # Production startup must reject debug mode.
    flask_env = os.environ.get('FLASK_ENV', 'production').lower()
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')
    if debug_mode and flask_env == 'production':
        raise RuntimeError(
            "Refusing to start: FLASK_DEBUG is enabled while FLASK_ENV=production. "
            "Set FLASK_ENV=development for debug mode."
        )
    if debug_mode:
        print("[WARNING] Flask debug mode is ENABLED - development use only")

    # Compute policy: report the effective mode (and the layer that set it)
    # before serving. The web application still starts when the configured mode
    # cannot be honoured - an operator must be able to reach the UI to fix the
    # setting - but the problem is stated here and every workload that requires
    # the missing device is refused by the gateway rather than silently run on
    # the CPU.
    try:
        from core.compute import policy as compute_policy

        selection = compute_policy.mode_selection()
        support = compute_policy.mode_support(selection)
        compute_policy.record(selection, support)
        compute_policy.emit(selection, support, printer=print)
        if not support.ok:
            print(f"[WARNING] {support.problem}")
            if support.action:
                print(f"[ACTION] {support.action}")
    except Exception as policy_exc:  # never block startup over the banner
        print(f"[WARNING] Compute policy could not be resolved: {policy_exc}")

    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', '5000'))
    print(f"[OK] Starting web server on http://127.0.0.1:{port} (press CTRL+C to stop)")
    app.run(debug=debug_mode, host=host, port=port)


if __name__ == '__main__':
    # `python run_web.py` must start the server (previously it imported the
    # app and exited silently, leaving beginners at an empty prompt).
    _run_main()
