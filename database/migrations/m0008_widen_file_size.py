"""Widen stored file sizes beyond PostgreSQL's signed 32-bit INTEGER.

A file larger than 2 GiB is valid input, but the original ``paths.file_size``
column was INTEGER.  Such files failed only at storage time with the opaque
``integer out of range`` error, after all extraction work had completed.
"""

version = "0008"
name = "widen_file_size"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE paths ALTER COLUMN file_size TYPE BIGINT")


def downgrade(conn) -> None:
    # Refuse to silently truncate data when rolling back.
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM paths WHERE file_size > 2147483647 LIMIT 1")
        if cur.fetchone() is not None:
            raise RuntimeError("Cannot downgrade file_size: values exceed INTEGER range")
        cur.execute("ALTER TABLE paths ALTER COLUMN file_size TYPE INTEGER")
