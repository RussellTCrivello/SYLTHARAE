"""Analyst-driven manual categorization (FRS: Analyst-Driven Manual
Categorization with Scoped Search Control).

Introduces a data model that is **fully separate** from the existing
system-generated ("smart") classification taxonomy
(``categorys`` / ``words_categorys`` / ``keywords``):

* ``analyst_categories``          - analyst-defined category labels
* ``analyst_file_categories``     - manual assignments of those labels to files
* ``analyst_categorization_log``  - audit trail (FR-1.5)

Design constraints (NFR-1 / NFR-2 of the spec):

* ``analyst_categories`` shares NO table, column, FK or join path with the
  smart taxonomy tables. There is deliberately no ``type`` flag on a shared
  "category" table; the two namespaces can never be merged by a query.
* Assignments reference ``paths`` directly, so nothing here can overwrite or
  modify smart classification values, and vice versa.
* ``analyst_id`` columns are intentionally FK-free (mirroring ``audit_log``):
  the audit trail must survive user deletion.
* Scope filtering (FR-2.x) runs on ``analyst_file_categories`` alone; smart
  categorization status is never consulted.
"""

version = "0009"
name = "analyst_categorization"

SQL_STATEMENTS = [
    # --- analyst_categories ---------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS analyst_categories (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        description TEXT NULL,
        color VARCHAR(7) NULL,
        created_by INTEGER NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_analyst_categories_name ON analyst_categories (name)",
    "CREATE INDEX IF NOT EXISTS idx_analyst_categories_created_by ON analyst_categories (created_by)",
    # Case-insensitive uniqueness. The service checks LOWER(name) before
    # inserting, but the plain UNIQUE constraint above is case-sensitive, so
    # two concurrent creations could otherwise both land ("Foo" vs "foo").
    # The service treats a violation of this index as a duplicate.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_categories_lower_name ON analyst_categories (LOWER(name))",
    # --- analyst_file_categories ---------------------------------------
    """
    CREATE TABLE IF NOT EXISTS analyst_file_categories (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        path_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        assigned_by INTEGER NULL,
        assigned_by_username VARCHAR(64) NULL,
        source_query TEXT NULL,
        assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        FOREIGN KEY (path_id) REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (category_id) REFERENCES analyst_categories(id) ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT unique_analyst_file_category UNIQUE (path_id, category_id)
    )
    """,
    # Primary scope-filter index: EXISTS/NOT EXISTS probes on path_id
    # (NFR-4 - scope-filtered searches must stay as fast as unfiltered ones).
    "CREATE INDEX IF NOT EXISTS idx_afc_path_id ON analyst_file_categories (path_id)",
    "CREATE INDEX IF NOT EXISTS idx_afc_category_id ON analyst_file_categories (category_id)",
    "CREATE INDEX IF NOT EXISTS idx_afc_assigned_by ON analyst_file_categories (assigned_by)",
    "CREATE INDEX IF NOT EXISTS idx_afc_assigned_at ON analyst_file_categories (assigned_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_afc_category_assigned_at ON analyst_file_categories (category_id, assigned_at DESC)",
    # --- analyst_categorization_log (FR-1.5 auditability) ---------------
    """
    CREATE TABLE IF NOT EXISTS analyst_categorization_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        analyst_id INTEGER NULL,
        analyst_username VARCHAR(64) NULL,
        action VARCHAR(32) NOT NULL,
        category_id INTEGER NULL,
        category_name VARCHAR(255) NULL,
        source_query TEXT NULL,
        path_ids JSONB NOT NULL,
        path_count INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_acl_created_at ON analyst_categorization_log (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_acl_analyst_id ON analyst_categorization_log (analyst_id)",
    "CREATE INDEX IF NOT EXISTS idx_acl_action ON analyst_categorization_log (action)",
    "CREATE INDEX IF NOT EXISTS idx_acl_category_id ON analyst_categorization_log (category_id)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)
