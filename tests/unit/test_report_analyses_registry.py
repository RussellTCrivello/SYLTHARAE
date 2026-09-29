"""Step 16: analyses in the report registry - declaration, validation,
fingerprints, lock - and the criteria-dataset reference scope."""

import dataclasses

import pytest

from core.analytics.kinds import Analysis
from core.criteria.compiler import AccessScope
from core.reporting import REGISTRY, Parameter, ReportDefinitionError, ReportRegistry
from core.reporting.registry import read_lock

KEYNESS = REGISTRY.report("term_keyness")
ANALYSIS = REGISTRY.analysis("term_keyness@1")
RANKED = REGISTRY.dataset("term_keyness.ranked@1")
TOTALS = REGISTRY.dataset("term_keyness.totals@1")
TOPIC = REGISTRY.help_topic(KEYNESS.help_topic)


def _registry(report=KEYNESS, datasets=(RANKED, TOTALS), analyses=(ANALYSIS,)):
    return ReportRegistry(reports=(report,), datasets=tuple(datasets),
                          help_topics=(TOPIC,), analyses=tuple(analyses))


def test_the_shipped_keyness_wiring_is_valid():
    assert _registry().validate() == []
    assert KEYNESS.analyses == ("term_keyness@1",)


@pytest.mark.parametrize("build, expected", [
    (lambda: _registry(analyses=()), "analysis 'term_keyness@1' is not registered"),
    (lambda: _registry(report=dataclasses.replace(KEYNESS, analyses=())),
     "analysis term_keyness@1 is used by no report"),
    (lambda: _registry(analyses=(ANALYSIS, ANALYSIS)), "duplicate analysis term_keyness@1"),
    (lambda: _registry(report=dataclasses.replace(
        KEYNESS, parameters=(KEYNESS.parameters[0],))),
     "needs parameter 'direction'"),
    (lambda: _registry(analyses=(dataclasses.replace(
        ANALYSIS, inputs={"ranked": RANKED.key, "totals": RANKED.key}),)),
     "input totals must be exact"),
    (lambda: _registry(analyses=(dataclasses.replace(
        ANALYSIS, inputs={"ranked": RANKED.key, "totals": "search_results.count@1"}),)),
     "reads search_results.count@1 (totals), which the report does not read"),
])
def test_analysis_wiring_problems_are_reported(build, expected):
    problems = build().validate()
    assert any(expected in p for p in problems), problems


def test_missing_input_columns_are_reported():
    thin = dataclasses.replace(TOTALS, columns=TOTALS.columns[:1])
    # The dataset itself is still constructible; the registry catches the gap.
    problems = _registry(datasets=(RANKED, thin)).validate()
    assert any("lacks ['reference_tokens'" in p for p in problems), problems


def test_report_analysis_references_are_checked_at_construction():
    with pytest.raises(ReportDefinitionError, match="must be distinct"):
        dataclasses.replace(KEYNESS, analyses=("term_keyness@1", "term_keyness@1"))
    with pytest.raises(ReportDefinitionError, match="'id@version'"):
        dataclasses.replace(KEYNESS, analyses=("term keyness",))


def test_reports_without_analyses_keep_their_released_fingerprint():
    search = REGISTRY.report("search_results")
    assert "analyses" not in search.semantic(REGISTRY.dataset_fingerprints())
    assert REGISTRY.report_fingerprint(search) == read_lock()["reports"]["search_results@1"]


def test_the_report_fingerprint_covers_its_analyses():
    fp = REGISTRY.report_fingerprint(KEYNESS)
    assert fp == read_lock()["reports"]["term_keyness@1"]
    changed = REGISTRY.analysis_fingerprints()
    changed["term_keyness@1"] = "f" * 64
    assert KEYNESS.fingerprint(REGISTRY.dataset_fingerprints(), changed) != fp


def test_editing_a_released_analysis_fails_the_lock(monkeypatch):
    lock = read_lock()
    edited = dataclasses.replace(ANALYSIS, inputs={"ranked": RANKED.key, "totals": RANKED.key})
    reg = dataclasses.replace(REGISTRY, analyses=(edited,))
    problems = reg.validate_lock(lock)
    assert any("analysis term_keyness@1 changed meaning" in p for p in problems), problems
    assert any("report term_keyness@1 changed meaning" in p for p in problems), problems


def test_every_template_msgid_is_in_the_translation_gate():
    keys = set(REGISTRY.translation_keys())
    assert set(ANALYSIS.kind_obj.templates.msgids()) <= keys
    assert ANALYSIS.title in keys
    assert set(KEYNESS.parameter("direction").choice_labels) <= keys


def test_choice_labels_are_one_per_choice_and_not_part_of_the_meaning():
    with pytest.raises(ReportDefinitionError, match="one choice label per choice"):
        Parameter("d", "enum", "Direction", choices=("a", "b"), choice_labels=("A",))
    p = KEYNESS.parameter("direction")
    assert "choice_labels" not in p.semantic()


# ------------------------------------------------------------ reference scope

def test_criteria_dataset_may_declare_a_scope_for_rows_outside_the_criteria():
    assert RANKED.criteria_param == "criteria" and RANKED.scope_column == "rpt_hc.source_id"
    values = KEYNESS.normalize_parameters({"criteria": {"text": "x"}})
    open_ = RANKED.bind(values, AccessScope.unrestricted())
    assert "WHERE TRUE" in open_.sql
    none = RANKED.bind(values, AccessScope(allowed_source_ids=()))
    assert "WHERE FALSE" in none.sql
    some = RANKED.bind(values, AccessScope(allowed_source_ids=(4, 9)))
    assert "rpt_hc.source_id = ANY(%s)" in some.sql
    # The compiler scopes the criteria rows too (hc.source_id), and the
    # parameters follow the %s order: criteria, reference scope, direction, limit.
    assert "hc.source_id IN (%s,%s)" in some.sql
    assert list(some.params[-3:]) == [[4, 9], "over", RANKED.row_limit + 1]


def test_a_scope_slot_without_scope_column_is_still_refused():
    crit = REGISTRY.dataset("search_results.matches@1")
    with pytest.raises(ReportDefinitionError, match="already applies the access scope"):
        dataclasses.replace(crit, sql=crit.sql.replace("WHERE {where}", "WHERE {where} AND {scope}"),
                            sql_params=("@criteria", "@scope", "@limit"))


def test_a_reason_cannot_be_combined_with_criteria():
    with pytest.raises(ReportDefinitionError, match="exactly one of"):
        dataclasses.replace(RANKED, unscoped_reason="no scope needed")
