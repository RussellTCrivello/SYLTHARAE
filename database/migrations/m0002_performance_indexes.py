"""Performance indexes (DB-06).

Each index has an explicit purpose (see docs/DATABASE.md for benchmarks):

* ``words_paths(path_id)``          - delete-time cascade scan, per-path word listing
* ``words_paths(word_id)``          - inverted lookup: which files contain a word
* ``words_paths(path_id, word_id)`` - index for the (path_id, word_id) uniqueness checks
* ``paths(hash_id)``                - dedup lookups joining paths -> hashs
* ``paths(file_name)``              - file-name search/filter
* ``paths(file_path)``              - exact path existence checks during ingestion
* ``paths(file_type)``              - taxonomy filters and analytics grouping
* ``titles_content(path_id)``       - title joins for file listings (also in 0001)
"""

version = "0002"
name = "performance_indexes"

SQL_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_words_paths_path_id ON words_paths (path_id)",
    "CREATE INDEX IF NOT EXISTS idx_words_paths_word_id ON words_paths (word_id)",
    "CREATE INDEX IF NOT EXISTS idx_words_paths_path_word ON words_paths (path_id, word_id)",
    "CREATE INDEX IF NOT EXISTS idx_paths_hash_id ON paths (hash_id)",
    "CREATE INDEX IF NOT EXISTS idx_paths_file_name ON paths (file_name)",
    "CREATE INDEX IF NOT EXISTS idx_paths_file_path ON paths (file_path)",
    "CREATE INDEX IF NOT EXISTS idx_paths_file_type ON paths (file_type)",
    "CREATE INDEX IF NOT EXISTS idx_paths_file_status ON paths (file_status)",
    "CREATE INDEX IF NOT EXISTS idx_paths_date_creation ON paths (date_creation)",
    "CREATE INDEX IF NOT EXISTS idx_keywords_paths_path_keyword_uq ON keywords_paths (keyword_id, path_id)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)
