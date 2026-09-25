"""Persist administrator-managed translations separately from source catalogs.

The checked-in gettext catalogs remain the source of defaults. This table
contains only deliberate runtime overrides, allowing translation edits to
survive deployments without rewriting generated PO/MO files.
"""

version = "0012"
name = "translation_overrides"


SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS translation_overrides (
        locale VARCHAR(16) NOT NULL,
        message_key CHAR(64) NOT NULL,
        msgid TEXT NOT NULL,
        translation TEXT NOT NULL CHECK (length(translation) > 0),
        updated_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (locale, message_key)
    )
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS translation_overrides")
