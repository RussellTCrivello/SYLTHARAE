"""Step 17 catalog addition: the Latest Entries report.

Structural contracts of the per-viewer dataset; the classification
behaviour against real PostgreSQL lives in
``tests/integration/test_report_registry_pg.py`` and the baseline advance
of a whole run in ``tests/integration/test_report_runs_pg.py``.
"""

from types import SimpleNamespace

import pytest

from core.criteria.access import scope_for
from core.criteria.compiler import AccessScope
from core.reporting import REGISTRY
from core.reporting.baselines import baseline_key
from core.reporting.datasets import LATEST_ENTRIES_V1
from core.reporting.definitions import LATEST_V1
from core.reporting.model import ReportParameterError


CRITERIA = {"text": "kibbutz"}


class TestRegistered:
    def test_latest_is_active_with_the_path_unit(self):
        report = REGISTRY.report("latest")
        assert report.status == "active" and report.version == 1
        assert report.unit == "path", (
            "the directive: every report declares its unit - Latest lists "
            "file occurrences, so its unit is the path")

    def test_criteria_is_the_only_parameter(self):
        report = REGISTRY.report("latest")
        assert [p.name for p in report.parameters] == ["criteria"]
        assert report.datasets == ("latest.entries@1",)

    def test_the_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_help_topic_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert LATEST_V1.help_topic in topics

    def test_every_reader_role_may_run_it(self):
        report = REGISTRY.report("latest")
        dataset = REGISTRY.dataset("latest.entries@1")
        assert set(dataset.roles) == set(report.roles)


class TestDatasetContract:
    def test_the_baseline_join_is_bound_not_interpolated(self):
        # The reader's identity and view key arrive as bound parameters
        # (the last two %s before the limit), never as formatted SQL.
        sql = LATEST_ENTRIES_V1.sql
        assert "LEFT JOIN report_baselines rpt_bl" in sql
        assert "rpt_bl.user_id = %s AND rpt_bl.criteria_hash = %s" in sql
        assert LATEST_ENTRIES_V1.sql_params == (
            "@criteria", "@viewer_id", "@viewer_key", "@limit")

    def test_rows_are_classified_against_the_readers_progress(self):
        sql = LATEST_ENTRIES_V1.sql
        assert "previously_seen" in sql and "new_since_last_view" in sql
        assert "p.id <= rpt_bl.last_max_id" in sql

    def test_newest_first_with_a_unique_tiebreaker(self):
        assert "ORDER BY p.date_creation DESC, p.id DESC" in LATEST_ENTRIES_V1.sql

    def test_progress_column_declared(self):
        assert LATEST_ENTRIES_V1.progress_column == "path_id"
        assert any(c.name == "path_id" for c in LATEST_ENTRIES_V1.columns)

    def test_the_view_key_requires_the_viewer_id(self):
        with pytest.raises(Exception) as exc:
            LATEST_ENTRIES_V1.__class__(
                dataset_id="x.bad", version=1, description="d", unit="path",
                semantics="capped", row_limit=10,
                columns=LATEST_ENTRIES_V1.columns, sql="SELECT 1 LIMIT %s",
                sql_params=("@viewer_key", "@limit"), roles=("admin",),
                criteria_param="criteria")
        assert "@viewer_key requires @viewer_id" in str(exc.value)

    def test_progress_column_requires_the_viewer_tokens(self):
        with pytest.raises(Exception) as exc:
            LATEST_ENTRIES_V1.__class__(
                dataset_id="x.bad2", version=1, description="d", unit="path",
                semantics="capped", row_limit=10,
                columns=LATEST_ENTRIES_V1.columns, sql="SELECT 1 LIMIT %s",
                sql_params=("@viewer_id", "@limit"), roles=("admin",),
                criteria_param="criteria", progress_column="path_id")
        assert "progress_column requires @viewer_id and @viewer_key" in str(exc.value)

    def test_bind_refuses_a_scope_without_a_reader(self):
        # Fail closed: a viewer dataset without an authenticated reader
        # cannot be bound - a system process must not read it (and thereby
        # advance nobody's baseline).
        with pytest.raises(ReportParameterError):
            LATEST_ENTRIES_V1.bind({"criteria": CRITERIA}, AccessScope.system())

    def test_bind_carries_the_reader_and_the_derived_view_key(self):
        scope = scope_for(SimpleNamespace(id=42, role="analyst"))
        bound = LATEST_ENTRIES_V1.bind({"criteria": CRITERIA}, scope)
        assert bound.progress_key == baseline_key(bound.criteria_fingerprint, {})
        # ... id, key, limit - in sql_params order, after the criteria's own
        assert bound.params[-3:] == (42, bound.progress_key,
                                     LATEST_ENTRIES_V1.row_limit + 1)
