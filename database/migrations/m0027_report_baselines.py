"""Per-user Latest/Change baselines (reporting step: Latest & Change).

Why: the Latest report must be able to say of every row which of three
states it is in - *recently created* (ingested recently, independent of any
reader), *previously seen* (this reader saw this report view before), and
*new since the last view* (present now, beyond what this reader had seen
up to last time). The first is a property of the data; the other two need a
durable per-reader memory of how far each reader had read.

Shape
-----
``report_baselines`` is that memory, keyed by reader and view:

``user_id``       the reader the baseline belongs to.
``criteria_hash`` SHA-256 of the view identity (criteria fingerprint plus
                  the parameters that shape the listing) - one reader keeps
                  one baseline per distinct view, not per report.
``last_seen_at``  when that reader last advanced the view.
``last_max_id``   how far the reader had read: the maximum row id the view
                  had shown them. A row with a larger id in a later run is
                  new *to that reader*.

The table records progress, never content: nothing here duplicates the
documents themselves, so a deleted document cannot dangle and the baseline
stays tiny. Rows are upserted (one reader, one view, one row); the update
is monotonic in the service layer (a baseline never moves backwards, so a
stale second tab cannot un-see what was already seen).

The zero/unknown distinction is structural: ``last_seen_at IS NULL`` (no
baseline yet) is written as an absent row, never as a zero id - "never
viewed" is not the same as "viewed and saw nothing".
"""

version = "0027"
name = "report_baselines"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS report_baselines (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        criteria_hash CHAR(64) NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_max_id BIGINT NOT NULL CHECK (last_max_id >= 0),
        CONSTRAINT pk_report_baselines PRIMARY KEY (user_id, criteria_hash),
        CONSTRAINT ck_report_baselines_hash_hex
            CHECK (criteria_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_report_baselines_user_seen
        ON report_baselines (user_id, last_seen_at DESC)
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS report_baselines")
