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
        tie_pid, tie_hash, _ = document(cur, source_id=s1, side_id=d1,
                                        text=f"{word} tie", file_date=datetime.date(2026, 1, 1))
        paths[s1].append(tie_pid)
        # Word frequencies for the keyness datasets: the matching contents
        # use a word of their own, one other content is the reference.
        cur.execute("SELECT DISTINCT hc.hash_id FROM paths p JOIN hash_contexts hc"
                    " ON hc.id = p.context_id WHERE p.id = ANY(%s)",
                    (sum(paths.values(), []),))
        matched_hashes = [h for (h,) in cur.fetchall()]
        for hash_id in matched_hashes:
            word_counts(cur, hash_id, {f"kw{tag}": 40, f"common{tag}": 5})
        # One count was never measured: the row exists, its count is unknown
        # (word_count IS NULL) and must never be read as zero downstream.
        word_counts(cur, matched_hashes[0], {f"unk{tag}": None})
        _, ref_hash, _ = document(cur, source_id=s2, side_id=d1, text="unrelated reference")
        word_counts(cur, ref_hash, {f"common{tag}": 50, f"other{tag}": 30})

        # Step 17 corpus: categories and keywords over the same documents.
        # Category A = {kw, common, unk}; category B = {common} (overlaps A
        # through `common`). Keyword 1 = pattern [kw, common] with
        # keywords_paths on every matched occurrence (counts: 3 on the first
        # source, 1 on the second, one count unknown); keyword 2 = [other],
        # which no matched document contains.
        def _word_id(w):
            cur.execute("INSERT INTO words (word) VALUES (%s)"
                        " ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word"
                        " RETURNING id", (w,))
            return cur.fetchone()[0]

        kw_id, common_id = _word_id(f"kw{tag}"), _word_id(f"common{tag}")
        other_id, unk_id = _word_id(f"other{tag}"), _word_id(f"unk{tag}")

        def _category(name_word, member_ids):
            name_id = _word_id(name_word)
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (name_id,))
            cid = cur.fetchone()[0]
            for wid in member_ids:
                cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                            " VALUES (%s, %s)", (wid, cid))
            return cid

        from core.serialization import pack_int_list

        cat_a = _category(f"catA{tag}", [kw_id, common_id, unk_id])
        cat_b = _category(f"catB{tag}", [common_id])
        cur.execute("INSERT INTO keywords (keyword, category_id)"
                    " VALUES (%s, %s) RETURNING id",
                    (pack_int_list([kw_id, common_id]), cat_a))
        keyword_id = cur.fetchone()[0]
        cur.execute("INSERT INTO keywords (keyword, category_id)"
                    " VALUES (%s, %s) RETURNING id",
                    (pack_int_list([other_id]), cat_b))
        # Content-level keyword hits (keywords_hashs, UNIQUE (hash_id,
        # keyword_id)): count 2 on every matched content except the tie
        # document's, whose count was never measured (word_count NULL).
        for h in matched_hashes:
            cur.execute("INSERT INTO keywords_hashs (hash_id, keyword_id,"
                        " word_count) VALUES (%s, %s, %s)",
                        (h, keyword_id, None if h == tie_hash else 2))
    yield {"conn": conn, "word": word, "tag": tag, "s1": s1, "s2": s2,
           "paths": paths, "cat_a": cat_a, "cat_b": cat_b,
           "keyword_id": keyword_id}
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


# ---------------------------------------------------------------------------
# Step 17: Keyword Intelligence and Category Analysis against real data.
# ---------------------------------------------------------------------------

def _new_dataset(corpus, key, values):
    return _execute(corpus["conn"], REGISTRY.dataset(key).bind(
        values, AccessScope.unrestricted()))


