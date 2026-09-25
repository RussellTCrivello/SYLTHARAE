"""Content identity evolution: One Content, Many Contexts.

m0001 modelled identity as ``hashs (hash, source_id, side_id) UNIQUE`` and
hung ``paths`` (and every derived store) off that row.  That conflated three
different concepts:

    CONTENT IDENTITY      the bytes themselves                 -> HASH
    CONTEXT IDENTITY      the content as represented           -> HASH + SOURCE + SIDE
    OCCURRENCE/PROVENANCE where it was physically encountered  -> PATH

Under the old model the same bytes seen in two sources were two "hashs" rows,
every occurrence re-duplicated its extracted text, word index and keywords,
and a second archive member with identical bytes silently lost its own record.

This migration evolves the existing schema into the three-level model without
creating a parallel architecture:

    hashs           one row per HASH (UNIQUE (hash))            - canonical content
    hash_contexts   one row per HASH+SOURCE+SIDE (UNIQUE)       - contextual identity
                    (the genuinely new relation: the context triple needs its
                    own uniqueness, which cannot live inside ``hashs`` once
                    ``hash`` alone is unique.  It extends the existing model
                    with plain FKs to ``hashs``/``sources``/``sides``.)
    paths           one row per physical occurrence, FK to hash_contexts
    contents, contents_raw, titles_content, words_hashs, keywords_hashs
                    content-derived data, FK to hashs (stored once per content)

Historical duplication is *consolidated*, never blindly deleted:

* ``hashs`` rows that shared a hash value collapse onto the earliest row
  (MIN(id)); every (source, side) pair they carried becomes a ``hash_contexts``
  row, so no context is lost.
* For each derived table and each content hash, the extraction belonging to
  the earliest occurrence (MIN(path_id)) becomes the canonical extraction;
  identical rows for the same content under other occurrences are merged into
  it.  Every dependent row therefore has a valid destination (the canonical
  hash row).
* Every ``paths`` row survives, repointed to its context.  Provenance columns
  (parent_path_id, hierarchy_path, extraction_provenance, coordinates, ...) are
  untouched.

Constraints added here are the database-enforced identity rules:

* ``hashs``:                 UNIQUE (hash)                      - one canonical row
* ``hash_contexts``:         UNIQUE (hash_id, source_id, side_id) - one context row
* ``words_hashs``:           UNIQUE (hash_id, word_id)
* ``keywords_hashs``:        UNIQUE (hash_id, keyword_id)
* ``contents_raw``:          UNIQUE (hash_id, chunk_seq)

which together make concurrent ingestion of identical content safe
(ON CONFLICT upserts converge on one canonical row and one context row).
"""

version = "0011"
name = "content_identity"


