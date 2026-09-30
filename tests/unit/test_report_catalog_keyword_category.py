"""Step 17 catalog additions: Keyword Intelligence and Category Analysis.

Structural checks at unit level; the datasets' SQL behaviour against real
PostgreSQL lives in ``tests/integration/test_report_registry_pg.py`` (whose
corpus seeds keywords and categories) — parsing alone proves nothing here.
"""

import pytest

from core.reporting import REGISTRY
from core.reporting.datasets import (
    CATEGORY_ANALYSIS_SUMMARY_V1,
    KEYWORD_INTELLIGENCE_MATCHES_V1,
)
from core.reporting.definitions import (
    CATEGORY_ANALYSIS_V1,
    KEYWORD_INTELLIGENCE_V1,
)
from core.reporting.model import UNITS


class TestTheTwoNewFamiliesAreRegistered:
    def test_both_report_versions_are_active_and_reachable(self):
        for report_id in ("keyword_intelligence", "category_analysis"):
            report = REGISTRY.report(report_id)
            assert report.status == "active"
            assert report.version == 1

    def test_the_whole_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_the_keyword_unit_is_declared_vocabulary(self):
        assert "keyword" in UNITS

    def test_unit_agrees_with_the_primary_dataset(self):
        assert KEYWORD_INTELLIGENCE_V1.unit == \
            REGISTRY.dataset("keyword_intelligence.matches@1").unit == "keyword"
        assert CATEGORY_ANALYSIS_V1.unit == \
            REGISTRY.dataset("category_analysis.summary@1").unit == "category"

    def test_roles_are_the_reader_roles_of_their_datasets(self):
        for report in (KEYWORD_INTELLIGENCE_V1, CATEGORY_ANALYSIS_V1):
            assert set(report.roles) <= set(
                REGISTRY.dataset(report.datasets[0]).roles)

    def test_help_topics_are_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert KEYWORD_INTELLIGENCE_V1.help_topic in topics
        assert CATEGORY_ANALYSIS_V1.help_topic in topics


class TestTheDatasetsDeclareTheirContract:
    def test_keyword_intelligence_declares_capped_rows_and_columns(self):
        ds = KEYWORD_INTELLIGENCE_MATCHES_V1
        assert ds.semantics == "capped" and ds.row_limit == 1000
        assert [c.name for c in ds.columns] == [
            "keyword_id", "label", "category_name", "contents",
            "occurrences", "unknown_count_rows", "corpus_contents"]
        # label may be empty-but-present; the measured columns are total
        nullable = {c.name: c.nullable for c in ds.columns}
        assert not any(nullable[n] for n in
                       ("keyword_id", "contents", "occurrences",
                        "unknown_count_rows", "corpus_contents"))

    def test_category_analysis_declares_the_share_as_nullable(self):
        ds = CATEGORY_ANALYSIS_SUMMARY_V1
        assert ds.semantics == "capped" and ds.row_limit == 1000
        share = next(c for c in ds.columns if c.name == "content_share")
        assert share.nullable is True, (
            "an empty matched set has no share: unknown, never zero")
        assert share.type == "numeric"

    def test_unknown_counts_are_never_read_as_zero(self):
        for ds in (KEYWORD_INTELLIGENCE_MATCHES_V1, CATEGORY_ANALYSIS_SUMMARY_V1):
            unknown = next(c for c in ds.columns if c.name == "unknown_count_rows")
            assert unknown.nullable is False

    def test_access_goes_through_the_criteria_compiler(self):
        for ds in (KEYWORD_INTELLIGENCE_MATCHES_V1, CATEGORY_ANALYSIS_SUMMARY_V1):
            assert ds.criteria_param == "criteria"
            assert ds.sql_params == ("@criteria", "@limit")
            assert ds.sql.count("LIMIT %s") == 1 and ds.sql.rstrip().endswith("LIMIT %s")

    def test_a_hostile_phrase_is_a_bound_parameter_not_sql(self):
        import re
        hostile = {"criteria": {"phrases": ["x'); DELETE FROM keywords; --"]}}
        for report_id in ("keyword_intelligence", "category_analysis"):
            values = REGISTRY.report(report_id).normalize_parameters(hostile)
            ds = REGISTRY.dataset(
                REGISTRY.report(report_id).datasets[0])
            bound = ds.bind(values, __import__(
                "core.criteria", fromlist=["AccessScope"]).AccessScope.unrestricted())
            # The compiler tokenises the phrase into a match regex carried as
            # a bound parameter; the SQL text itself never contains it.
            assert "DELETE" not in bound.sql
            assert any("DELETE" in str(p) for p in bound.params)

    def test_the_sql_mentions_its_declared_stores_only(self):
        import re
        # Both reports read content-level stores - keywords_hashs and
        # words_hashs x words_categorys, the same content identity the
        # criteria compiler uses; the path-keyed legacy names are gone.
        assert "keywords_hashs" in KEYWORD_INTELLIGENCE_MATCHES_V1.sql
        assert not re.search(r"\bkeywords_paths\b|\bwords_paths\b",
                             KEYWORD_INTELLIGENCE_MATCHES_V1.sql)
        assert "words_hashs" in CATEGORY_ANALYSIS_SUMMARY_V1.sql
        assert "words_categorys" in CATEGORY_ANALYSIS_SUMMARY_V1.sql


class TestParameters:
    def test_criteria_is_the_only_parameter_and_it_is_required(self):
        for report in (KEYWORD_INTELLIGENCE_V1, CATEGORY_ANALYSIS_V1):
            assert [p.name for p in report.parameters] == ["criteria"]
            assert report.parameters[0].required is True

    def test_normalize_round_trips_a_criteria_dict(self):
        values = KEYWORD_INTELLIGENCE_V1.normalize_parameters(
            {"criteria": {"text": "alpha"}})
        assert values["criteria"]["text"] == "alpha"
        with pytest.raises(Exception):
            KEYWORD_INTELLIGENCE_V1.normalize_parameters({})
