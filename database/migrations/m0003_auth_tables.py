"""Authentication & authorization tables (SEC-01, SEC-02).

* ``users``     - accounts with scrypt password hashes and lockout state
* ``sessions``  - server-side session records (revocable, expiring)
* ``audit_log`` - security-relevant action trail
"""

version = "0003"
name = "auth_tables"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        username VARCHAR(64) UNIQUE NOT NULL,
        password_hash VARCHAR(512) NOT NULL,
        role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
        failed_login_count INTEGER NOT NULL DEFAULT 0,
        locked_until TIMESTAMPTZ NULL,
        last_login_at TIMESTAMPTZ NULL,
        password_changed_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id CHAR(64) PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ NULL,
        ip_address VARCHAR(64) NULL,
        user_agent VARCHAR(255) NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at)",
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER NULL,
        username VARCHAR(64) NULL,
        action VARCHAR(255) NOT NULL,
        resource VARCHAR(255) NULL,
        detail JSONB NULL,
        ip_address VARCHAR(64) NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log (action)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)
