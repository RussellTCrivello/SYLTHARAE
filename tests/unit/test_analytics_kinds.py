"""Narrative templates and the keyness analysis (core.analytics)."""

import copy
import math

import pytest

from core.analytics import measures, thresholds
from core.analytics.kinds import (KEYNESS_TEMPLATES, KINDS, Analysis, AnalysisError,
                                  MEASURED, NOT_MEASURABLE)
from core.analytics.narrative import VOICES, NarrativeError, TemplateSet, render
from core.reporting import REGISTRY


# ------------------------------------------------------------------ helpers

def _ds(key, semantics, columns, rows, truncated=False):
    return {"dataset_key": key, "semantics": semantics, "truncated": truncated,
            "columns": [{"name": c} for c in columns], "rows": rows}


def _row(term, a, b, c, d):
    return {"term": term, "target_freq": a, "reference_freq": b,
            "g2": measures.log_likelihood(a, b, c, d), "log_ratio": measures.log_ratio(a, b, c, d)}


def _inputs(rows, totals, truncated=False):
    ranked = _ds("term_keyness.ranked@1", "top_n",
                 ["term", "target_freq", "reference_freq", "g2", "log_ratio"], rows, truncated)
    tot = _ds("term_keyness.totals@1", "exact",
              ["target_tokens", "reference_tokens", "target_contents", "reference_contents",
               "unknown_count_rows", "significant_terms"], [totals])
    return {"term_keyness.ranked@1": ranked, "term_keyness.totals@1": tot}


def _totals(c=10_000, d=20_000, tc=3, rc=7, unknown=0, significant=1):
    return {"target_tokens": c, "reference_tokens": d, "target_contents": tc,
            "reference_contents": rc, "unknown_count_rows": unknown,
            "significant_terms": significant}


ANALYSIS = REGISTRY.analysis("term_keyness@1")


# ------------------------------------------------------------------ templates

def test_template_set_needs_every_voice_and_valid_keys():
    with pytest.raises(NarrativeError, match="voice"):
        TemplateSet("x", 1, {"measure.a": "M", "finding.a": "F"})
    with pytest.raises(NarrativeError, match="<voice>.<variant>"):
        TemplateSet("x", 1, {**{f"{v}.a": v for v in VOICES}, "opinion.a": "no"})
    with pytest.raises(NarrativeError, match="stray"):
        TemplateSet("x", 1, {f"{v}.a": f"{v} 50%" for v in VOICES})
    with pytest.raises(NarrativeError, match="version"):
        TemplateSet("x", 0, {f"{v}.a": v for v in VOICES})


def test_compose_requires_one_sentence_per_voice_in_order_and_exact_params():
    t = TemplateSet("x", 1, {**{f"{v}.a": v for v in VOICES}, "finding.b": "n=%(n)s"})
    ok = [(f"{v}.a", {}) for v in VOICES]
    assert [v["voice"] for v in t.compose(ok)["voices"]] == list(VOICES)
    with pytest.raises(NarrativeError, match="in order"):
        t.compose(list(reversed(ok)))
    bad = list(ok)
    bad[1] = ("finding.b", {})
    with pytest.raises(NarrativeError, match="takes"):
        t.compose(bad)
    bad[1] = ("finding.b", {"n": 1, "extra": 2})
    with pytest.raises(NarrativeError, match="takes"):
        t.compose(bad)
    bad[1] = ("finding.b", {"n": [1]})
    with pytest.raises(NarrativeError, match="text or a number"):
        t.compose(bad)


def test_template_fingerprint_follows_text_and_version():
    base = {f"{v}.a": v for v in VOICES}
    a = TemplateSet("x", 1, base)
    assert a.fingerprint() == TemplateSet("x", 1, dict(reversed(list(base.items())))).fingerprint()
    assert a.fingerprint() != TemplateSet("x", 2, base).fingerprint()
    assert a.fingerprint() != TemplateSet("x", 1, dict(base, **{"caveat.a": "other"})).fingerprint()


def test_render_translates_then_fills_and_formats_numbers():
    t = TemplateSet("x", 1, {**{f"{v}.a": v for v in VOICES}, "finding.b": "n=%(n)s g=%(g)s"})
    stored = t.compose([("measure.a", {}), ("finding.b", {"n": 12345, "g": 1.5}),
                        ("confidence.a", {}), ("consequence.a", {}), ("caveat.a", {})])
    text = render(stored, lambda s: "N=%(n)s G=%(g)s" if s.startswith("n=") else s)
    assert text[1] == {"voice": "finding", "text": "N=12,345 G=1.50"}
    assert [x["voice"] for x in text] == list(VOICES)


# ------------------------------------------------------------------ keyness

def test_keyness_measured_oracle_and_narrative():
    c, d = 10_000, 20_000
    rows = [_row("alpha", 100, 50, c, d), _row("beta", 10, 10, c, d)]
    result = ANALYSIS.compute(_inputs(rows, _totals(significant=1)), {"direction": "over"})
    assert result["state"] == MEASURED and result["reason"] is None
    top = result["rows"][0]
    assert top["g2"] == pytest.approx(100 * math.log(2)) and top["log_ratio"] == pytest.approx(2.0)
    assert top["level"] == "ll_p0001"
    assert top["target_per_million"] == pytest.approx(10_000.0)
    assert result["rows"][1]["level"] in ("ll_p05", "ll_p01", "ll_p001", "none")
    narrative = result["narrative"]
    assert narrative["template_set"] == "keyness" and narrative["template_version"] == 1
    keys = [v["key"] for v in narrative["voices"]]
    assert keys == ["measure.over", "finding.significant_over", "confidence.threshold",
                    "consequence.significant", "caveat.default"]
    finding = narrative["voices"][1]["params"]
    assert finding["top_term"] == "alpha" and finding["significant"] == 1
    assert finding["critical"] == thresholds.LL_P0001.value
    assert narrative["voices"][2]["params"]["source"] == thresholds.LL_P0001.source
    text = render(narrative, lambda s: s)
    assert "\"alpha\"" in text[1]["text"] and "69.31" in text[1]["text"]


