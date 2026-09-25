"""Database tests for migration 0007 (provenance, hierarchy, processing status).

The pg_db fixture runs the real bootstrap, so m0007 is already applied here.
These tests assert what the migration actually produced against a live
PostgreSQL instance, including the rollback path.
"""

import datetime
import itertools
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from database.migrations import m0007_provenance_hierarchy_status as m0007  # noqa: E402

pytestmark = pytest.mark.integration

#: Session-wide, not per-fixture: hash_contexts is
#: UNIQUE (hash_id, source_id, side_id) and the database is shared across tests
#: in a session, so a per-test counter makes two tests that use the same
#: default label collide.
_SEQ = itertools.count()


@pytest.fixture
def conn(pg_db):
    c = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield c
    c.close()


def columns(conn, table="paths"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


@pytest.fixture
def make_path(conn):
    """Insert a paths row with the given legacy file_status."""

    def _make(file_status="Unread", parent_id=None, name=None):
        today = datetime.date.today()
        label = name or f"m0007_{next(_SEQ)}"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 0.5, %s) ON CONFLICT (name)"
                " DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (f"_m0007_side_{today}", today),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
                " DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (f"_m0007_src_{today}", today),
            )
            source_id = cur.fetchone()[0]
            # A real 64-hex digest; pgcrypto is not enabled in this schema.
            import hashlib

            digest = hashlib.sha256(
                f"{label}:{next(_SEQ)}".encode()
            ).hexdigest()
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (digest,),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO paths (file_name, file_path, file_size, file_type,"
                " file_status, file_date, date_creation, context_id, parent_path_id)"
                " VALUES (%s, %s, 10, 'FILE', %s, %s, %s, %s, %s) RETURNING id",
                (label, f"/tmp/{label}", file_status, today, today, context_id, parent_id),
            )
            path_id = cur.fetchone()[0]
        conn.commit()
        return path_id

    return _make


# ----------------------------------------------------------------------
# A1 - provenance
# ----------------------------------------------------------------------
class TestProvenanceColumn:
    def test_column_exists_and_is_jsonb(self, conn):
        assert "extraction_provenance" in columns(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_name = 'paths'"
                " AND column_name = 'extraction_provenance'"
            )
            assert cur.fetchone()[0] == "jsonb"

    def test_null_for_existing_rows_is_the_truthful_default(self, conn, make_path):
        path_id = make_path()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extraction_provenance FROM paths WHERE id = %s", (path_id,)
            )
            assert cur.fetchone()[0] is None

    def test_round_trips_the_documented_shape(self, conn, make_path):
        path_id = make_path()
        payload = {
            "ocr": {
                "engine": "rapidocr",
                "engine_version": "1.4.4 (PP-OCRv4 onnx)",
                "confidence": 0.9763,
                "derived": True,
                "input_variant": "original",
            }
        }
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paths SET extraction_provenance = %s::jsonb WHERE id = %s",
                (psycopg2.extras.Json(payload), path_id),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extraction_provenance->'ocr'->>'engine',"
                " (extraction_provenance->'ocr'->>'derived')::boolean,"
                " (extraction_provenance->'ocr'->>'confidence')::real"
                " FROM paths WHERE id = %s",
                (path_id,),
            )
            engine, derived, confidence = cur.fetchone()
        assert engine == "rapidocr"
        assert derived is True
        assert confidence == pytest.approx(0.9763, abs=1e-3)


# ----------------------------------------------------------------------
# B - hierarchy
# ----------------------------------------------------------------------
class TestHierarchyColumns:
    def test_both_columns_exist(self, conn):
        present = columns(conn)
        assert "parent_path_id" in present
        assert "hierarchy_path" in present

    def test_parent_is_indexed(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_paths_parent_path_id'"
            )
            assert cur.fetchone() is not None

    def test_child_resolves_to_its_parent(self, conn, make_path):
        archive = make_path(name="archive.zip")
        child = make_path(name="child.pdf", parent_id=archive)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name FROM paths c"
                " JOIN paths p ON p.id = c.parent_path_id WHERE c.id = %s",
                (child,),
            )
            assert cur.fetchone()[0] == "archive.zip"

    def test_deleting_a_parent_nulls_rather_than_cascades(self, conn, make_path):
        """An archive deletion must not destroy indexed children."""
        archive = make_path(name="to_delete.zip")
        child = make_path(name="survivor.pdf", parent_id=archive)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM paths WHERE id = %s", (archive,))
            conn.commit()
            cur.execute(
                "SELECT parent_path_id FROM paths WHERE id = %s", (child,)
            )
            row = cur.fetchone()
        assert row is not None, "the child was deleted instead of orphaned"
        assert row[0] is None

    def test_bogus_parent_is_rejected(self, conn, make_path):
        with pytest.raises(psycopg2.Error):
            make_path(parent_id=999_999)
        conn.rollback()


