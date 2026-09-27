"""Content-derived geolocation mentions (File Analysis: Geolocation).

Real place-name mentions found in a document's extracted text (gazetteer
match against ``contents_raw``), one row per (hash, place). A hash can
mention more than one place; ``paths.coordinates`` (existing column, already
consumed by the legacy ``/api/archives/geolocation`` endpoint) is kept in
sync with the single best/most-frequent match per hash for backward
compatibility, while this table keeps every match for richer browsing.

Nothing here is fabricated: rows are only ever inserted by
``Api/services/geo_extraction_service.py`` after finding an actual
case-sensitive, word-bounded gazetteer match in a file's real extracted
text, and are keyed off ``hash_id`` (canonical content), never guessed per
source/side.
"""

version = "0015"
name = "geo_mentions"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS path_geo_mentions (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash_id INTEGER NOT NULL,
        place_name VARCHAR(255) NOT NULL,
        country VARCHAR(255) NULL,
        latitude DOUBLE PRECISION NOT NULL,
        longitude DOUBLE PRECISION NOT NULL,
        mention_count INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT uq_path_geo_mentions_hash_place UNIQUE (hash_id, place_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_path_geo_mentions_hash_id ON path_geo_mentions (hash_id)",
    "CREATE INDEX IF NOT EXISTS idx_path_geo_mentions_place_name ON path_geo_mentions (place_name)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS path_geo_mentions")
