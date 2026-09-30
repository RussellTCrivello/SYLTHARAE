"""The Comprehensive report's own analyses: composition and coverage
(core.analytics.kinds). Oracle-tested like keyness: measures recomputed by
hand, narrative voices in order, hostile text kept as parameters, unknown
never read as zero, and a listing that contradicts the overview fails."""


import pytest

from core.analytics.kinds import (AnalysisError, KINDS, MEASURED,
                                  NOT_MEASURABLE, _coverage, _composition,
                                  _narrate_composition, _narrate_coverage)
from core.analytics.narrative import render
from core.reporting import REGISTRY


# ------------------------------------------------------------------ helpers

def _ds(key, semantics, columns, rows, truncated=False):
    return {"dataset_key": key, "semantics": semantics, "truncated": truncated,
            "columns": [{"name": c} for c in columns], "rows": rows}


OVERVIEW_COLUMNS = ["contents", "paths", "sources", "keywords", "categories",
                    "first_ingest", "last_ingest", "first_event", "last_event",
                    "unknown_count_rows"]


def _overview(contents=4, paths=6, sources=2, keywords=3, categories=2,
              first="2026-01-01", last="2026-03-01",
              first_event="2026-02-01", last_event="2026-02-20",
              unknown=0):
    return dict(contents=contents, paths=paths, sources=sources,
                keywords=keywords, categories=categories,
                first_ingest=first, last_ingest=last,
                first_event=first_event, last_event=last_event,
                unknown_count_rows=unknown)


def _kw_row(label, contents, occurrences, corpus=4):
    return {"keyword_id": 1, "label": label, "category_name": "Cat",
            "contents": contents, "occurrences": occurrences,
            "unknown_count_rows": 0, "corpus_contents": corpus}


def _cat_row(name, contents, corpus=4):
    return {"category_id": 1, "category_name": name, "member_words": 5,
            "keywords": 2, "contents": contents, "occurrences": contents * 2,
            "unknown_count_rows": 0, "corpus_contents": corpus,
            "content_share": contents / corpus if corpus else None}


COMPOSITION = REGISTRY.analysis("composition@1")
COVERAGE_ANALYSIS = REGISTRY.analysis("coverage@1")


def _roles(by_dataset_key):
    """Role-keyed inputs for calling the kind functions directly."""
    return {"overview": by_dataset_key["comprehensive.overview@1"],
            "keywords": by_dataset_key["keyword_intelligence.matches@1"],
            "categories": by_dataset_key["category_analysis.summary@1"]}


def _composition_inputs(overview=None):
    return {"comprehensive.overview@1": _ds(
        "comprehensive.overview@1", "exact", OVERVIEW_COLUMNS,
        [overview or _overview()])}


def _coverage_inputs(overview=None, keywords=None, categories=None):
    overview = overview or _overview()
    keywords = _ds("keyword_intelligence.matches@1", "capped",
                   ["keyword_id", "label", "category_name", "contents",
                    "occurrences", "unknown_count_rows", "corpus_contents"],
                   keywords if keywords is not None else
                   [_kw_row("alpha", 3, 9)])
    categories = _ds("category_analysis.summary@1", "capped",
                     ["category_id", "category_name", "member_words",
                      "keywords", "contents", "occurrences",
                      "unknown_count_rows", "corpus_contents", "content_share"],
                     categories if categories is not None else
                     [_cat_row("People", 2)])
    return {"comprehensive.overview@1": _ds(
                "comprehensive.overview@1", "exact", OVERVIEW_COLUMNS, [overview]),
            "keyword_intelligence.matches@1": keywords,
            "category_analysis.summary@1": categories}


# ------------------------------------------------------------------ composition