def _execute(cur, statements):
    for statement in statements:
        cur.execute(statement)


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables"
        " WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        # ------------------------------------------------------------------
        # Phase 0 - idempotence guard: a database created directly at m0011+
        # (fresh installs run every migration in order, so this only guards
        # manual re-runs against an already-migrated database).
        # ------------------------------------------------------------------
        if not _column_exists(cur, "hashs", "source_id"):
            # Already migrated.
            return

        # ------------------------------------------------------------------
        # Phase 1 - canonical mapping + context relation
        # ------------------------------------------------------------------
        # old hashs row -> canonical (earliest) hashs row per hash value.
        cur.execute("DROP TABLE IF EXISTS _hash_canon")
        cur.execute(
            """
            CREATE TEMP TABLE _hash_canon (
                old_id   INTEGER PRIMARY KEY,
                canon_id INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            INSERT INTO _hash_canon (old_id, canon_id)
            SELECT h.id, m.canon
            FROM hashs h
            JOIN (SELECT hash, MIN(id) AS canon FROM hashs GROUP BY hash) m
              ON m.hash = h.hash
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hash_contexts (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                hash_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                side_id INTEGER NOT NULL,
                date_creation DATE NOT NULL DEFAULT CURRENT_DATE,
                CONSTRAINT uq_hash_contexts_identity
                    UNIQUE (hash_id, source_id, side_id),
                FOREIGN KEY (source_id) REFERENCES sources(id) ON UPDATE CASCADE,
                FOREIGN KEY (side_id) REFERENCES sides(id) ON UPDATE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_hash_contexts_hash_id"
            " ON hash_contexts (hash_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_hash_contexts_source_id"
            " ON hash_contexts (source_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_hash_contexts_side_id"
            " ON hash_contexts (side_id)"
        )
        # One context row per (hash, source, side).  The old UNIQUE
        # (hash, source_id, side_id) guarantees these are distinct already;
        # DISTINCT + ON CONFLICT keeps this statement safe regardless.
        cur.execute(
            """
            INSERT INTO hash_contexts (hash_id, source_id, side_id)
            SELECT DISTINCT m.canon_id, h.source_id, h.side_id
            FROM hashs h
            JOIN _hash_canon m ON m.old_id = h.id
            ON CONFLICT (hash_id, source_id, side_id) DO NOTHING
            """
        )

        # ------------------------------------------------------------------
        # Phase 2 - paths become occurrences of a context
        # ------------------------------------------------------------------
        cur.execute("ALTER TABLE paths ADD COLUMN IF NOT EXISTS context_id INTEGER")
        cur.execute(
            """
            UPDATE paths p SET context_id = c.id
            FROM _hash_canon m
            JOIN hashs h ON h.id = m.old_id
            JOIN hash_contexts c
              ON c.hash_id = m.canon_id
             AND c.source_id = h.source_id
             AND c.side_id = h.side_id
            WHERE p.hash_id = m.old_id
            """
        )
        cur.execute("SELECT COUNT(*) FROM paths WHERE context_id IS NULL")
        unmapped = cur.fetchone()[0]
        if unmapped:
            raise RuntimeError(
                f"content_identity migration: {unmapped} path row(s) could not "
                "be mapped to a context - refusing to migrate to protect them"
            )

        # ------------------------------------------------------------------
        # Phase 3 - derived stores re-keyed to canonical content
        #
        # Uniform consolidation rule: per content hash, the extraction of the
        # earliest occurrence (MIN(path_id)) becomes canonical.  Rows belonging
        # to later occurrences of the same content are the historical
        # duplication this migration exists to remove; they are merged, not
        # silently dropped blind - each consolidation below is scoped to one
        # content hash and keeps a valid destination.
        # ------------------------------------------------------------------

        # --- words_paths -> words_hashs, keywords_paths -> keywords_hashs ---
        # The old names asserted that words/keywords belong to path rows; they
        # are content-derived and belong to canonical content.  The old
        # UNIQUE (path_id, keyword_id) constraint on keywords_paths dies with
        # its columns below; the hash-keyed uniques are added in Phase 6.
        cur.execute("ALTER TABLE words_paths RENAME TO words_hashs")
        cur.execute("ALTER TABLE keywords_paths RENAME TO keywords_hashs")

        for table, extra in (
            ("contents", ""),
            ("contents_raw", ""),
            ("titles_content", ""),
            ("words_hashs", ""),
            ("keywords_hashs", ""),
        ):
            cur.execute(
                f"""
                WITH keep AS (
                    SELECT m.canon_id AS hash_id, MIN(t.path_id) AS keep_path
                    FROM {table} t
                    JOIN paths p ON p.id = t.path_id
                    JOIN _hash_canon m ON m.old_id = p.hash_id
                    GROUP BY m.canon_id
                )
                DELETE FROM {table} t
                USING paths p, _hash_canon m, keep k
                WHERE t.path_id = p.id
                  AND m.old_id = p.hash_id
                  AND k.hash_id = m.canon_id
                  AND t.path_id <> k.keep_path
                """
                + extra
            )

        # words_hashs may hold repeated (path, word) rows from before the
        # NOT EXISTS guard was reliable; collapse to one row per (path, word)
        # so the hash-keyed unique identity below can be enforced (all
        # remaining rows belong to the one canonical path of their hash).
        cur.execute(
            """
            DELETE FROM words_hashs w
            USING words_hashs k
            WHERE w.word_id = k.word_id
              AND w.path_id = k.path_id
              AND k.ctid < w.ctid
            """
        )
        # contents historically held one row per chunk per path; after the
        # per-hash consolidation above all remaining rows belong to the
        # canonical extraction of their hash.

        for table in (
            "contents",
            "contents_raw",
            "titles_content",
            "words_hashs",
            "keywords_hashs",
        ):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS hash_id INTEGER")
            cur.execute(
                f"""
                UPDATE {table} t SET hash_id = m.canon_id
                FROM paths p
                JOIN _hash_canon m ON m.old_id = p.hash_id
                WHERE t.path_id = p.id
                """
            )
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN hash_id SET NOT NULL")
            cur.execute(f"ALTER TABLE {table} DROP COLUMN path_id")

        # ------------------------------------------------------------------
        # Phase 4 - hashs becomes the canonical content table
        # ------------------------------------------------------------------
        # The legacy paths.hash_id reference must go first: it still points at
        # the old per-context rows, including the ones about to be consolidated.
        cur.execute("ALTER TABLE paths DROP COLUMN hash_id")
        cur.execute(
            """
            DELETE FROM hashs h
            USING _hash_canon m
            WHERE h.id = m.old_id AND m.old_id <> m.canon_id
            """
        )
        cur.execute("ALTER TABLE hashs DROP COLUMN source_id")
        cur.execute("ALTER TABLE hashs DROP COLUMN side_id")
        cur.execute(
            "ALTER TABLE hashs ADD CONSTRAINT hashs_hash_key UNIQUE (hash)"
        )
        cur.execute(
            """
            ALTER TABLE hash_contexts
            ADD CONSTRAINT fk_hash_contexts_hash
            FOREIGN KEY (hash_id) REFERENCES hashs(id) ON UPDATE CASCADE
            """
        )

        # ------------------------------------------------------------------
        # Phase 5 - paths finalize as occurrences
        # ------------------------------------------------------------------
        cur.execute("ALTER TABLE paths ALTER COLUMN context_id SET NOT NULL")
        cur.execute(
            """
            ALTER TABLE paths
            ADD CONSTRAINT fk_paths_context
            FOREIGN KEY (context_id) REFERENCES hash_contexts(id) ON UPDATE CASCADE
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_paths_context_id ON paths (context_id)"
        )

        # ------------------------------------------------------------------
        # Phase 6 - derived FKs, uniqueness and indexes
        # ------------------------------------------------------------------
        _execute(
            cur,
            [
                "ALTER TABLE contents ADD CONSTRAINT fk_contents_hash"
                " FOREIGN KEY (hash_id) REFERENCES hashs(id)"
                " ON DELETE CASCADE ON UPDATE CASCADE",
                "CREATE INDEX IF NOT EXISTS idx_contents_hash_id"
                " ON contents (hash_id)",
                "ALTER TABLE contents_raw ADD CONSTRAINT fk_contents_raw_hash"
                " FOREIGN KEY (hash_id) REFERENCES hashs(id)"
                " ON DELETE CASCADE ON UPDATE CASCADE",
                "ALTER TABLE contents_raw ADD CONSTRAINT uq_contents_raw_hash_chunk"
                " UNIQUE (hash_id, chunk_seq)",
                "CREATE INDEX IF NOT EXISTS idx_contents_raw_hash_id"
                " ON contents_raw (hash_id)",
                "ALTER TABLE titles_content ADD CONSTRAINT fk_titles_content_hash"
                " FOREIGN KEY (hash_id) REFERENCES hashs(id)"
                " ON DELETE CASCADE ON UPDATE CASCADE",
                "CREATE INDEX IF NOT EXISTS idx_titles_content_hash_id"
                " ON titles_content (hash_id)",
                "ALTER TABLE words_hashs ADD CONSTRAINT fk_words_hashs_hash"
                " FOREIGN KEY (hash_id) REFERENCES hashs(id)"
                " ON DELETE CASCADE ON UPDATE CASCADE",
                "ALTER TABLE words_hashs ADD CONSTRAINT uq_words_hashs_hash_word"
                " UNIQUE (hash_id, word_id)",
                "CREATE INDEX IF NOT EXISTS idx_words_hashs_hash_id"
                " ON words_hashs (hash_id)",
                "CREATE INDEX IF NOT EXISTS idx_words_hashs_word_id"
                " ON words_hashs (word_id)",
                "ALTER TABLE keywords_hashs ADD CONSTRAINT fk_keywords_hashs_hash"
                " FOREIGN KEY (hash_id) REFERENCES hashs(id)"
                " ON DELETE CASCADE ON UPDATE CASCADE",
                "ALTER TABLE keywords_hashs ADD CONSTRAINT uq_keywords_hashs_hash_keyword"
                " UNIQUE (hash_id, keyword_id)",
                "CREATE INDEX IF NOT EXISTS idx_keywords_hashs_hash_id"
                " ON keywords_hashs (hash_id)",
                "CREATE INDEX IF NOT EXISTS idx_keywords_hashs_keyword_id"
                " ON keywords_hashs (keyword_id)",
            ],
        )

        cur.execute("DROP TABLE IF EXISTS _hash_canon")


