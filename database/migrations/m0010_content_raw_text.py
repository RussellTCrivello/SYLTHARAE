"""Structured raw content for faithful per-type display.

The word-ID content store is a search index: words are lowercased and joined
by single spaces on reconstruction, which destroys the structure the
extractors worked out (worksheet names and boundaries, slide numbers, page
headers, tab-delimited table rows, original casing, punctuation, line
breaks). Type-aware display (spreadsheet sheets with tab navigation, slide
decks, paginated PDFs, rendered Markdown, log viewers) needs that structure.

``contents_raw`` stores the extractor's combined text verbatim (chunked),
written in the same transaction as the word-ID content. Readers prefer the
raw text and fall back to the word-join reconstruction for legacy rows, so
existing databases keep working unchanged.

Design notes:

* The word-ID pipeline (``contents`` + ``words_paths``) is untouched - it
  remains the single source for word search, frequencies and classification.
* ``ON DELETE CASCADE`` from ``paths`` mirrors ``contents`` so file deletion
  and re-ingest clean up automatically.
* TEXT (not BYTEA): the raw text is the display source of truth and is
  chunked at 1 MB to keep single rows small.
"""

version = "0010"
name = "content_raw_text"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS contents_raw (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        path_id INTEGER NOT NULL,
        chunk_seq INTEGER NOT NULL,
        content TEXT NOT NULL,
        char_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_contents_raw_path_chunk UNIQUE (path_id, chunk_seq),
        CONSTRAINT fk_contents_raw_path FOREIGN KEY (path_id)
            REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_contents_raw_path_id ON contents_raw (path_id)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS contents_raw")