def test_keyness_is_deterministic():
    rows = [_row("alpha", 100, 50, 10_000, 20_000)]
    one = ANALYSIS.compute(_inputs(rows, _totals()), {"direction": "over"})
    two = ANALYSIS.compute(_inputs(copy.deepcopy(rows), _totals()), {"direction": "over"})
    assert one == two


def test_keyness_without_significant_terms_says_so():
    rows = [_row("beta", 10, 10, 10_000, 20_000)]
    result = ANALYSIS.compute(_inputs(rows, _totals(significant=0)), {"direction": "over"})
    keys = [v["key"] for v in result["narrative"]["voices"]]
    assert keys[1] == "finding.none" and keys[3] == "consequence.none"


def test_keyness_unknown_counts_are_reported_in_the_caveat():
    rows = [_row("alpha", 100, 50, 10_000, 20_000)]
    result = ANALYSIS.compute(_inputs(rows, _totals(unknown=4)), {"direction": "over"})
    caveat = result["narrative"]["voices"][4]
    assert caveat["key"] == "caveat.unknown_counts" and caveat["params"] == {"unknown_rows": 4}
    assert result["measures"]["unknown_count_rows"] == 4


def test_keyness_under_direction():
    c, d = 10_000, 20_000
    rows = [_row("gamma", 5, 200, c, d)]
    result = ANALYSIS.compute(_inputs(rows, _totals(significant=1)), {"direction": "under"})
    assert result["rows"][0]["log_ratio"] < 0
    assert result["narrative"]["voices"][0]["key"] == "measure.under"
    assert result["narrative"]["voices"][1]["key"] == "finding.significant_under"


@pytest.mark.parametrize("totals, reason", [
    (_totals(tc=0, c=0), "no_target"),
    (_totals(c=0), "no_target_tokens"),
    (_totals(rc=0, d=0), "no_reference"),
    (_totals(d=0), "no_reference"),
])
def test_keyness_not_measurable_is_not_zero(totals, reason):
    result = ANALYSIS.compute(_inputs([], totals), {"direction": "over"})
    assert result["state"] == NOT_MEASURABLE and result["reason"] == reason
    assert result["rows"] == []
    assert "significant_terms" not in result["measures"], "no count is reported when nothing was measured"
    assert result["narrative"]["voices"][1]["key"] == f"finding.{reason}"


def test_keyness_fails_when_database_and_reference_disagree():
    row = _row("alpha", 100, 50, 10_000, 20_000)
    row["g2"] += 0.001
    with pytest.raises(AnalysisError, match="disagree"):
        ANALYSIS.compute(_inputs([row], _totals()), {"direction": "over"})


def test_keyness_fails_on_a_term_in_the_wrong_direction():
    row = _row("gamma", 5, 200, 10_000, 20_000)
    with pytest.raises(AnalysisError, match="direction"):
        ANALYSIS.compute(_inputs([row], _totals()), {"direction": "over"})


def test_keyness_fails_when_more_significant_rows_are_listed_than_counted():
    rows = [_row("alpha", 100, 50, 10_000, 20_000)]
    with pytest.raises(AnalysisError, match="more significant"):
        ANALYSIS.compute(_inputs(rows, _totals(significant=0)), {"direction": "over"})


def test_input_contract_is_checked():
    inputs = _inputs([], _totals())
    inputs["term_keyness.totals@1"]["semantics"] = "capped"
    with pytest.raises(AnalysisError, match="must be"):
        ANALYSIS.compute(inputs, {"direction": "over"})
    inputs = _inputs([], _totals())
    inputs["term_keyness.ranked@1"]["columns"] = [{"name": "term"}]
    with pytest.raises(AnalysisError, match="lacks"):
        ANALYSIS.compute(inputs, {"direction": "over"})
    with pytest.raises(AnalysisError, match="was not read"):
        ANALYSIS.compute({}, {"direction": "over"})


# ------------------------------------------------------------------ declarations

def test_analysis_declaration_is_validated():
    with pytest.raises(ValueError, match="unknown kind"):
        Analysis("a", 1, "sentiment", {}, "d", "T")
    with pytest.raises(ValueError, match="exactly the roles"):
        Analysis("a", 1, "keyness", {"ranked": "x@1"}, "d", "T")


def test_analysis_fingerprint_covers_kind_thresholds_templates_and_inputs():
    fps = REGISTRY.dataset_fingerprints()
    base = ANALYSIS.fingerprint(fps)
    assert base == REGISTRY.analysis_fingerprints()["term_keyness@1"]
    changed = dict(fps, **{"term_keyness.totals@1": "0" * 64})
    assert ANALYSIS.fingerprint(changed) != base
    semantic = ANALYSIS.semantic(fps)
    assert semantic["kind"]["thresholds"]["ll_p0001"] == 15.13
    assert semantic["kind"]["templates"] == KEYNESS_TEMPLATES.fingerprint()
    assert set(KINDS) == {"keyness@1", "keyness@2"}
