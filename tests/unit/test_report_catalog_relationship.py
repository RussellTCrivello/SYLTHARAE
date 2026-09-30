"""Step 17 catalog addition: the Content Relationships report.

Structural contracts; the context-identity behaviour against real PostgreSQL
lives in ``tests/integration/test_report_registry_pg.py``.
"""

import pytest

from core.reporting import REGISTRY
from core.reporting.datasets import RELATIONSHIP_CONTEXTS_V1
from core.reporting.definitions import RELATIONSHIP_V1


class TestRegistered:
    def test_relationship_is_active_with_the_context_unit(self):
        report = REGISTRY.report("relationship")
        assert report.status == "active" and report.version == 1
        assert report.unit == "context", (
            "the directive: relationship reports must declare their unit - "
            "this one counts contexts, not paths")

    def test_the_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_help_topic_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert RELATIONSHIP_V1.help_topic in topics


class TestDatasetContract:
    def test_rows_are_contexts_from_hash_contexts(self):
        # The driving relation is the matched *hashes* joined to their
        # contexts - one row per context, never per path row (the first
        # draft drove from paths and duplicated rows; the PG suite caught
        # it before landing).
        sql = RELATIONSHIP_CONTEXTS_V1.sql
        assert "FROM rpt_hashes" in sql
        assert "JOIN hash_contexts hc ON hc.hash_id = rpt_hashes.hash_id" in sql
        assert any(c.name == "context_id" for c in RELATIONSHIP_CONTEXTS_V1.columns)

    def test_repeated_triples_are_not_new_relationships(self):
        # Multiplicity inside a context is a counted column (paths); the
        # relationship measure counts DISTINCT other contexts.
        assert "(SELECT COUNT(*) FROM rpt_paths rp WHERE rp.context_id = hc.id" \
            in RELATIONSHIP_CONTEXTS_V1.sql
        assert "COUNT(DISTINCT hc.id)" in RELATIONSHIP_CONTEXTS_V1.sql

    def test_sibling_sources_exclude_the_rows_own_source(self):
        assert "hc2.source_id <> hc.source_id" in RELATIONSHIP_CONTEXTS_V1.sql

    def test_capped_and_criteria_scoped(self):
        ds = RELATIONSHIP_CONTEXTS_V1
        assert ds.semantics == "capped" and ds.row_limit == 5000
        assert ds.criteria_param == "criteria"
        assert ds.sql_params == ("@criteria", "@limit")

    def test_columns_declared(self):
        assert [c.name for c in RELATIONSHIP_CONTEXTS_V1.columns] == [
            "context_id", "hash_id", "source_name", "side_name", "paths",
            "sibling_contexts", "sibling_sources", "first_path_id",
            "first_file_name"]
        assert all(not c.nullable for c in RELATIONSHIP_CONTEXTS_V1.columns), (
            "every column is total")
