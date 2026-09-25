"""
Web-based installation wizard routes.

The page at /setup renders a multi-step wizard (install_wizard.html).
Each step calls an API endpoint that delegates to the shared installer
services in core.installer — no business logic lives here.

Endpoints:
    GET  /setup                  → wizard page
    GET  /api/setup/system-check → prerequisite checks
    POST /api/setup/test-database→ DB connectivity test
    POST /api/setup/install      → execute full installation
    GET  /api/setup/check        → is the system initialized?
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
import logging
import threading

logger = logging.getLogger(__name__)

setup_bp = Blueprint("setup", __name__)

# Installation lock: prevents concurrent installation requests from
# racing.  Only one installation can execute at a time across all
# threads.  The second request receives a deterministic 409 response.
_install_lock = threading.Lock()


def _text_field(data, key, label, default="", *, required=False, maximum=None, preserve=False):
    """Read one bounded text value from an untrusted setup JSON object."""
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(f"{label} cannot contain line breaks or null characters.")
    result = value if preserve else value.strip()
    if required and not result.strip():
        raise ValueError(f"{label} is required.")
    if maximum is not None and len(result) > maximum:
        raise ValueError(f"{label} must be no more than {maximum} characters.")
    return result


def _integer_field(data, key, label, default, minimum, maximum):
    """Parse a JSON integer without accepting booleans or truncating floats."""
    value = data.get(key, default)
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{label} must be a whole number.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a whole number.") from None
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return result


def _is_initialized():

    """Authoritative initialization-state check.

    The system is considered initialized when the filesystem marker exists
    AND the critical tables exist in the application database.  The
    filesystem marker is the fast-path; the table check is authoritative.

    Returns True if initialized, False if not, False on any error
    (fail-safe: an errored check should not block legitimate setup).
    """
    # Fast-path: filesystem marker (written at end of successful install)
    try:
        from core.initialization import is_system_initialized
        if not is_system_initialized():
            return False
        # Marker exists, but verify DB is actually ready too
    except Exception:
        return False

    # Authoritative check: do the critical tables exist?
    try:
        import psycopg2
        from database import get_db_config
        cfg = get_db_config()
        conn = psycopg2.connect(
            dbname=cfg.get("database", "analysis"),
            user=cfg.get("user", "postgres"),
            password=cfg.get("password", ""),
            host=cfg.get("host", "localhost"),
            port=cfg.get("port", 5432),
            connect_timeout=5,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND table_name IN ('words','paths','contents','users')")
            count = cur.fetchone()[0]
            cur.close()
            return count >= 4
        finally:
            conn.close()
    except Exception:
        # DB unreachable — if marker exists, trust it (partial recovery case)
        return True


def _reject_if_initialized():
    """Return an error response if the system is already initialized.

    Used as a guard on mutating setup endpoints.  Returns None if the
    system is NOT initialized (i.e., installation is allowed).
    """
    try:
        if _is_initialized():
            return jsonify({
                "ok": False,
                "error": "System is already initialized. "
                         "Re-installation is not permitted through this endpoint."
            }), 409  # 409 Conflict
    except Exception as e:
        # If we cannot determine state, block installation as a safety measure.
        # The admin can always use CLI for recovery.
        logger.error("Initialization check failed: %s", e)
        return jsonify({
            "ok": False,
            "error": "Cannot verify system state. "
                     "Installation blocked for safety. Use CLI for recovery."
        }), 503
    return None


# ── Page ──
@setup_bp.route("/setup", methods=["GET"])
def setup_page():
    if _is_initialized():
        return redirect(url_for("index"))
    return render_template("Setup/install_wizard.html")


# ── Step 1: System check ──
@setup_bp.route("/api/setup/system-check", methods=["GET"])
def system_check():
    from core.installer import check_system
    checks = check_system()
    all_ok = all(c.get("ok", False) for c in checks.values())
    return jsonify({"all_ok": all_ok, "checks": checks})


# ── Step 2: Test database ──
@setup_bp.route("/api/setup/test-database", methods=["POST"])
def test_database():
    from core.installer import test_database_connection

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "message": "Request body must be a JSON object."}), 400

    try:
        port = _integer_field(data, "port", "Database port", 5432, 1, 65535)
        host = _text_field(data, "host", "Database host", "localhost", required=True, maximum=253)
        user = _text_field(data, "user", "Database username", "postgres", required=True, maximum=63)
        database = _text_field(data, "database", "Database name", "analysis", required=True, maximum=63)
        password = _text_field(data, "password", "Database password", "", required=True,
                               maximum=1024, preserve=True)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    result = test_database_connection(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    status = 200 if result.get("ok", True) else 400
    return jsonify(result), status


# ── Step 5: Full installation ──
@setup_bp.route("/api/setup/install", methods=["POST"])
def run_installation():
    # GUARD 1: reject if system is already initialized
    guard_response = _reject_if_initialized()
    if guard_response is not None:
        return guard_response

    # GUARD 2: serialize installation — only one request at a time.
    # Non-blocking acquire: if another request is already installing,
    # return 409 immediately rather than waiting or racing.
    acquired = _install_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({
            "ok": False,
            "error": "Installation is already in progress. "
                     "Please wait for it to complete."
        }), 409

    try:
        from core.installer import run_installation as _run
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Request body must be a JSON object."}), 400

        try:
            db_host = _text_field(data, "db_host", "Database host", "localhost", required=True, maximum=253)
            db_user = _text_field(data, "db_user", "Database username", "postgres", required=True, maximum=63)
            db_password = _text_field(data, "db_password", "Database password", "", required=True,
                                      maximum=1024, preserve=True)
            db_name = _text_field(data, "db_name", "Database name", "analysis", required=True, maximum=63)
            admin_username = _text_field(data, "admin_username", "Admin username", "admin",
                                         required=True, maximum=64)
            admin_password = _text_field(data, "admin_password", "Admin password", "", required=True,
                                         maximum=256, preserve=True)
            environment = _text_field(data, "environment", "Environment", "production", required=True)
            flask_host = _text_field(data, "flask_host", "Bind address", "0.0.0.0", required=True, maximum=253)
            log_level = _text_field(data, "log_level", "Log level", "INFO", required=True)
            ingestion_roots = _text_field(data, "ingestion_roots", "Ingestion roots", "", maximum=4096)

            db_port = _integer_field(data, "db_port", "Database port", 5432, 1, 65535)
            flask_port = _integer_field(data, "flask_port", "Web port", 5000, 1, 65535)
            max_workers = _integer_field(data, "max_workers", "Maximum processing workers", 8, 1, 32)
            max_failed_logins = _integer_field(data, "max_failed_logins", "Maximum failed logins", 5, 1, 20)
            lockout_minutes = _integer_field(data, "lockout_minutes", "Lockout duration", 15, 1, 1440)
            session_hours = _integer_field(data, "session_hours", "Session lifetime", 12, 1, 720)
            session_idle_hours = _integer_field(data, "session_idle_hours", "Idle timeout", 6, 1, 720)
            pw_min = _integer_field(data, "password_min_length", "Minimum password length", 12, 8, 128)
            rate_per_minute = _integer_field(data, "rate_limit_per_minute", "Rate limit per minute", 60, 1, 1000000)
            rate_per_hour = _integer_field(data, "rate_limit_per_hour", "Rate limit per hour", 600, 1, 1000000)
            file_processing_timeout = _integer_field(
                data, "file_processing_timeout", "Processing timeout", 1200, 30, 7200)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        if len(admin_password) < pw_min:
            return jsonify({"ok": False,
                            "error": f"Admin password must be at least {pw_min} characters."}), 400
        if environment not in {"production", "staging", "development"}:
            return jsonify({"ok": False, "error": "Environment must be production, staging, or development."}), 400
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            return jsonify({"ok": False, "error": "Choose a supported log level."}), 400
        if session_idle_hours > session_hours:
            return jsonify({"ok": False,
                            "error": "Idle timeout cannot exceed the session lifetime."}), 400
        if rate_per_minute > rate_per_hour:
            return jsonify({"ok": False,
                            "error": "The per-minute rate limit cannot exceed the per-hour limit."}), 400

        # Map the validated frontend fields to the installer configuration.
        config = {
            "DB_HOST": db_host,
            "DB_PORT": str(db_port),
            "DB_USER": db_user,
            "DB_PASSWORD": db_password,
            "DB_NAME": db_name,
            "APP_ADMIN_USERNAME": admin_username,
            "APP_ADMIN_PASSWORD": admin_password,
            "FLASK_ENV": environment,
            "FLASK_PORT": str(flask_port),
            "FLASK_HOST": flask_host,
            "MAX_WORKERS": str(max_workers),
            "LOG_LEVEL": log_level,
            "INGESTION_ROOTS": ingestion_roots,
            "SECURITY_MAX_FAILED_LOGINS": str(max_failed_logins),
            "SECURITY_LOCKOUT_MINUTES": str(lockout_minutes),
            "SECURITY_SESSION_HOURS": str(session_hours),
            "SECURITY_SESSION_IDLE_HOURS": str(session_idle_hours),
            "PASSWORD_MIN_LENGTH": str(pw_min),
            "RATE_LIMIT_PER_MINUTE": str(rate_per_minute),
            "RATE_LIMIT_PER_HOUR": str(rate_per_hour),
            "FILE_PROCESSING_TIMEOUT": str(file_processing_timeout),
        }

        result = _run(config)
        result["redirect"] = url_for("index")
        status = 200 if result.get("ok") else 500
        return jsonify(result), status
    finally:
        _install_lock.release()


# ── Status check ──
@setup_bp.route("/api/setup/check", methods=["GET"])
def check_setup_status():
    try:
        return jsonify({"initialized": _is_initialized()}), 200
    except Exception:
        return jsonify({"initialized": False}), 200


# ── Registration ──
def register_setup_routes(app):
    app.register_blueprint(setup_bp)

    @app.before_request
    def _check_database_setup():
        if request.endpoint in (
            "setup.setup_page", "setup.system_check",
            "setup.test_database", "setup.run_installation",
            "setup.check_setup_status", "static",
        ):
            return None
        if request.path.startswith("/api/setup/") or request.path.startswith("/static/"):
            return None
        if request.path == "/api/csrf-token":
            return None
        if request.endpoint in ("_internal_error", "not_found", "favicon"):
            return None
        try:
            if _is_initialized():
                return None
        except Exception:
            pass
        if request.path != "/setup":
            return redirect(url_for("setup.setup_page"))
        return None
