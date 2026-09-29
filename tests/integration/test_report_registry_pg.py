"""Step 13 against PostgreSQL: every registered dataset executes as declared.

Parsing proves nothing about SQL, so each dataset in the shipped registry is
bound and executed on the migrated schema. Checked per dataset: the column
names and order equal the declaration; each column's PostgreSQL type OID is
one the declared type allows; declared non-nullable columns contain no NULL;
the fetch uses row_limit + 1 so overflow is visible; the order is
deterministic; the access scope is applied in SQL; and the listing agrees
with the criteria compiler's own count.
"""

import dataclasses
import datetime
import uuid

import pytest

from core.criteria import AccessScope, compile_criteria, from_dict
from core.reporting import REGISTRY
from core.reporting.model import COLUMN_TYPES

from _seed import connect, document, side, source, word_counts

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def corpus(pg_db, app):
    tag = uuid.uuid4().hex[:8]
    word = f"zrr{tag}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1, s2 = source(cur), source(cur)
        d1 = side(cur)
        paths = {s1: [], s2: []}
        for i in range(4):
            sid = s1 if i % 2 == 0 else s2
            paths[sid].append(document(
                cur, source_id=sid, side_id=d1, text=f"report {word} number {i}",
                file_type="pdf", file_date=datetime.date(2026, 1, 1 + i))[0])
        # Same file date for two rows: order must still be total (p.id tie-break).
        paths[s1].append(document(cur, source_id=s1, side_id=d1, text=f"{word} tie",
                                  file_date=datetime.date(2026, 1, 1))[0])
        # Word frequencies for the keyness datasets: the matching contents
        # use a word of their own, one other content is the reference.
        cur.execute("SELECT DISTINCT hc.hash_id FROM paths p JOIN hash_contexts hc"
                    " ON hc.id = p.context_id WHERE p.id = ANY(%s)",
                    (sum(paths.values(), []),))
        for (hash_id,) in cur.fetchall():
            word_counts(cur, hash_id, {f"kw{tag}": 40, f"common{tag}": 5})
        _, ref_hash, _ = document(cur, source_id=s2, side_id=d1, text="unrelated reference")
        word_counts(cur, ref_hash, {f"common{tag}": 50, f"other{tag}": 30})
    yield {"conn": conn, "word": word, "s1": s1, "s2": s2, "paths": paths}
    conn.close()


def _execute(conn, bound):
    with conn.cursor() as cur:
        cur.execute(bound.sql, bound.params)
        return [d.name for d in cur.description], [d.type_code for d in cur.description], \
            cur.fetchall()


def _values(corpus):
    return REGISTRY.report("search_results").normalize_parameters(
        {"criteria": {"text": corpus["word"]}})


@pytest.mark.parametrize("key", [d.key for d in REGISTRY.datasets])
def test_every_dataset_executes_with_its_declared_shape(corpus, key):
    ds = REGISTRY.dataset(key)
    report = next(r for r in REGISTRY.reports if key in r.datasets)
    values = report.normalize_parameters({"criteria": {"text": corpus["word"]}})
    names, types, rows = _execute(corpus["conn"], ds.bind(values, AccessScope.unrestricted()))
    assert names == [c.name for c in ds.columns]
    for column, oid in zip(ds.columns, types):
        assert oid in COLUMN_TYPES[column.type], (key, column.name, oid)
    assert rows, f"guard: {key} returned nothing, so the checks below are empty"
    for column_index, column in enumerate(ds.columns):
        if not column.nullable:
            assert all(row[column_index] is not None for row in rows), (key, column.name)
    assert len(rows) <= ds.row_limit + 1


def test_listing_matches_the_compiler_count_and_order_is_total(corpus):
    values = _values(corpus)
    scope = AccessScope.unrestricted()
    listing = REGISTRY.dataset("search_results.matches@1")
    count = REGISTRY.dataset("search_results.count@1")
    _, _, rows = _execute(corpus["conn"], listing.bind(values, scope))
    _, _, (counted,) = _execute(corpus["conn"], count.bind(values, scope))
    compiled = compile_criteria(from_dict(values["criteria"]), scope)
    with corpus["conn"].cursor() as cur:
        cur.execute(*compiled.count_sql())
        assert cur.fetchone()[0] == counted[0] == len(rows) == 5
    expected = sorted(sum(corpus["paths"].values(), []))
    assert sorted(r[0] for r in rows) == expected
    again = _execute(corpus["conn"], listing.bind(values, scope))[2]
    assert again == rows, "same snapshot, same parameters: same order"
    # The default criteria sort is file date newest first, then id - the tie
    # on 2026-01-01 is broken by the unique path id.
    dates = [r[3] for r in rows]
    assert dates == sorted(dates, reverse=True)
    tied = [r[0] for r in rows if r[3] == datetime.date(2026, 1, 1)]
    assert tied == sorted(tied) and len(tied) == 2


def test_the_access_scope_restricts_rows_before_retrieval(corpus):
    values = _values(corpus)
    listing = REGISTRY.dataset("search_results.matches@1")
    only_s2 = AccessScope(user_id=1, role="viewer", allowed_source_ids=(corpus["s2"],))
    _, _, rows = _execute(corpus["conn"], listing.bind(values, only_s2))
    assert sorted(r[0] for r in rows) == sorted(corpus["paths"][corpus["s2"]])
    _, _, none = _execute(corpus["conn"], listing.bind(
        values, AccessScope(user_id=1, role="viewer", allowed_source_ids=())))
    assert none == []
    count = REGISTRY.dataset("search_results.count@1")
    _, _, ((n,),) = _execute(corpus["conn"], count.bind(values, only_s2))
    assert n == len(corpus["paths"][corpus["s2"]])


def test_overflow_is_visible_through_the_extra_row(corpus):
    """A capped dataset fetches row_limit + 1: the runner can tell 'exactly
    row_limit matched' from 'more matched' without a second query."""
    values = _values(corpus)
    small = dataclasses.replace(REGISTRY.dataset("search_results.matches@1"), row_limit=3)
    _, _, rows = _execute(corpus["conn"], small.bind(values, AccessScope.unrestricted()))
    assert len(rows) == 4 == small.row_limit + 1
    exact = dataclasses.replace(small, row_limit=5)
    _, _, rows = _execute(corpus["conn"], exact.bind(values, AccessScope.unrestricted()))
    assert len(rows) == 5 == exact.row_limit


def test_a_hostile_phrase_is_data_not_sql(corpus):
    values = REGISTRY.report("search_results").normalize_parameters(
        {"criteria": {"phrases": ["x'); DROP TABLE paths; --"]}})
    bound = REGISTRY.dataset("search_results.count@1").bind(values, AccessScope.unrestricted())
    _, _, ((n,),) = _execute(corpus["conn"], bound)
    assert n == 0
    with corpus["conn"].cursor() as cur:
        cur.execute("SELECT to_regclass('public.paths') IS NOT NULL")
        assert cur.fetchone()[0] is True
