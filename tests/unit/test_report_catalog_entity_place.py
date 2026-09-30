"""Step 17 catalog addition: the Entity & Place report.

Structural contracts. The ambiguity behaviour against real PostgreSQL lives
in ``tests/integration/test_report_registry_pg.py``; the HTTP lifecycle in
``tests/integration/test_report_catalog_api.py``.
"""

import pytest

from core.detection import place_intel
from core.reporting import REGISTRY
from core.reporting.datasets import ENTITY_PLACE_MENTIONS_V1
from core.reporting.definitions import ENTITY_PLACE_V1


class TestRegistered:
    def test_entity_place_is_active_with_its_own_unit(self):
        report = REGISTRY.report("entity_place")
        assert report.status == "active" and report.version == 1
        assert report.unit == "place"

    def test_the_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_help_topic_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert ENTITY_PLACE_V1.help_topic in topics

    def test_criteria_is_the_only_parameter(self):
        assert [(p.name, p.type, p.required) for p in ENTITY_PLACE_V1.parameters] == \
            [("criteria", "criteria", True)]


class TestDatasetContract:
    def test_capped_places_ordered_by_identified_reach(self):
        ds = ENTITY_PLACE_MENTIONS_V1
        assert ds.semantics == "capped" and ds.row_limit == 2000
        assert "ORDER BY rpt_h.ident_contents DESC, rpt_h.ident_occ DESC," in ds.sql

    def test_reads_the_candidate_table_not_a_resolution(self):
        # Ambiguity is preserved through content_signal_places (one candidate
        # = identified, several = ambiguous): the dataset reads the
        # candidates and splits by the signal's own resolution - it never
        # picks among candidates in SQL.
        assert "content_signal_places" in ENTITY_PLACE_MENTIONS_V1.sql
        assert "geo_places" in ENTITY_PLACE_MENTIONS_V1.sql

    def test_identified_and_ambiguous_are_distinct_columns(self):
        names = [c.name for c in ENTITY_PLACE_MENTIONS_V1.columns]
        for name in ("identified_contents", "identified_occurrences",
                     "ambiguous_contents", "ambiguous_occurrences"):
            assert name in names
        sql = ENTITY_PLACE_MENTIONS_V1.sql
        assert sql.count("FILTER (WHERE s.resolution = 'identified')") == 2
        assert sql.count("FILTER (WHERE s.resolution = 'ambiguous')") == 2

    def test_unprovenanced_confidence_is_carried_not_zeroed(self):
        sql = ENTITY_PLACE_MENTIONS_V1.sql
        assert "AND s.confidence IS NULL" in sql
        unknown = next(c for c in ENTITY_PLACE_MENTIONS_V1.columns
                       if c.name == "unknown_confidence_occurrences")
        assert unknown.nullable is False

    def test_place_signals_only(self):
        assert f"s.detector = '{place_intel.DETECTOR_NAME}'" in \
            ENTITY_PLACE_MENTIONS_V1.sql
        assert f"s.signal_type = '{place_intel.SIGNAL_PLACE}'" in \
            ENTITY_PLACE_MENTIONS_V1.sql

    def test_access_goes_through_the_criteria_compiler(self):
        ds = ENTITY_PLACE_MENTIONS_V1
        assert ds.criteria_param == "criteria"
        assert ds.sql_params == ("@criteria", "@limit")
        assert ds.sql.rstrip().endswith("LIMIT %s")

    def test_columns_declared(self):
        assert [c.name for c in ENTITY_PLACE_MENTIONS_V1.columns] == [
            "place_id", "place_label", "feature_type", "country_codes",
            "retired", "identified_contents", "identified_occurrences",
            "ambiguous_contents", "ambiguous_occurrences",
            "unknown_confidence_occurrences", "first_path_id",
            "first_file_name"]
        nullable = {c.name: c.nullable for c in ENTITY_PLACE_MENTIONS_V1.columns}
        assert not any(nullable.values()), (
            "every column is total: zero and unknown are expressed as values")
