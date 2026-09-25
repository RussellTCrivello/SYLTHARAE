"""Relationship Deduplication and Integrity (Hash + Source + Side).

Enforces relationship identity defined strictly by (Hash + Source + Side).
1. Merges any duplicate rows in `hash_contexts` matching identical (hash_id, source_id, side_id):
   - For each duplicate group, keeps the canonical minimum id.
   - Updates `paths` referencing duplicate context IDs to point to canonical ID.
   - Deletes duplicate hash_contexts rows.
2. Ensures a UNIQUE constraint exists on `hash_contexts (hash_id, source_id, side_id)`.
"""

version = "0013"
name = "relationship_deduplication"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        # Check if hash_contexts table exists (m0011 three-level architecture)
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'hash_contexts'
            )
            """
        )
        has_contexts = cur.fetchone()[0]

        if has_contexts:
            cur.execute(
                """
                SELECT hash_id, source_id, side_id, COUNT(*), MIN(id) as canonical_id
                FROM hash_contexts
                GROUP BY hash_id, source_id, side_id
                HAVING COUNT(*) > 1
                """
            )
            duplicates = cur.fetchall()
            for row in duplicates:
                h_id, src_id, sd_id, cnt, canonical_id = row
                cur.execute(
                    """
                    SELECT id FROM hash_contexts
                    WHERE hash_id = %s AND source_id = %s AND side_id = %s AND id != %s
                    """,
                    (h_id, src_id, sd_id, canonical_id),
                )
                dup_ids = [r[0] for r in cur.fetchall()]
                if dup_ids:
                    placeholders = ",".join(["%s"] * len(dup_ids))
                    cur.execute(
                        f"UPDATE paths SET context_id = %s WHERE context_id IN ({placeholders})",
                        (canonical_id, *dup_ids),
                    )
                    cur.execute(
                        f"DELETE FROM hash_contexts WHERE id IN ({placeholders})",
                        tuple(dup_ids),
                    )

            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_hash_contexts_hash_source_side'
                    ) THEN
                        ALTER TABLE hash_contexts ADD CONSTRAINT uq_hash_contexts_hash_source_side UNIQUE (hash_id, source_id, side_id);
                    END IF;
                END $$;
                """
            )
        else:
            # Fallback for two-level hashs table schema
            cur.execute(
                """
                SELECT hash, source_id, side_id, COUNT(*), MIN(id) as canonical_id
                FROM hashs
                GROUP BY hash, source_id, side_id
                HAVING COUNT(*) > 1
                """
            )
            duplicates = cur.fetchall()
            for row in duplicates:
                h_val, src_id, sd_id, cnt, canonical_id = row
                cur.execute(
                    """
                    SELECT id FROM hashs
                    WHERE hash = %s AND source_id = %s AND side_id = %s AND id != %s
                    """,
                    (h_val, src_id, sd_id, canonical_id),
                )
                dup_ids = [r[0] for r in cur.fetchall()]
                if dup_ids:
                    placeholders = ",".join(["%s"] * len(dup_ids))
                    cur.execute(
                        f"UPDATE paths SET hash_id = %s WHERE hash_id IN ({placeholders})",
                        (canonical_id, *dup_ids),
                    )
                    cur.execute(
                        f"DELETE FROM hashs WHERE id IN ({placeholders})",
                        tuple(dup_ids),
                    )

            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'hashs_hash_source_id_side_id_key'
                    ) THEN
                        ALTER TABLE hashs ADD CONSTRAINT hashs_hash_source_id_side_id_key UNIQUE (hash, source_id, side_id);
                    END IF;
                END $$;
                """
            )


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE hash_contexts DROP CONSTRAINT IF EXISTS uq_hash_contexts_hash_source_side")
        cur.execute("ALTER TABLE hashs DROP CONSTRAINT IF EXISTS hashs_hash_source_id_side_id_key")
