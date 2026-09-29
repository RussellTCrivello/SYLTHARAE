"""Step 12: what the /monitoring editors start from is valid as it stands."""

from __future__ import annotations


def test_the_starter_scenario_parses_and_is_warning_free():
    from Api.routes.scenarios import STARTER_SCENARIO
    from services.monitoring.scenario_model import parse_definition

    parsed = parse_definition(STARTER_SCENARIO)
    assert parsed.canonical()["default_outcome"] == "none"
    assert parsed.notify_outcomes() == ["review"]
    assert parsed.warnings() == []


def test_the_starter_rule_parses():
    from Api.routes.scenarios import STARTER_RULE
    from services.monitoring.rule_model import parse_definition

    parsed = parse_definition(STARTER_RULE)
    assert parsed.fingerprint()
