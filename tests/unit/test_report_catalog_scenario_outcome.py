"""Step 17 catalog addition: the Scenario Outcome report.

Structural contracts of the owner-scoped dataset and the definition's
declared access control; the authorization behaviour against real
PostgreSQL lives in ``tests/integration/test_report_registry_pg.py`` (the
SQL predicate) and ``tests/integration/test_report_runs_pg.py`` (the
submit-time refusal), and over HTTP in
``tests/integration/test_report_catalog_api.py``.
"""

from types import SimpleNamespace

import pytest

from core.criteria.access import scope_for
from core.criteria.compiler import AccessScope
from core.reporting import REGISTRY
from core.reporting.datasets import SCENARIO_OUTCOMES_V1
from core.reporting.definitions import SCENARIO_OUTCOME_V1
from core.reporting.model import ReportParameterError


class TestRegistered:
    def test_scenario_outcome_is_active_with_its_declared_unit(self):
        report = REGISTRY.report("scenario_outcome")
        assert report.status == "active" and report.version == 1
        assert report.unit == "scenario_outcome", (
            "the unit the model declared for this family")

    def test_the_scenario_is_the_only_parameter(self):
        report = REGISTRY.report("scenario_outcome")
        assert [(p.name, p.type, p.required, p.minimum)
                for p in report.parameters] == [("scenario_id", "integer",
                                                 True, 1)]
        assert report.datasets == ("scenario.outcomes@1",)

    def test_the_access_decision_is_declared_on_the_definition(self):
        assert SCENARIO_OUTCOME_V1.access_control == "scenario_owner", (
            "the per-scenario access decision: owner and admins read, "
            "refused pre-retrieval")

    def test_the_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_help_topic_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert SCENARIO_OUTCOME_V1.help_topic in topics


class TestDatasetContract:
    def test_rows_are_owner_scoped_before_retrieval(self):
        dataset = SCENARIO_OUTCOMES_V1
        assert dataset.owner_column == "rpt_sc.owner_user_id"
        assert dataset.progress_column is None, (
            "outcome history is not a per-reader view - no watermark")
        sql = dataset.sql
        assert "{owner}" in sql
        # Defense in depth: the reader's own source scope applies too.
        assert "{scope}" in sql and "hc.hash_id = rpt_o.hash_id AND {scope}" in sql

    def test_the_owner_predicate_is_bound_never_interpolated(self):
        # Both %s of the ownership decision are the reader's id, bound
        # from the scope - the SQL text itself carries no identity.
        assert SCENARIO_OUTCOMES_V1.sql_params == (
            "scenario_id", "@viewer_id", "@viewer_id", "@scope", "@limit")

    def test_the_history_is_newest_first_and_append_only_shaped(self):
        sql = SCENARIO_OUTCOMES_V1.sql
        assert "FROM scenario_outcomes rpt_o" in sql
        assert "ORDER BY rpt_o.recorded_at DESC, rpt_o.id DESC" in sql

    def test_previous_outcomes_and_priority_are_declared_nullable(self):
        by_name = {c.name: c for c in SCENARIO_OUTCOMES_V1.columns}
        assert by_name["previous_outcomes"].nullable is True
        assert by_name["priority"].nullable is True, (
            "baseline and recorded rows carry no derived priority - "
            "NULL is not low")
        assert by_name["outcomes"].type == "json"
        assert by_name["file_name"].nullable is True, (
            "a content whose every path is gone keeps its history but "
            "has no name - unknown, never an empty string")

    def test_bind_refuses_a_scope_without_a_reader(self):
        with pytest.raises(ReportParameterError):
            SCENARIO_OUTCOMES_V1.bind({"scenario_id": 1}, AccessScope.system())

    def test_bind_carries_the_scenario_and_the_reader_twice(self):
        scope = scope_for(SimpleNamespace(id=31, role="viewer"))
        bound = SCENARIO_OUTCOMES_V1.bind({"scenario_id": 9}, scope)
        assert bound.params == (9, 31, 31, 5001)
        assert "rpt_sc.owner_user_id = %s" in bound.sql
        assert "rpt_adm.role = 'admin'" in bound.sql
        assert bound.progress_key is None
