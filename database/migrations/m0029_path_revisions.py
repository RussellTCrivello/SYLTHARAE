"""Path revision history - what changed, when, and what it was before.

Why: the Change report must distinguish *added*, *removed* and *modified*,
and for each change expose the detection time, the previous values and the
current values (reporting requirement: Latest and Change baselines). Added
rows are already measurable - ``paths.date_creation`` is the ingestion
time. But nothing recorded a *change to an existing path*: the rename
operation updated the row in place, so after a rename the previous name was
gone, unanswerable. Worse, the absence of history made "modified" and
"removed" unmeasurable rather than measured-zero - exactly the conflation
the directive forbids. This table is the durable memory those states need.

Shape
-----
``path_revisions`` is an append-only event log, one row per recorded
transition of one path:

``path_id``     the path that changed (FK to paths; its deletion would
                remove the file entirely - the log follows the file).
``changed_at``  the detection time of the change (the report's "when did
                it change"), ``NOW()`` at the recording transaction.
``changed_by``  who made the change, when it is a person (NULL for system
                operations - never fabricated).
``change_kind`` ``modified`` (metadata changed, row still present) or
                ``removed`` (the file occurrence left the system). There is
                no ``created`` kind: creation is already an event on
                ``paths.date_creation`` and duplicated here it would drift.
``old_values``  the values before the change, as JSON - ``NULL`` for
                ``removed`` would lose what was removed, so it is required
                there too; only a malformed caller writes NULL.
``new_values``  the values after the change - the live values for
                ``modified``; ``removed`` records the reason/context the
                operation provides.

Writes go through exactly one function,
``services/changes.record_path_revision``: append-only, no UPDATE and no
DELETE path exists on this table by design - historical evaluation results
are never rewritten (scenario-engine rule, applied here).
"""

version = "0029"
name = "path_revisions"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS path_revisions (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        path_id INTEGER NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        changed_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        change_kind VARCHAR(16) NOT NULL
            CHECK (change_kind IN ('modified', 'removed')),
        old_values JSONB NOT NULL,
        new_values JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_path_revisions_path_id"
    " ON path_revisions (path_id, changed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_path_revisions_changed_at"
    " ON path_revisions (changed_at DESC)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS path_revisions")
