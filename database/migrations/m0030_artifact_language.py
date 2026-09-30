"""Which language an artifact was rendered in (step 18).

Why: the renderers are deterministic per (run, format, dataset, renderer
version, language) - the language is part of the rendering, chosen by the
requester, recorded on the artifact and in its manifest. Without the
column, two renderings of one run that differ only by language would
collide on the uniqueness key, and a reader could not tell which language
a stored file speaks without opening it and guessing.

``language`` is the BCP-47 primary tag of one of the shipped Babel
catalogs (``en``, ``ar``, ``he``, ``fa``, ``hr``). Existing rows describe
the English-only renderers, so the default is ``'en'`` - true for every
row written before this migration, never a placeholder.
"""

version = "0030"
name = "report_artifacts_language"

SQL_STATEMENTS = [
    """
    ALTER TABLE report_artifacts
        ADD COLUMN IF NOT EXISTS language VARCHAR(8) NOT NULL DEFAULT 'en'
    """,
    """
    ALTER TABLE report_artifacts
        DROP CONSTRAINT IF EXISTS ck_report_artifacts_language
    """,
    """
    ALTER TABLE report_artifacts
        ADD CONSTRAINT ck_report_artifacts_language
        CHECK (language ~ '^[a-z]{2}(-[A-Za-z0-9]+)?$')
    """,
    "DROP INDEX IF EXISTS uq_report_artifacts_rendering",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_report_artifacts_rendering
    ON report_artifacts (run_id, format, COALESCE(dataset_key, ''),
                         renderer_version, language)
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS uq_report_artifacts_rendering")
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_report_artifacts_rendering
            ON report_artifacts (run_id, format, COALESCE(dataset_key, ''),
                                 renderer_version)
        """)
        cur.execute(
            "ALTER TABLE report_artifacts DROP CONSTRAINT IF EXISTS"
            " ck_report_artifacts_language")
        cur.execute("ALTER TABLE report_artifacts DROP COLUMN IF EXISTS language")
