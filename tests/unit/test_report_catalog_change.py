"""Step 17 catalog addition: the Change report.

Structural contracts of the three per-viewer datasets and the single
revision writer; the three-states behaviour against real PostgreSQL lives
in ``tests/integration/test_report_registry_pg.py``, the whole-run
watermark advance in ``tests/integration/test_report_runs_pg.py``, and the
rename-to-report path over HTTP in ``tests/integration/test_report_catalog_api.py``.
"""

from types import SimpleNamespace

import pytest

from core.criteria.access import scope_for
from core.criteria.compiler import AccessScope
from core.reporting import REGISTRY
from core.reporting.datasets import (
    CHANGE_ADDED_V1,
    CHANGE_MODIFIED_V1,
    CHANGE_REMOVED_V1,
)
from core.reporting.definitions import CHANGE_V1
from core.reporting.model import ReportParameterError


CRITERIA = {"text": "kibbutz"}
CHANGE_DATASETS = (CHANGE_ADDED_V1, CHANGE_MODIFIED_V1, CHANGE_REMOVED_V1)


class TestRegistered:
    def test_change_is_active_with_the_path_unit(self):
        report = REGISTRY.report("change")
        assert report.status == "active" and report.version == 1
        assert report.unit == "path"

    def test_the_three_states_are_three_declared_datasets(self):
        report = REGISTRY.report("change")
        assert report.datasets == ("change.added@1", "change.modified@1",
                                   "change.removed@1")
        assert [p.name for p in report.parameters] == ["criteria"]

    def test_the_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_help_topic_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert CHANGE_V1.help_topic in topics


class TestDatasetContract:
    def test_every_state_is_read_per_viewer(self):
        for dataset in CHANGE_DATASETS:
            assert dataset.criteria_param == "criteria"
            assert dataset.sql_params.count("@viewer_id") == 1
            assert dataset.sql_params.count("@viewer_key") == 1
            assert dataset.progress_column == "path_id"

    def test_bind_refuses_a_scope_without_a_reader(self):
        for dataset in CHANGE_DATASETS:
            with pytest.raises(ReportParameterError):
                dataset.bind({"criteria": CRITERIA}, AccessScope.system())

    def test_the_three_states_share_one_view_watermark(self):
        # One reader, one view: the three datasets of one run read and
        # advance the same baseline row - a report whose halves disagree
        # about what the reader has seen would be incoherent.
        scope = scope_for(SimpleNamespace(id=9, role="analyst"))
        keys = {d.bind({"criteria": CRITERIA}, scope).progress_key
                for d in CHANGE_DATASETS}
        assert len(keys) == 1

    def test_latest_and_change_share_the_watermark_of_one_view(self):
        # The baseline is kept per distinct view, not per report (the
        # store's own contract): the same criteria shape the same view,
        # whichever report reads it - "what changed since you last looked"
        # answers for the set you looked at, however you looked at it.
        from core.reporting.datasets import LATEST_ENTRIES_V1

        scope = scope_for(SimpleNamespace(id=9, role="analyst"))
        latest = LATEST_ENTRIES_V1.bind({"criteria": CRITERIA}, scope)
        change = CHANGE_ADDED_V1.bind({"criteria": CRITERIA}, scope)
        assert latest.progress_key == change.progress_key

    def test_modified_and_removed_read_the_revision_log(self):
        for dataset in (CHANGE_MODIFIED_V1, CHANGE_REMOVED_V1):
            sql = dataset.sql
            assert "FROM path_revisions rpt_rev" in sql
            assert "JOIN paths p ON p.id = rpt_rev.path_id" in sql
            # The kind is part of the dataset's own fixed fragment, one
            # state per dataset - never a request parameter.
            assert "rpt_rev.change_kind = '" in sql
            # The watermark condition uses bound parameters, never values.
            assert "rpt_rev.changed_at > COALESCE(rpt_bl.last_seen_at," in sql

    def test_added_is_measured_from_the_ingestion_date(self):
        sql = CHANGE_ADDED_V1.sql
        assert "p.date_creation::timestamptz AS detected_at" in sql
        assert "(rpt_bl.last_seen_at AT TIME ZONE 'UTC')::date" in sql, (
            "the watermark comparison is pinned to UTC, not the session zone")

    def test_previous_and_current_values_are_declared_nullable(self):
        for dataset in CHANGE_DATASETS:
            by_name = {c.name: c for c in dataset.columns}
            # A revision records whichever fields its operation changed;
            # a future operation may record no file_name - unknown, never
            # an empty string. (Added rows have no previous value at all.)
            assert by_name["previous_value"].nullable is True
        assert {c.name for c in CHANGE_MODIFIED_V1.columns} >= {
            "path_id", "revision_id", "detected_at", "previous_value",
            "current_value", "change_kind"}

    def test_state_and_order_are_deterministic(self):
        assert "ORDER BY rpt_rev.changed_at DESC, rpt_rev.id DESC" in \
            CHANGE_MODIFIED_V1.sql
        assert "ORDER BY p.date_creation DESC, p.id DESC" in CHANGE_ADDED_V1.sql
        assert "'added' AS change_kind" in CHANGE_ADDED_V1.sql


class TestTheRevisionWriter:
    def test_unknown_kinds_and_non_object_values_are_refused(self):
        from services.changes import record_path_revision

        with pytest.raises(ValueError):
            record_path_revision(1, "created", old_values={}, new_values={})
        with pytest.raises(ValueError):
            record_path_revision(1, "modified", old_values=None,  # type: ignore[arg-type]
                                 new_values={})

    def test_the_kind_vocabulary_is_exactly_the_two_transitions(self):
        from services.changes import KINDS

        assert KINDS == ("modified", "removed"), (
            "creation is an event of paths.date_creation, not of this log")
