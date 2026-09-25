"""Health check endpoint (public, unauthenticated).

Provides a simple liveness/readiness probe for load balancers, monitoring,
and the installer's verification phase.

GET /health → 200 if the app can start, 503 if a critical dependency fails.

Thread-safety: uses ``threading.local()`` so each thread gets its own
connection.  A module-level ``psycopg2`` connection shared across threads
is unsafe because psycopg2 connections are not thread-safe for concurrent
cursor operations.  ``threading.local()`` gives each request-handling
thread its own connection with independent lifecycle and stale-connection
handling.

The connection is lightweight (single ``SELECT 1`` probe), not a pool.
"""

from __future__ import annotations

import logging
import threading
import time

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)

_start_time = time.time()

# Thread-local storage for health-check connections.
# Each thread (i.e., each concurrent request handler) gets its own
# psycopg2 connection.  This eliminates the shared-state race that a
# module-level connection would have under Flask's threaded mode.
_local = threading.local()

# Lock for connection creation — prevents thundering-herd on startup
# when multiple health probes arrive before any connection exists.
_connect_lock = threading.Lock()


def _get_health_connection():
    """Return a thread-local psycopg2 connection for health probes.

    Each thread gets its own connection.  Stale connections (closed by the
    server, idle timeout, network reset) are detected by a ``SELECT 1``
    probe and replaced transparently.

    Returns a valid open connection, or raises if PostgreSQL is unreachable.
    """
    conn = getattr(_local, "health_conn", None)

    # Fast path: existing connection, test liveness.
    if conn is not None:
        try:
            if conn.closed:
                conn = None
            else:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return conn
        except Exception:
            # Connection stale — close and fall through to create a new one.
            try:
                conn.close()
            except Exception:
                pass
            _local.health_conn = None
            conn = None

    # Slow path: create a new connection (serialized to avoid thundering herd).
    with _connect_lock:
        # Double-check: another thread may have created one while we waited.
        conn = getattr(_local, "health_conn", None)
        if conn is not None and not conn.closed:
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                _local.health_conn = None

        import psycopg2
        from database import get_db_config
        cfg = get_db_config()
        conn = psycopg2.connect(
            host=cfg.get("host", "localhost"),
            port=int(cfg.get("port", 5432)),
            user=cfg.get("user", "postgres"),
            password=cfg.get("password", ""),
            dbname=cfg.get("database", "analysis"),
            connect_timeout=5,
        )
        _local.health_conn = conn
        return conn


@health_bp.route("/health", methods=["GET"])
def health():
    """Liveness + lightweight readiness probe.

    Returns 200 with component status when the app is operational.
    Returns 503 when a critical component is down.
    """
    checks: dict = {}
    all_ok = True

    # Database connectivity — thread-local connection, no shared state.
    try:
        conn = _get_health_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        all_ok = False

    # Settings system
    try:
        from settings import get_settings

        settings = get_settings()
        _ = settings.version
        checks["settings"] = "ok"
    except Exception as exc:
        checks["settings"] = f"error: {exc.__class__.__name__}"
        all_ok = False

    uptime = int(time.time() - _start_time)
    status_code = 200 if all_ok else 503
    return jsonify({
        "status": "healthy" if all_ok else "degraded",
        "uptime_seconds": uptime,
        "checks": checks,
    }), status_code


def register_health_routes(app):
    """Register the health blueprint on the app."""
    app.register_blueprint(health_bp)
    logger.info("✅ Health check endpoint registered at /health")
