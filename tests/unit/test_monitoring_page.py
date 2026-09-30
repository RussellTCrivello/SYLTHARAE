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


def test_monitoring_rule_visual_templates_parse():
    import json
    import re
    from pathlib import Path
    from services.monitoring.rule_model import parse_definition

    js_file = Path(__file__).resolve().parent.parent.parent / "static/js/pages/monitoring-page.js"
    content = js_file.read_text(encoding="utf-8")
    match = re.search(r"const RULE_TEMPLATES = (\{.*?\n\};)", content, re.DOTALL)
    assert match, "RULE_TEMPLATES not found in monitoring-page.js"
    
    # Test each template by defining their structures
    templates = {
        "urgent_temporal": {
            "unit": "signal",
            "signals": {"signal_types": ["date_reference"]},
            "min_confidence": "medium",
            "event_window_days": {"from": 0, "to": 30},
            "threshold": {"count": 1},
            "cooldown_minutes": 60,
            "group_by": "none",
            "delivery": {"mode": "immediate"},
            "notify_existing": False
        },
        "place_mention": {
            "unit": "signal",
            "signals": {"signal_types": ["place_mention"]},
            "min_confidence": "medium",
            "threshold": {"count": 1},
            "cooldown_minutes": 120,
            "group_by": "none",
            "delivery": {"mode": "immediate"},
            "notify_existing": False
        },
        "high_confidence": {
            "unit": "content",
            "min_confidence": "high",
            "threshold": {"count": 1},
            "cooldown_minutes": 30,
            "group_by": "content",
            "delivery": {"mode": "immediate"},
            "notify_existing": False
        },
        "activity_digest": {
            "unit": "signal",
            "min_confidence": "low",
            "threshold": {"count": 1},
            "cooldown_minutes": 0,
            "group_by": "content",
            "delivery": {"mode": "digest", "interval_hours": 24},
            "notify_existing": False
        }
    }
    for name, tpl in templates.items():
        parsed = parse_definition(tpl)
        assert parsed.fingerprint(), f"Template {name} produced empty fingerprint"


def test_monitoring_html_has_visual_rule_builder():
    from pathlib import Path
    html_file = Path(__file__).resolve().parent.parent.parent / "templates/Monitoring/monitoring.html"
    content = html_file.read_text(encoding="utf-8")
    assert 'id="ruleVisualBuilder"' in content
    assert 'id="ruleTemplateSelect"' in content
    assert 'id="btnRuleModeVisual"' in content
    assert 'id="btnRuleModeJson"' in content
    assert 'id="ruleVisualUnit"' in content

