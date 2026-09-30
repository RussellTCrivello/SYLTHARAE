"""Per-user sidebar navigation preferences (owner request: manageability).

Why: the sidebar is rendered from the interface registry - domains in
declared order, entries in declaration order - and neither the order nor
the visibility could be chosen by the user. The owner asked to reorder the
sidebar buttons from the settings and to show or hide them, controlling
each entry's position. This table stores that choice per user; the
registry stays the source of what EXISTS and what may be SHOWN (roles,
dependencies, feature flags are still decided in `core.interfaces` and
`core/security`); the preference can only narrow what the registry already
allows, never widen it. A hidden entry stays hidden only while the
registry would show it; a demoted user loses entries by role, not by
preference.

Storage is one row per (user, interface): `hidden` removes the entry from
that user's sidebar, `position` (1-based, NULL = the declared order) places
it. Unknown interface ids are refused by the service before anything is
written; rows whose interface later leaves the registry are simply ignored
by the navigation builder.
"""

version = "0033"
name = "user_navigation_prefs"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS user_navigation_prefs (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        interface_id VARCHAR(64) NOT NULL,
        hidden BOOLEAN NOT NULL DEFAULT FALSE,
        position INTEGER NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, interface_id),
        CONSTRAINT ck_user_navigation_prefs_position
            CHECK (position IS NULL OR position >= 1)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_user_navigation_prefs_user
        ON user_navigation_prefs (user_id)
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS user_navigation_prefs")
