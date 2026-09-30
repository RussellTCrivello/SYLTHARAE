"""Step 17 catalog addition: the Horizon report.

Structural contracts; the bucket behaviour against real PostgreSQL lives in
``tests/integration/test_report_registry_pg.py`` and the HTTP lifecycle in
``tests/integration/test_report_catalog_api.py``.
"""

import pytest

from core.detection import horizon as horizon_defs
from core.detection import temporal_intel
from core.reporting import REGISTRY
from core.reporting.datasets import HORIZON_SIGNALS_V1
from core.reporting.definitions import HORIZON_V1


class TestRegistered:
    def test_horizon_is_active_with_its_own_unit(self):
        report = REGISTRY.report("horizon")
        assert report.status == "active" and report.version == 1
        assert report.unit == "signal"

    def test_the_registry_still_validates(self):
        assert REGISTRY.validate() == []

    def test_help_topic_declared_and_used(self):
        topics = {t.topic for t in REGISTRY.help_topics}
        assert HORIZON_V1.help_topic in topics

    def test_parameters_are_criteria_and_a_required_reference_date(self):
        names = {p.name: p for p in HORIZON_V1.parameters}
        assert set(names) == {"criteria", "as_of"}
        assert names["criteria"].type == "criteria"
        assert names["as_of"].type == "date"
        assert names["as_of"].required is True, (
            "the reference date is part of the run's recorded identity: "
            "without it the buckets are not reproducible")


class TestDatasetContract:
    def test_capped_signals_in_event_date_order(self):
        ds = HORIZON_SIGNALS_V1
        assert ds.semantics == "capped" and ds.row_limit == 5000
        assert "ORDER BY s.date_from ASC, s.id ASC" in ds.sql

    def test_columns_declared_with_provenance_nullability(self):
        nullable = {c.name: c.nullable for c in HORIZON_SIGNALS_V1.columns}
        # Provenance columns arrived with m0018; signals stored before it
        # carry NULL - unknown, never zero.
        assert nullable["confidence"] is True
        assert nullable["method"] is True
        assert nullable["evidence_sentence"] is True
        # Detection facts are never NULL.
        for name in ("signal_id", "event_date", "bucket", "signal_type",
                     "value", "detector_ver"):
            assert nullable[name] is False, name

    def test_the_buckets_come_from_the_one_definition_not_a_copy(self):
        # The exact SQL expression the Signal Explorer runs must appear in
        # the dataset - importing it, not restating it.
        assert horizon_defs.bucket_sql() in HORIZON_SIGNALS_V1.sql
        assert horizon_defs.REF_SQL in HORIZON_SIGNALS_V1.sql

    def test_the_horizon_shows_temporal_signals_only(self):
        assert temporal_intel.DETECTOR_NAME in HORIZON_SIGNALS_V1.sql
        from services.detection.signal_query import HORIZON_SIGNAL_TYPES
        for signal_type in HORIZON_SIGNAL_TYPES:
            assert f"'{signal_type}'" in HORIZON_SIGNALS_V1.sql

    def test_undated_and_past_are_out_of_the_horizon(self):
        assert "s.date_from IS NOT NULL" in HORIZON_SIGNALS_V1.sql
        assert "<> 'past'" in HORIZON_SIGNALS_V1.sql

    def test_the_reference_date_is_a_bound_parameter(self):
        assert HORIZON_SIGNALS_V1.sql_params == ("@criteria", "as_of", "@limit")
        assert HORIZON_SIGNALS_V1.sql.count("%s::date") == 1

    def test_normalize_requires_the_reference_date(self):
        with pytest.raises(Exception):
            HORIZON_V1.normalize_parameters({"criteria": {"text": "x"}})
