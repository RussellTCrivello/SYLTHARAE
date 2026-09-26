"""``docs/reference/DATABASE_SCHEMA.md`` matches what the migrations build.

Regenerate after adding a migration::

    python3 tools/docs/generate_schema.py
    # or, without a configured database:
    INFORAXIS_WRITE_EVIDENCE=1 python3 -m pytest tests/integration/test_schema_reference.py
"""

import os
import sys
from pathlib import Path

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.docs.generate_schema import TARGET, generate  # noqa: E402

pytestmark = pytest.mark.integration


def test_schema_reference_matches_the_migrated_database(pg_db):
    connection = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                                  password=pg_db["password"], dbname=pg_db["database"])
    try:
        generated = generate(connection)
    finally:
        connection.close()
    if os.environ.get("INFORAXIS_WRITE_EVIDENCE") == "1":
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(generated, encoding="utf-8")
        pytest.skip("schema reference regenerated")
    assert TARGET.exists(), "run python3 tools/docs/generate_schema.py"
    assert TARGET.read_text(encoding="utf-8") == generated, (
        "docs/reference/DATABASE_SCHEMA.md is stale; run python3 tools/docs/generate_schema.py")