def test_keyword_intelligence_lists_every_keyword_over_the_matched_set(corpus):
    tag = corpus["tag"]
    names, _, rows = _new_dataset(
        corpus, "keyword_intelligence.matches@1", _values(corpus))
    assert names == [c.name for c in
                     REGISTRY.dataset("keyword_intelligence.matches@1").columns]
    by_label = {r[1]: r for r in rows}
    assert len(rows) == 2, "the zero-presence keyword is listed too"
    hit = by_label[f"kw{tag} common{tag}"]
    assert hit[0] == corpus["keyword_id"] and hit[2] == f"catA{tag}"
    assert hit[3] == 5, "every matched content contains the pattern"
    assert hit[4] == 8, "count 2 on four contents; the unknown one excluded"
    assert hit[5] == 1, "the unknown count is carried, not read as zero"
    assert hit[6] == 5
    zero = by_label[f"other{tag}"]
    assert zero[3] == zero[4] == zero[5] == 0, (
        "zero presence is a measured zero, not a missing measurement")
    # The label joins the pattern's words in pattern order: alphabetically
    # `common...` would come first, the stored pattern says otherwise.
    assert f"kw{tag} common{tag}" in by_label


def test_category_analysis_counts_distinct_contents_and_keeps_overlap(corpus):
    tag = corpus["tag"]
    _, _, rows = _new_dataset(
        corpus, "category_analysis.summary@1", _values(corpus))
    by_name = {r[1]: r for r in rows}
    a = by_name[f"catA{tag}"]
    b = by_name[f"catB{tag}"]
    # row: category_id, category_name, member_words, keywords, contents,
    #      occurrences, unknown_count_rows, corpus_contents, content_share
    assert a[2] == 3 and a[3] == 1
    assert a[4] == 5, "5 distinct matched contents contain a member word"
    assert a[5] == 225, "5 contents x (40 + 5); the unknown count is excluded"
    assert a[6] == 1 and a[7] == 5
    assert abs(a[8] - 1.0) < 1e-12
    assert b[2] == 1 and b[3] == 1 and b[4] == 5 and b[5] == 25
    # Overlap is preserved, not partitioned away: the shared content is
    # counted in both categories, so the shares are per category and sum
    # beyond 100% by declaration.
    assert a[4] + b[4] == 10 > 5
    assert abs(a[8] + b[8] - 2.0) < 1e-12


def test_the_access_scope_narrows_the_new_datasets_before_retrieval(corpus):
    tag = corpus["tag"]
    only_s2 = AccessScope(user_id=1, role="viewer",
                          allowed_source_ids=(corpus["s2"],))
    values = _values(corpus)
    key_ds = REGISTRY.dataset("keyword_intelligence.matches@1")
    _, _, rows = _execute(corpus["conn"], key_ds.bind(values, only_s2))
    hit = next(r for r in rows if r[1] == f"kw{tag} common{tag}")
    assert hit[3] == 2 and hit[4] == 4 and hit[5] == 0 and hit[6] == 2
    cat_ds = REGISTRY.dataset("category_analysis.summary@1")
    _, _, rows = _execute(corpus["conn"], cat_ds.bind(values, only_s2))
    a = next(r for r in rows if r[1] == f"catA{tag}")
    assert a[4] == 2 and a[5] == 90 and a[7] == 2
    _, _, rows = _execute(corpus["conn"], cat_ds.bind(
        values, AccessScope(user_id=1, role="viewer", allowed_source_ids=())))
    assert all(r[4] == 0 and r[8] is None for r in rows), (
        "an empty matched set: measured zeros and an unknown share, never a zero share")


def test_the_new_capped_datasets_show_overflow_through_the_extra_row(corpus):
    values = _values(corpus)
    small = dataclasses.replace(
        REGISTRY.dataset("keyword_intelligence.matches@1"), row_limit=1)
    _, _, rows = _execute(corpus["conn"], small.bind(values, AccessScope.unrestricted()))
    assert len(rows) == 2 == small.row_limit + 1
    small_cat = dataclasses.replace(
        REGISTRY.dataset("category_analysis.summary@1"), row_limit=1)
    _, _, rows = _execute(corpus["conn"], small_cat.bind(values, AccessScope.unrestricted()))
    assert len(rows) == 2 == small_cat.row_limit + 1
    assert rows[0][4] >= rows[1][4], "ordered by contents, most widespread first"
