"""Request parsing for the Horizon / Signal Explorer API.

services/detection/signal_query.py turns query-string arguments into a
SignalFilter and document Criteria. Anything it cannot answer as written must
be a SignalQueryError (HTTP 400) - never ignored, never guessed, and never an
unrelated exception that surfaces as a 500. Equal requests must parse to equal
(fingerprint-identical) filters regardless of argument order or repetition.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from werkzeug.datastructures import MultiDict  # noqa: E402

from core.criteria.model import Criteria  # noqa: E402
from services.detection import signal_query as sq  # noqa: E402
from services.detection.signal_query import SignalQueryError  # noqa: E402


def parse(**kwargs):
    return sq.parse_filter(MultiDict(_pairs(kwargs)))


def criteria(load=lambda _id: None, **kwargs):
    return sq.parse_document_criteria(MultiDict(_pairs(kwargs)), load_saved_search=load)


def _pairs(kwargs):
    out = []
    for k, v in kwargs.items():
        for item in (v if isinstance(v, list) else [v]):
            out.append((k, item))
    return out


# --- signal filters ------------------------------------------------------------

def test_empty_request_is_the_unfiltered_filter():
    assert parse() == sq.SignalFilter()


def test_filters_are_canonical_regardless_of_order_and_repetition():
    a = parse(language=["he", "ar", "he"], confidence=["low", "high"])
    b = parse(confidence=["high", "low", "low"], language=["ar", "he"])
    assert a == b
    assert a.languages == ("ar", "he") and a.confidence == ("high", "low")
    assert sq._fingerprint("x", a, Criteria()) == sq._fingerprint("x", b, Criteria())


@pytest.mark.parametrize("name,value", [
    ("language", "de"), ("confidence", "certain"), ("calendar", "julian"),
    ("signal_type", "person"), ("resolution", "exact"), ("orientation", "sideways"),
])
def test_unknown_enum_values_are_refused_not_ignored(name, value):
    with pytest.raises(SignalQueryError, match=name):
        parse(**{name: value})


def test_unknown_detector_is_refused():
    with pytest.raises(SignalQueryError):
        parse(detector="sentiment")


def test_null_buckets_are_filterable_by_name():
    f = parse(confidence="unrecorded", language="unspecified", orientation="unrecorded")
    assert f.confidence == ("unrecorded",) and f.languages == ("unspecified",)


@pytest.mark.parametrize("value", ["Day Month", "1abc", "x" * 65, "a;b", ""])
def test_method_names_are_pattern_checked(value):
    if value == "":
        assert parse(method=value).methods == ()   # blank = not given
        return
    with pytest.raises(SignalQueryError, match="method"):
        parse(method=value)


@pytest.mark.parametrize("value", ["Q3579", "place Q3579", "place:", "place:Q1 OR 1=1"])
def test_place_keys_are_pattern_checked(value):
    with pytest.raises(SignalQueryError, match="place_key"):
        parse(place_key=value)


def test_well_formed_place_key_is_accepted():
    assert parse(place_key="wikidata:Q3579").place_keys == ("wikidata:Q3579",)


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", "²", "٣"])
def test_hash_id_must_be_a_positive_ascii_integer(value):
    with pytest.raises(SignalQueryError, match="hash_id"):
        parse(hash_id=value)


def test_too_many_values_are_refused():
    with pytest.raises(SignalQueryError, match="at most"):
        parse(method=[f"m{i}" for i in range(sq.MAX_FILTER_VALUES + 1)])


def test_evidence_text_is_bounded_and_trimmed():
    assert parse(evidence_text="  Tripoli ").evidence_text == "Tripoli"
    assert parse(evidence_text="   ").evidence_text is None
    with pytest.raises(SignalQueryError, match="evidence_text"):
        parse(evidence_text="x" * (sq.MAX_EVIDENCE_TEXT + 1))


@pytest.mark.parametrize("value", ["2026-13-01", "01/10/2026", "2026-10", "tomorrow"])
def test_event_dates_must_be_iso(value):
    with pytest.raises(SignalQueryError, match="event_from"):
        parse(event_from=value)


def test_inverted_event_window_is_refused():
    with pytest.raises(SignalQueryError, match="after"):
        parse(event_from="2026-10-02", event_to="2026-10-01")
    assert parse(event_from="2026-10-01", event_to="2026-10-01").event_to == "2026-10-01"


# --- document criteria ----------------------------------------------------------

def test_no_document_parameters_is_the_empty_criteria():
    assert criteria() == (Criteria(), None)


def test_document_parameters_map_to_canonical_criteria():
    c, saved = criteria(source_id=["2", "1"], file_type="pdf")
    assert saved is None
    assert sorted(c.sources) == [1, 2] and list(c.file_types) == ["pdf"]


@pytest.mark.parametrize("value", ["abc", "-3", "1.0", "²", "٣", "0"])
def test_document_ids_must_be_positive_ascii_integers(value):
    with pytest.raises(SignalQueryError, match="source_id"):
        criteria(source_id=value)


def test_saved_search_is_loaded_through_the_callers_permission_check():
    seen = []
    stored = Criteria()

    def load(search_id):
        seen.append(search_id)
        return stored

    assert criteria(load, saved_search_id="7") == (stored, 7)
    assert seen == [7]


def test_unreadable_saved_search_is_a_lookup_error_not_a_400():
    # absent and not-permitted are the same answer (404 at the route)
    with pytest.raises(LookupError):
        criteria(lambda _id: None, saved_search_id="7")


@pytest.mark.parametrize("value", ["abc", "0", "-1", "²"])
def test_saved_search_id_must_be_a_positive_integer(value):
    with pytest.raises(SignalQueryError, match="saved_search_id"):
        criteria(lambda _id: Criteria(), saved_search_id=value)


def test_saved_search_cannot_be_mixed_with_document_filters():
    with pytest.raises(SignalQueryError, match="cannot be combined"):
        criteria(lambda _id: Criteria(), saved_search_id="7", source_id="1")


def test_invalid_criteria_values_become_400s():
    with pytest.raises(SignalQueryError, match="document criteria"):
        criteria(keyword_logic="XOR")


# --- paging ---------------------------------------------------------------------

@pytest.mark.parametrize("limit,offset", [(0, 0), (sq.MAX_PAGE_SIZE + 1, 0), (10, -1),
                                          ("x", 0), (10, "y")])
def test_paging_is_bounded(limit, offset):
    with pytest.raises(SignalQueryError):
        sq._page(limit, offset)
