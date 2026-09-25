"""Database test: archive/child title lineage (PARENT-02).

Verified defect. ``TitlesContentRepository.insert_titles_content`` accepted and
documented a ``title_content_id`` parent parameter, and
``ContentsDBService.create_title_content`` accepted and forwarded it, but the
SQL it executed was:

    INSERT INTO titles_content (title_data, title_status, path_id)
    VALUES (%s, %s, %s)

Three columns. The parent was dropped on the floor by every caller. The column
and its self-referencing foreign key already exist in m0001_initial_schema.py,
so this is a query defect, not a schema gap - no migration was needed.

Measured on a real database before the fix, ingesting container.zip -> child.pdf:

    titles_content:
      (1, 'Main', None, 1)      <- child.pdf
      (2, 'Main', None, 2)      <- container.zip
    rows with parent linkage (title_content_id NOT NULL): 0

Two further break points upstream are recorded in
PHASE2_SCHEMA_PROPOSALS.md because they interact with the hierarchy_path
decision that needs approval: ``contents_db_service.py`` calls
``create_title_content`` without a parent, and
``StoragePipeline._store_title_pipeline`` - the only code that computes
``title_status = 'Branch'`` - is dead because it calls
``self.db_hub.word_operations`` / ``.title_operations``, neither of which
exists on DatabaseHub (AttributeError, swallowed by a broad except).
"""

import datetime
import hashlib
import itertools
import os
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_SEQ = itertools.count()


def unique_hash(label: str) -> str:
    """A distinct 64-char digest per invocation.

    hashs is UNIQUE (hash, source_id, side_id), so reusing a fixed value makes
    the second test in a session collide with the first.
    """
    return hashlib.sha256(f"{label}:{os.getpid()}:{next(_SEQ)}".encode()).hexdigest()


@pytest.fixture
def conn(pg_db):
    c = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield c
    c.close()


@pytest.fixture
def two_paths(conn):
    """Two paths rows to stand in for an archive and its extracted child."""
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (f"_p02_side_{today}", today),
        )
        side_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
            " DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (f"_p02_src_{today}", today),
        )
        source_id = cur.fetchone()[0]
        # Identity-model seed: one canonical content (hashs), one context
        # (hash_contexts), one occurrence (paths) per file.
        pairs = []
        for name in ("container.zip", "child.pdf"):
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (unique_hash(name),),
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
                " file_status, file_date, date_creation, context_id)"
                " VALUES (%s, %s, 10, 'FILE', 'Unread', %s, %s, %s) RETURNING id",
                (name, f"/tmp/{name}", today, today, context_id),
            )
            pairs.append((cur.fetchone()[0], hash_id))
    conn.commit()
    return pairs


def insert_title(conn, word_ids, hash_id, title_status="Main", parent=None):
    from database.database.repository.titles_content_repo import TitlesContentRepository

    # BaseRepository(db, connection=None): passing the connection puts the repo
    # in transaction context so it uses our connection rather than opening one.
    # Titles belong to canonical content (hash_id) since migration m0011.
    repo = TitlesContentRepository(None, connection=conn)
    return repo.insert_titles_content(word_ids, hash_id, title_status, parent)


def read_title(conn, title_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title_status, title_content_id, hash_id"
            " FROM titles_content WHERE id = %s",
            (title_id,),
        )
        return cur.fetchone()


def test_parent_linkage_is_persisted(conn, two_paths):
    """The regression: a supplied parent used to be silently discarded."""
    (archive_path_id, archive_hash_id), (child_path_id, child_hash_id) = two_paths
    parent_id = insert_title(conn, [1, 2, 3], archive_hash_id, "Main")
    child_id = insert_title(conn, [4, 5], child_hash_id, "Branch", parent_id)
    conn.commit()

    row = read_title(conn, child_id)
    assert row is not None
    assert row[1] == "Branch"
    assert row[2] == parent_id, (
        f"title_content_id was {row[2]!r}, expected the parent {parent_id}; "
        "the parent is being dropped before it reaches the database"
    )


def test_linkage_is_retrievable_from_the_child(conn, two_paths):
    """Lineage must be queryable, not merely written."""
    (archive_path_id, archive_hash_id), (child_path_id, child_hash_id) = two_paths
    parent_id = insert_title(conn, [1], archive_hash_id, "Main")
    child_id = insert_title(conn, [2], child_hash_id, "Branch", parent_id)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, p_parent.file_name, c.title_status"
            " FROM titles_content c"
            " JOIN titles_content parent ON parent.id = c.title_content_id"
            " JOIN hash_contexts hc_parent ON hc_parent.hash_id = parent.hash_id"
            " JOIN paths p_parent ON p_parent.context_id = hc_parent.id"
            " WHERE c.id = %s",
            (child_id,),
        )
        row = cur.fetchone()

    assert row is not None, "the child's parent link could not be joined"
    assert row[1] == "container.zip"
    assert row[2] == "Branch"


def test_omitting_a_parent_still_works(conn, two_paths):
    """Backwards compatibility: existing callers pass no parent."""
    (_archive_path_id, archive_hash_id), _ = two_paths
    title_id = insert_title(conn, [7, 8], archive_hash_id)
    conn.commit()

    row = read_title(conn, title_id)
    assert row[1] == "Main"
    assert row[2] is None


def test_foreign_key_still_enforced(conn, two_paths):
    """Writing the column must not bypass its self-referencing constraint."""
    from database.exceptions import QueryError

    _, (_child_path_id, child_hash_id) = two_paths
    # The repository wraps driver errors, so accept either the raw psycopg2
    # error or the project's QueryError - what matters is that it propagates.
    with pytest.raises((psycopg2.Error, QueryError)):
        insert_title(conn, [9], child_hash_id, "Branch", parent=999_999)
    conn.rollback()