# ----------------------------------------------------------------------
# C - processing status
# ----------------------------------------------------------------------
class TestProcessingStatus:
    def test_column_exists_with_the_nine_states(self, conn):
        assert "processing_status" in columns(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid)"
                " FROM pg_constraint WHERE conrelid = 'paths'::regclass"
                " AND pg_get_constraintdef(oid) LIKE '%processing_status%'"
            )
            definition = cur.fetchone()[1]
        for state in m0007.PROCESSING_STATES:
            assert f"'{state}'" in definition, f"{state} missing from {definition}"

    def test_file_status_is_untouched(self, conn):
        """file_status must survive so the 57 existing references keep working."""
        assert "file_status" in columns(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = 'paths'::regclass"
                " AND pg_get_constraintdef(oid) LIKE '%file_status%'"
            )
            definition = cur.fetchone()[0]
        assert "'Read'" in definition and "'Unread'" in definition

    def test_illegal_state_is_rejected(self, conn, make_path):
        path_id = make_path()
        with pytest.raises(psycopg2.Error):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE paths SET processing_status = 'exploded' WHERE id = %s",
                    (path_id,),
                )
        conn.rollback()

    def test_long_states_fit_the_column(self, conn, make_path):
        """'partially_processed' is 19 chars; the old VARCHAR(10) could not hold it."""
        path_id = make_path()
        for state in ("partially_processed", "unsupported"):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE paths SET processing_status = %s WHERE id = %s",
                    (state, path_id),
                )
            conn.commit()

    def test_backfill_restates_existing_rows(self, conn, make_path):
        read_id = make_path(file_status="Read", name="was_read.txt")
        unread_id = make_path(file_status="Unread", name="was_unread.txt")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paths SET processing_status = 'discovered'"
                " WHERE id IN (%s, %s)",
                (read_id, unread_id),
            )
        conn.commit()
        m0007.upgrade(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, processing_status FROM paths WHERE id IN (%s, %s)",
                (read_id, unread_id),
            )
            mapping = dict(cur.fetchall())
        assert mapping[read_id] == "processed"
        assert mapping[unread_id] == "discovered"

    def test_backfill_is_idempotent(self, conn, make_path):
        """Running the migration twice must not change the outcome."""
        path_id = make_path(file_status="Read", name="idempotent.txt")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paths SET processing_status = 'processed' WHERE id = %s",
                (path_id,),
            )
        conn.commit()
        # A second run must not downgrade an already-processed row.
        m0007.upgrade(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT processing_status FROM paths WHERE id = %s", (path_id,)
            )
            assert cur.fetchone()[0] == "processed"

    def test_helper_columns_exist(self, conn):
        present = columns(conn)
        for column in ("status_detail", "attempts", "status_updated_at"):
            assert column in present

    def test_attempts_defaults_to_zero(self, conn, make_path):
        path_id = make_path()
        with conn.cursor() as cur:
            cur.execute("SELECT attempts FROM paths WHERE id = %s", (path_id,))
            assert cur.fetchone()[0] == 0


# ----------------------------------------------------------------------
# Migration hygiene
# ----------------------------------------------------------------------
class TestMigrationHygiene:
    def test_upgrade_is_idempotent_on_an_upgraded_database(self, conn):
        """Bootstrap already ran it; running it again must not fail."""
        m0007.upgrade(conn)
        conn.commit()
        m0007.upgrade(conn)
        conn.commit()

    def test_downgrade_removes_everything_it_added(self, conn):
        before = set(columns(conn))
        m0007.downgrade(conn)
        conn.commit()
        after = set(columns(conn))

        removed = before - after
        assert removed == {
            "extraction_provenance", "parent_path_id", "hierarchy_path",
            "processing_status", "status_detail", "attempts", "status_updated_at",
        }, removed
        assert "file_status" in after, "downgrade must not touch file_status"

        # Re-applying restores the schema.
        m0007.upgrade(conn)
        conn.commit()
        assert set(columns(conn)) == before

    def test_version_and_name_follow_the_convention(self):
        assert m0007.version == "0007"
        assert m0007.name == "provenance_hierarchy_status"

    def test_migration_is_registered_and_applied(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM schema_migrations WHERE version = '0007'"
            )
            row = cur.fetchone()
        assert row is not None, "m0007 was not applied by bootstrap"
        assert row[0] == "provenance_hierarchy_status"