def test_composition_measured_oracle_and_voice_order():
    result = COMPOSITION.compute(_composition_inputs(), {})
    assert result["state"] == MEASURED and result["reason"] is None
    assert result["measures"] == {"contents": 4, "paths": 6, "sources": 2,
                                  "keywords": 3, "categories": 2,
                                  "unknown_count_rows": 0}
    voices = result["narrative"]["voices"]
    assert [v["voice"] for v in voices] == ["measure", "finding", "confidence",
                                            "consequence", "caveat"]
    # record format 2: the measure voice carries several whole sentences.
    measure_keys = [s["key"] for s in voices[0]["sentences"]]
    assert measure_keys == ["measure.method", "measure.sizes", "measure.dates"]
    assert voices[0]["sentences"][1]["params"] == {"contents": 4, "paths": 6,
                                                   "sources": 2}
    finding_keys = [s["key"] for s in voices[1]["sentences"]]
    assert finding_keys == ["finding.keywords", "finding.categories"]
    assert voices[1]["sentences"][0]["msgid_plural"] is not None
    text = render(result["narrative"], lambda s: s, ngettext=lambda s, p, n: s)
    assert text[0]["text"].startswith("The overview counts what")
    assert "File dates: 2026-01-01 to 2026-03-01" in text[0]["text"]


def test_composition_dates_are_unknown_not_zero_when_no_events():
    result = _composition({"overview": _ds(
        "comprehensive.overview@1", "exact", OVERVIEW_COLUMNS,
        [_overview(first_event=None, last_event=None)])}, {},
        lambda **kw: _narrate_composition(**kw))
    # the measure voice names the no-events sentence, not a zero date
    keys = [s["key"] for s in result["narrative"]["voices"][0]["sentences"]]
    assert "measure.dates_no_events" in keys


def test_composition_empty_set_is_not_measurable_not_zero():
    result = COMPOSITION.compute(
        _composition_inputs(_overview(contents=0, paths=0, sources=0,
                                      keywords=0, categories=0)), {})
    assert result["state"] == NOT_MEASURABLE and result["reason"] == "no_contents"
    keys = [s["key"] for v in result["narrative"]["voices"] for s in v["sentences"]]
    assert "finding.no_contents" in keys
    assert "confidence.not_measurable" in keys


def test_composition_unknown_counts_reported_as_plural_caveat():
    result = COMPOSITION.compute(_composition_inputs(_overview(unknown=2)), {})
    caveat = result["narrative"]["voices"][4]["sentences"]
    assert [s["key"] for s in caveat] == ["caveat.overlap", "caveat.unknown_counts"]
    assert caveat[1]["params"] == {"unknown_count_rows": 2}


def test_composition_is_deterministic():
    one = COMPOSITION.compute(_composition_inputs(), {})
    two = COMPOSITION.compute(_composition_inputs(), {})
    assert one == two


def test_composition_hostile_dates_stay_text():
    overview = _overview(first="2026-01-01'); DROP TABLE paths;--",
                         last="<script>x</script>")
    result = COMPOSITION.compute(_composition_inputs(overview), {})
    params = result["narrative"]["voices"][0]["sentences"][2]["params"]
    assert params["first_ingest"] == "2026-01-01'); DROP TABLE paths;--"
    assert params["last_ingest"] == "<script>x</script>"


# ------------------------------------------------------------------ coverage

def test_coverage_measured_oracle_and_cross_check():
    result = COVERAGE_ANALYSIS.compute(_coverage_inputs(), {})
    assert result["state"] == MEASURED and result["reason"] is None
    m = result["measures"]
    assert m["contents"] == 4 and m["keyword_rows"] == 1 and m["category_rows"] == 1
    voices = result["narrative"]["voices"]
    finding_keys = [s["key"] for s in voices[1]["sentences"]]
    assert finding_keys == ["finding.top_keyword", "finding.top_category"]
    top_kw = voices[1]["sentences"][0]["params"]
    assert top_kw["top_keyword"] == "alpha"
    assert top_kw["keyword_contents"] == 3
    assert top_kw["keyword_share"] == 75.0      # 3 of 4, in percent
    assert top_kw["keyword_occurrences"] == 9
    top_cat = voices[1]["sentences"][1]["params"]
    assert top_cat["category_share"] == 50.0    # 2 of 4


def test_coverage_fails_when_a_listing_disagrees_with_the_overview():
    keywords = [_kw_row("alpha", 3, 9, corpus=5)]   # overview says 4
    with pytest.raises(AnalysisError, match="disagrees with the overview"):
        _coverage(_roles(_coverage_inputs(keywords=keywords)), {},
                  lambda **kw: _narrate_coverage(**kw))
    categories = [_cat_row("People", 2, corpus=7)]
    with pytest.raises(AnalysisError, match="disagrees with the overview"):
        _coverage(_roles(_coverage_inputs(categories=categories)), {},
                  lambda **kw: _narrate_coverage(**kw))