def downgrade(conn) -> None:
    """Best-effort reversal: restore the (hash, source, side) identity shape.

    Contexts re-expand into one ``hashs`` row each and content-derived rows
    attach to the first path of their content's contexts.  Canonical
    consolidation and per-path derived duplication are not reversible: merged
    rows stay merged on one representative path.
    """
    with conn.cursor() as cur:
        if _column_exists(cur, "hashs", "source_id"):
            return

        _execute(
            cur,
            [
                # Re-expand identity rows per context.
                "ALTER TABLE hashs ADD COLUMN source_id INTEGER",
                "ALTER TABLE hashs ADD COLUMN side_id INTEGER",
                """
                UPDATE hashs h
                SET source_id = c.source_id, side_id = c.side_id
                FROM hash_contexts c
                WHERE c.hash_id = h.id
                """,
            ],
        )
        # Contexts whose canonical row already took one triple keep that row;
        # any additional contexts need their own hashs rows.
        cur.execute(
            """
            INSERT INTO hashs (hash, source_id, side_id)
            SELECT h.hash, c.source_id, c.side_id
            FROM hash_contexts c
            JOIN hashs h ON h.id = c.hash_id
            WHERE NOT EXISTS (
                SELECT 1 FROM hashs x
                WHERE x.hash = h.hash
                  AND x.source_id = c.source_id
                  AND x.side_id = c.side_id
            )
            """
        )
        cur.execute(
            "ALTER TABLE hashs ALTER COLUMN source_id SET NOT NULL"
        )
        cur.execute(
            "ALTER TABLE hashs ALTER COLUMN side_id SET NOT NULL"
        )
        cur.execute("ALTER TABLE hashs DROP CONSTRAINT IF EXISTS hashs_hash_key")

        # paths.hash_id = the hashs row of their (hash, source, side).
        cur.execute("ALTER TABLE paths ADD COLUMN hash_id INTEGER")
        cur.execute(
            """
            UPDATE paths p SET hash_id = h2.id
            FROM hash_contexts c
            JOIN hashs h1 ON h1.id = c.hash_id
            JOIN hashs h2
              ON h2.hash = h1.hash
             AND h2.source_id = c.source_id
             AND h2.side_id = c.side_id
            WHERE c.id = p.context_id
            """
        )
        cur.execute("ALTER TABLE paths ALTER COLUMN hash_id SET NOT NULL")
        cur.execute(
            "ALTER TABLE paths ADD CONSTRAINT fk_paths_hashs"
            " FOREIGN KEY (hash_id) REFERENCES hashs(id)"
        )
        cur.execute("ALTER TABLE paths DROP COLUMN context_id")

        # Derived rows attach to the first path of their content's contexts.
        # (Best-effort: the pre-m0011 per-path duplication of derived data is
        # NOT restored - every row survives on one representative path instead
        # of being copied onto each occurrence.  Canonical consolidation above
        # is likewise irreversible.)
        for table in ("contents", "contents_raw", "titles_content",
                      "words_hashs", "keywords_hashs"):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN path_id INTEGER")
            cur.execute(
                f"""
                UPDATE {table} t SET path_id = sub.first_path
                FROM (
                    SELECT c.hash_id, MIN(p.id) AS first_path
                    FROM hash_contexts c
                    JOIN paths p ON p.context_id = c.id
                    GROUP BY c.hash_id
                ) sub
                WHERE sub.hash_id = t.hash_id
                """
            )
            cur.execute(f"DELETE FROM {table} WHERE path_id IS NULL")
            cur.execute(
                f"ALTER TABLE {table} ALTER COLUMN path_id SET NOT NULL"
            )
            cur.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_path"
                " FOREIGN KEY (path_id) REFERENCES paths(id)"
                " ON DELETE CASCADE ON UPDATE CASCADE"
            )
            cur.execute(f"ALTER TABLE {table} DROP COLUMN hash_id")

        cur.execute("ALTER TABLE words_hashs RENAME TO words_paths")
        cur.execute("ALTER TABLE keywords_hashs RENAME TO keywords_paths")
        cur.execute(
            "ALTER TABLE keywords_paths ADD CONSTRAINT unique_keywords_paths_path_keyword"
            " UNIQUE (path_id, keyword_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_paths_hash_id ON paths (hash_id)"
        )
        cur.execute("DROP TABLE hash_contexts")
