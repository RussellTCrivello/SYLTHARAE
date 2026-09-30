"""Step 20: the schedule model — what may be scheduled, and what it carries.

Pure validation (no database): the schedule types map onto existing
JobManager jobs, the payload is validated against the report registry at
creation and edit time (never silently stored invalid), and the owner
eligibility rule is the authorization the scheduler re-decides at every
fire.
"""

import pytest

from services.scheduling.model import (
    MAX_INTERVAL_MINUTES, SCHEDULE_TYPES,
    ScheduleError, describe, owner_eligible, required_role, validate_interval,
    validate_payload,
)


class TestTheVocabulary:
    def test_three_job_types_map_onto_existing_jobs(self):
        assert set(SCHEDULE_TYPES) == {"report_run", "rule_evaluation",
                                       "scenario_evaluation"}

    def test_reports_run_as_their_owner_evaluations_are_administrative(self):
        assert required_role("report_run") == "analyst"
        assert required_role("rule_evaluation") == "admin"
        assert required_role("scenario_evaluation") == "admin"
        with pytest.raises(ScheduleError):
            required_role("nonsense")

    def test_the_interval_is_bounded(self):
        assert validate_interval(5) == 5
        assert validate_interval(1440) == 1440
        assert validate_interval(MAX_INTERVAL_MINUTES) == MAX_INTERVAL_MINUTES
        for bad in (4, MAX_INTERVAL_MINUTES + 1, 0, -5, 10.5, True, "60", None):
            with pytest.raises(ScheduleError):
                validate_interval(bad)


class TestThePayload:
    def test_a_report_payload_is_validated_against_the_registry(self):
        stored = validate_payload("report_run", {
            "report_id": "search_results", "version": 1,
            "parameters": {"criteria": {"text": "x"}}})
        assert stored["report_id"] == "search_results" and stored["version"] == 1
        assert stored["parameters"] == {"criteria": {"text": "x"}}

    def test_an_unknown_report_is_refused_not_stored(self):
        with pytest.raises(ScheduleError, match="no registered report"):
            validate_payload("report_run", {"report_id": "no_such_report"})

    def test_invalid_parameters_are_refused_with_the_registry_reason(self):
        with pytest.raises(ScheduleError, match="payload.parameters"):
            validate_payload("report_run", {"report_id": "search_results",
                                            "parameters": {"nonsense": 1}})
        with pytest.raises(ScheduleError, match="payload.parameters"):
            validate_payload("report_run", {"report_id": "search_results",
                                            "parameters": {}}), \
            "a missing required parameter is refused, never run unfiltered"

    def test_payload_shapes_are_strict(self):
        for bad in ({"parameters": {}},                        # no report_id
                    {"report_id": "search_results", "junk": 1},
                    {"report_id": 5},
                    {"report_id": "search_results", "version": "1"},
                    {"report_id": "search_results", "parameters": []}):
            with pytest.raises(ScheduleError):
                validate_payload("report_run", bad)

    def test_evaluation_payloads_carry_nothing(self):
        assert validate_payload("rule_evaluation", {}) == {}
        assert validate_payload("scenario_evaluation", None) == {}
        with pytest.raises(ScheduleError, match="unknown payload field"):
            validate_payload("rule_evaluation", {"report_id": "x"})

    def test_the_type_is_validated_first(self):
        with pytest.raises(ScheduleError, match="schedule_type must be one of"):
            validate_payload("nonsense", {})


class TestOwnerEligibility:
    def test_the_role_is_read_at_fire_time_not_stored(self):
        assert owner_eligible("report_run", "analyst", True) == (True, None)
        assert owner_eligible("report_run", "admin", True) == (True, None)
        assert owner_eligible("report_run", "viewer", True) == (False, "owner_role")
        assert owner_eligible("report_run", "analyst", False) == \
            (False, "owner_inactive"), "a deactivated owner never fires"
        assert owner_eligible("rule_evaluation", "analyst", True) == \
            (False, "owner_role"), "evaluate-all is administrative"
        assert owner_eligible("rule_evaluation", "admin", True) == (True, None)
        assert owner_eligible("scenario_evaluation", None, True) == \
            (False, "owner_role")


class TestDescription:
    def test_the_page_can_say_what_the_schedule_runs(self):
        assert "search_results" in describe("report_run", {
            "report_id": "search_results"})
        assert "@2" in describe("report_run", {
            "report_id": "search_results", "version": 2})
        assert "rule" in describe("rule_evaluation", {})
        assert "scenario" in describe("scenario_evaluation", {})