def test_coverage_zero_presence_is_measured_not_unknown():
    # The collection has no keywords and no categories at all: both listings
    # are empty AND the overview counts zero presence - consistent, measured.
    result = COVERAGE_ANALYSIS.compute(
        _coverage_inputs(_overview(keywords=0, categories=0),
                         keywords=[], categories=[]), {})
    assert result["state"] == MEASURED
    assert [s["key"] for s in result["narrative"]["voices"][1]["sentences"]] == ["finding.none"]


def test_coverage_both_listings_empty_contradicts_presence():
    with pytest.raises(AnalysisError, match="contradict the overview"):
        _coverage(_roles(_coverage_inputs(keywords=[], categories=[])), {},
                  lambda **kw: _narrate_coverage(**kw))


def test_coverage_top_row_may_lag_the_exact_count():
    # The exact overview counts 3 keywords; the capped listing shows the
    # widest one only. The finding names the shown top, the measure voice
    # states the exact presence alongside the listing size.
    result = COVERAGE_ANALYSIS.compute(
        _coverage_inputs(_overview(keywords=3),
                         keywords=[_kw_row("alpha", 3, 9)],
                         categories=[_cat_row("People", 2)]), {})
    m = result["measures"]
    assert m["keywords"] == 3 and m["keyword_rows"] == 1
    assert m["keywords_truncated"] is False and m["categories_truncated"] is False


def test_coverage_truncation_is_stated_in_measures():
    result = COVERAGE_ANALYSIS.compute(
        _coverage_inputs(keywords=[_kw_row("alpha", 3, 9)],
                         categories=[_cat_row("People", 2)]), {})
    listed = [s for s in result["narrative"]["voices"][0]["sentences"]
              if s["key"].startswith("measure.listing")]
    assert [s["key"] for s in listed] == ["measure.listing_keywords",
                                          "measure.listing_categories"]
    assert listed[0]["params"] == {"keyword_rows": 1}


def test_coverage_is_deterministic():
    one = COVERAGE_ANALYSIS.compute(_coverage_inputs(), {})
    two = COVERAGE_ANALYSIS.compute(_coverage_inputs(), {})
    assert one == two


# ------------------------------------------------------------------ guards

def test_kinds_are_registered():
    assert "composition@1" in KINDS and "coverage@1" in KINDS


def test_input_contract_is_enforced():
    with pytest.raises(Exception, match="must be"):
        COVERAGE_ANALYSIS.compute(  # dataset keys are mapped to roles by compute
            {"comprehensive.overview@1": _ds("comprehensive.overview@1", "capped",
                                             OVERVIEW_COLUMNS, [_overview()]),
             "keyword_intelligence.matches@1": _ds("keyword_intelligence.matches@1",
                                                   "capped", ["label"], []),
             "category_analysis.summary@1": _ds("category_analysis.summary@1",
                                                "capped", ["category_name"], [])},
            {})
    with pytest.raises(Exception, match="lacks columns"):
        COVERAGE_ANALYSIS.compute(
            {"comprehensive.overview@1": _ds("comprehensive.overview@1", "exact",
                                             OVERVIEW_COLUMNS, [_overview()]),
             "keyword_intelligence.matches@1": _ds(
                 "keyword_intelligence.matches@1", "capped",
                 ["label", "contents", "corpus_contents"], []),
             "category_analysis.summary@1": _ds(
                 "category_analysis.summary@1", "capped",
                 ["category_name", "contents", "corpus_contents"], [])},
            {})


def test_comprehensive_report_declares_three_sections():
    report = REGISTRY.report("comprehensive")
    assert report.analyses == ("composition@1", "term_keyness@2", "coverage@1")
    assert set(report.datasets) == {
        "comprehensive.overview@1", "term_keyness.ranked@1",
        "term_keyness.totals@1", "keyword_intelligence.matches@1",
        "category_analysis.summary@1"}
    assert REGISTRY.dataset("comprehensive.overview@1").row_limit == 1

