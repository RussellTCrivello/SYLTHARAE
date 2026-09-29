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

        # Horizon corpus: temporal signals on the first matched content.
        # Reference date for the bucket assertions is 2026-01-10 (as_of).
        import hashlib as _hashlib
        cur.execute("SELECT content FROM contents_raw WHERE hash_id = %s"
                    " ORDER BY chunk_seq LIMIT 1", (matched_hashes[0],))
        _body = cur.fetchone()[0] or "seed document body"
        _sentence = _body[:min(40, len(_body))]
        _signals = [
            # (signal_type, date_from, date_to, orientation, expected bucket)
            ("date_reference", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5),
             "future", "overdue"),
            ("date_reference", datetime.date(2026, 1, 12), datetime.date(2026, 1, 12),
             "future", "week"),
            ("relative_reference", datetime.date(2026, 2, 1), datetime.date(2026, 2, 1),
             "future", "month"),
            ("date_reference", datetime.date(2026, 3, 15), datetime.date(2026, 3, 15),
             "future", "quarter"),
            ("date_reference", datetime.date(2027, 1, 1), datetime.date(2027, 1, 1),
             "future", "later"),
            # A past mention: resolved but not part of the horizon proper.
            ("date_reference", datetime.date(2026, 1, 2), datetime.date(2026, 1, 2),
             "past", None),
        ]
        for i, (stype, d_from, d_to, orient, _bucket) in enumerate(_signals):
            cur.execute(
                "INSERT INTO content_signals (hash_id, detector, detector_ver,"
                " signal_type, value, surface, char_start, char_end, language,"
                " calendar, resolution, date_from, date_to, text_orientation,"
                " anchor_date, evidence, dedup_key, method, confidence,"
                " confidence_basis, evidence_sentence, sentence_start,"
                " sentence_end)"
                " VALUES (%s, 'temporal', 'temporal-1.1.0', %s, %s, %s, 3, 20,"
                " 'en', 'gregorian', 'absolute', %s, %s, %s, %s, %s::jsonb, %s,"
                " 'parse', 'high', 'explicit_day', %s, 0, %s)",
                (matched_hashes[0], stype, f"2026-01-{i + 1:02d}", f"day {i}",
                 d_from, d_to, orient, d_from,
                 '{"normalized": "seed"}',
                 _hashlib.sha256(f"{tag}-sig-{i}".encode()).hexdigest(),
                 _sentence, len(_sentence)))
        # Relationship corpus: the tie content appears in a second context
        # (source s2) and carries a second path in its own context.
        tie_dup_path, _, _ = document(cur, source_id=s1, side_id=d1,
                                      text="ignored", hash_id=tie_hash,
                                      file_date=datetime.date(2026, 1, 1))
        paths[s1].append(tie_dup_path)
        tie_x_path, _, _ = document(cur, source_id=s2, side_id=d1,
                                    text="ignored", hash_id=tie_hash,
                                    file_date=datetime.date(2026, 1, 2))
        paths[s2].append(tie_x_path)
        # Change corpus: recorded modification history (migration 0029)
        # on two matched occurrences. previous/current values mirror the
        # rename writer's shape; changed_at is NOW() by default.
        for i, (pid, before, after) in enumerate((
                (paths[s1][0], "report_before_a.txt", "report_after_a.txt"),
                (paths[s2][0], "report_before_b.txt", "report_after_b.txt"))):
            cur.execute(
                "INSERT INTO path_revisions (path_id, change_kind,"
                " old_values, new_values)"
                " VALUES (%s, 'modified', %s::jsonb, %s::jsonb)",
                (pid, f'{{"file_name": "{before}", "file_path": "/seed/before"}}',
                 f'{{"file_name": "{after}", "file_path": "/seed/after"}}'))
        # An undated signal: never a bucket, not listed by the horizon.
        cur.execute(
            "INSERT INTO content_signals (hash_id, detector, detector_ver,"
            " signal_type, value, surface, char_start, char_end, resolution,"
            " evidence, dedup_key)"
            " VALUES (%s, 'temporal', 'temporal-1.1.0', 'ambiguous_reference',"
            " 'someday', 'someday', 5, 12, 'ambiguous', '{}'::jsonb, %s)",
            (matched_hashes[0],
             _hashlib.sha256(f"{tag}-sig-undated".encode()).hexdigest()))

        # Entity/Place corpus. Two places; one ambiguous mention keeps both
        # as candidates (never picked); one identified mention of place A;
        # one identified mention of place B whose confidence was never
        # recorded (unprovenanced, not zero); one unresolved mention (no
        # candidates: out of per-place attribution); place B is retired.
        cur.execute(
            "INSERT INTO geo_places (place_key, label, feature_type,"
            " country_codes, source) VALUES (%s, %s, 'city', '{LY}', 'seed')"
            " RETURNING id", (f"zz:tripoli-{tag}", f"Tripoli {tag}"))
        place_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO geo_places (place_key, label, feature_type,"
            " country_codes, source, retired) VALUES (%s, %s, 'country',"
            " '{GE}', 'seed', TRUE) RETURNING id", (f"zz:georgia-{tag}", f"Georgia {tag}"))
        place_b = cur.fetchone()[0]

        def _place_signal(resolution, hash_id, with_conf, dedup_suffix, place_ids):
            cur.execute(
                "INSERT INTO content_signals (hash_id, detector, detector_ver,"
                " signal_type, value, surface, char_start, char_end, resolution,"
                " evidence, dedup_key, method, confidence, confidence_basis,"
                " evidence_sentence, sentence_start, sentence_end)"
                " VALUES (%s, 'places', 'places-1.0.0+seed', 'place_mention',"
                " %s, %s, 0, 9, %s, '{}'::jsonb, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (hash_id, "place", "PlaceName", resolution,
                 _hashlib.sha256(f"{tag}-place-{dedup_suffix}".encode()).hexdigest(),
                 # Provenance is all-or-nothing (m0018): a pre-provenance row
                 # has no method, no confidence, no sentence at all.
                 "gazetteer" if with_conf else None,
                 "high" if with_conf else None,
                 "explicit" if with_conf else None,
                 _sentence if with_conf else None,
                 0 if with_conf else None,
                 len(_sentence) if with_conf else None))
            signal_id = cur.fetchone()[0]
            for pid in place_ids:
                cur.execute("INSERT INTO content_signal_places (signal_id,"
                            " place_id) VALUES (%s, %s)", (signal_id, pid))

        _place_signal("identified", matched_hashes[0], True, "a1", [place_a])
        _place_signal("identified", matched_hashes[1], False, "b1", [place_b])
        _place_signal("ambiguous", matched_hashes[0], True, "amb", [place_a, place_b])
        cur.execute(
            "INSERT INTO content_signals (hash_id, detector, detector_ver,"
            " signal_type, value, surface, char_start, char_end, resolution,"
            " evidence, dedup_key)"
            " VALUES (%s, 'places', 'places-1.0.0+seed', 'place_mention',"
            " 'somewhere', 'Somewhere', 0, 9, 'unresolved', '{}'::jsonb, %s)",
            (matched_hashes[0],
             _hashlib.sha256(f"{tag}-place-unres".encode()).hexdigest()))
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
    extra = ({"as_of": "2026-01-10"}
             if any(p.name == "as_of" for p in report.parameters) else {})
    values = report.normalize_parameters(
        {"criteria": {"text": corpus["word"]}, **extra})
    # A viewer dataset is bound per reader (fail-closed without one); a
    # plain integer exercises the viewer path for latest.entries@1 without
    # affecting any other dataset's binding.
    scope = AccessScope.unrestricted(user_id=1)
    names, types, rows = _execute(corpus["conn"], ds.bind(values, scope))
    assert names == [c.name for c in ds.columns]
    for column, oid in zip(ds.columns, types):
        assert oid in COLUMN_TYPES[column.type], (key, column.name, oid)
    assert rows or key == "change.removed@1", (
        f"guard: {key} returned nothing, so the checks below are empty "
        "- the removed state is empty by expectation until a removal "
        "operation records events (the report test seeds one)")
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
        assert cur.fetchone()[0] == counted[0] == len(rows) == 7
    expected = sorted(sum(corpus["paths"].values(), []))
    assert sorted(r[0] for r in rows) == expected
    again = _execute(corpus["conn"], listing.bind(values, scope))[2]
    assert again == rows, "same snapshot, same parameters: same order"
    # The default criteria sort is file date newest first, then id - the tie
    # on 2026-01-01 is broken by the unique path id.
    dates = [r[3] for r in rows]
    assert dates == sorted(dates, reverse=True)
    tied = [r[0] for r in rows if r[3] == datetime.date(2026, 1, 1)]
    assert tied == sorted(tied) and len(tied) == 3, (
        "three paths share the tie date: two occurrences of the tie content "
        "plus the day's first document; the unique path id breaks the tie")


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
    exact = dataclasses.replace(small, row_limit=7)
    _, _, rows = _execute(corpus["conn"], exact.bind(values, AccessScope.unrestricted()))
    assert len(rows) == 7 == exact.row_limit


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
    # The tie content's second context (s2) matches the criteria too, so
    # the s2 scope now sees 3 contents; the tie's unknown count rides along.
    assert hit[3] == 3 and hit[4] == 4 and hit[5] == 1 and hit[6] == 3
    cat_ds = REGISTRY.dataset("category_analysis.summary@1")
    _, _, rows = _execute(corpus["conn"], cat_ds.bind(values, only_s2))
    a = next(r for r in rows if r[1] == f"catA{tag}")
    assert a[4] == 3 and a[5] == 135 and a[6] == 0 and a[7] == 3
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


def test_the_horizon_buckets_against_the_declared_reference_date(corpus):
    """The report's buckets come from the same definition as the explorer's,
    relative to the run's declared as_of - so a stored run is reproducible."""
    from services.detection.signal_query import DEFAULT_BUCKETS
    values = REGISTRY.report("horizon").normalize_parameters({
        "criteria": {"text": corpus["word"]},
        "as_of": "2026-01-10"})
    names, _, rows = _new_dataset(corpus, "horizon.signals@1", values)
    assert names == [c.name for c in REGISTRY.dataset("horizon.signals@1").columns]
    by_value = {r[7]: r for r in rows}
    expected = {"2026-01-01": "overdue", "2026-01-02": "week",
                "2026-01-03": "month", "2026-01-04": "quarter",
                "2026-01-05": "later"}
    for value, bucket in expected.items():
        assert by_value[value][5] == bucket, (value, by_value[value][5])
    # The past mention and the undated signal are out of the horizon proper.
    assert len(rows) == 5
    assert all(r[5] in DEFAULT_BUCKETS for r in rows)
    # Soonest event date first (the horizon reads forward).
    dates = [r[3] for r in rows]
    assert dates == sorted(dates)
    # Provenance columns carried (seeded signals have them).
    assert all(r[10] == "high" and r[11] == "parse" for r in rows)


def test_a_different_reference_date_moves_the_buckets_not_the_rows(corpus):
    one = REGISTRY.report("horizon").normalize_parameters({
        "criteria": {"text": corpus["word"]}, "as_of": "2026-01-10"})
    other = REGISTRY.report("horizon").normalize_parameters({
        "criteria": {"text": corpus["word"]}, "as_of": "2027-06-01"})
    _, _, early = _new_dataset(corpus, "horizon.signals@1", one)
    _, _, late = _new_dataset(corpus, "horizon.signals@1", other)
    assert {r[5] for r in early} != {r[5] for r in late}, (
        "a later reference date pulls the same signals into earlier buckets")
    assert {r[0] for r in early} <= {r[0] for r in late}
    overdue = [r for r in late if r[5] == "overdue"]
    assert overdue and all(r[10] == "high" for r in overdue)


def test_entity_place_preserves_ambiguity_and_unprovenanced_confidence(corpus):
    tag = corpus["tag"]
    values = REGISTRY.report("entity_place").normalize_parameters(
        {"criteria": {"text": corpus["word"]}})
    names, _, rows = _new_dataset(corpus, "entity_place.mentions@1", values)
    assert names == [c.name for c in
                     REGISTRY.dataset("entity_place.mentions@1").columns]
    by_label = {r[1]: r for r in rows}
    a = by_label[f"Tripoli {tag}"]
    b = by_label[f"Georgia {tag}"]
    # row: 0 id, 1 label, 2 feature, 3 countries, 4 retired,
    #      5 ident_contents, 6 ident_occ, 7 amb_contents, 8 amb_occ,
    #      9 unk_conf, 10 first_path_id, 11 first_file_name
    assert a[2] == "city" and a[3] == "LY" and a[4] is False
    assert a[5] == 1 and a[6] == 1, "only the resolved mention counts as identified"
    assert a[7] == 1 and a[8] == 1, "the ambiguous mention is counted - separately"
    assert a[9] == 0
    assert b[4] is True, "a retired place is marked, not hidden"
    assert b[5] == 1 and b[6] == 1
    assert b[7] == 1 and b[8] == 1
    assert b[9] == 1, "its unprovenanced mention is carried as unknown, not zero"
    # Neither place absorbed the other's mention: ambiguity preserved.
    assert a[6] + b[6] == 2
    # The unresolved mention names no place: exactly two rows.
    assert len(rows) == 2
    # Representative documents: lowest matched path ids of each place (they
    # may legitimately share the document that carries the ambiguity).
    assert a[10] > 0 and b[10] > 0


def test_latest_classifies_per_reader_inside_one_snapshot(corpus):
    """The per-viewer dataset: everything new for a reader without a
    baseline (an absent row, never zero), the split exactly at the
    reader's recorded progress afterwards, arrivals beyond it new and
    nothing else re-marked, another reader's progress independent, and
    recency a property of the data reported alongside - not instead of -
    reading progress."""
    from core.reporting.baselines import record_baseline

    conn = corpus["conn"]
    ds = REGISTRY.dataset("latest.entries@1")
    report = REGISTRY.report("latest")
    values = report.normalize_parameters({"criteria": {"text": corpus["word"]}})
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rl_{corpus['tag']}",))
        reader = cur.fetchone()[0]
    scope = AccessScope.unrestricted(user_id=reader)

    def _rows(bound):
        names, _, rows = _execute(conn, bound)
        return names, rows

    names, rows1 = _rows(ds.bind(values, scope))
    path_id, view_state, recent = (names.index("path_id"),
                                   names.index("view_state"),
                                   names.index("recently_created"))
    assert len(rows1) == 7, "the same matched set the search listing counts"
    assert all(r[view_state] == "new_since_last_view" for r in rows1), (
        "no baseline yet: a first view is new by definition, not 'zero seen'")
    # Newest first by creation, ties broken by the unique path id.
    pairs = [(r[names.index("date_creation")], r[path_id]) for r in rows1]
    assert pairs == sorted(pairs, key=lambda t: (t[0], t[1]), reverse=True)
    # Every seeded path was created today (the seeder stamps CURRENT_DATE):
    # recently created is reported alongside, whatever the reading state.
    assert all(r[recent] is True for r in rows1)

    # Reading the view advances this reader: now everything is previously
    # seen, split exactly at the recorded progress.
    bound = ds.bind(values, scope)
    record_baseline(reader, bound.progress_key,
                    max(r[path_id] for r in rows1), conn=conn)
    _, rows2 = _rows(ds.bind(values, scope))
    assert all(r[view_state] == "previously_seen" for r in rows2)

    # One arrival: exactly it is new; nothing else is re-marked.
    with conn, conn.cursor() as cur:
        fresh_pid = document(cur, source_id=corpus["s1"], side_id=side(cur),
                             text=f"{corpus['word']} fresh after baseline")[0]
    _, rows3 = _rows(ds.bind(values, scope))
    assert len(rows3) == 8
    states = {r[path_id]: r[view_state] for r in rows3}
    assert states[fresh_pid] == "new_since_last_view"
    assert sum(1 for v in states.values() if v == "new_since_last_view") == 1

    # Another reader's progress is their own: all new for them.
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rl2_{corpus['tag']}",))
        reader_b = cur.fetchone()[0]
    _, rows_b = _rows(ds.bind(values, AccessScope.unrestricted(user_id=reader_b)))
    assert all(r[view_state] == "new_since_last_view" for r in rows_b)

    # Recency is the data, not the reading: an old creation date drops out
    # of recently_created for every reader alike.
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE paths SET date_creation = %s WHERE id = %s",
                    (datetime.date(2025, 6, 1), fresh_pid))
    _, rows4 = _rows(ds.bind(values, scope))
    by_id = {r[path_id]: r for r in rows4}
    assert by_id[fresh_pid][recent] is False
    assert all(r[recent] is True for pid, r in by_id.items() if pid != fresh_pid)


def test_change_reports_the_three_states_per_reader(corpus):
    """The Change report: added / modified / removed since this reader last
    saw the view. First view shows every state in full (no watermark is
    never a zero); the watermark is one per reader and view; a new event
    after it is shown again and nothing else is re-marked; the removed
    state reports the previous values and no current value."""
    from core.reporting.baselines import record_baseline

    conn = corpus["conn"]
    ds_added = REGISTRY.dataset("change.added@1")
    ds_modified = REGISTRY.dataset("change.modified@1")
    ds_removed = REGISTRY.dataset("change.removed@1")
    values = REGISTRY.report("change").normalize_parameters(
        {"criteria": {"text": corpus["word"]}})
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rc_{corpus['tag']}",))
        reader = cur.fetchone()[0]
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rc2_{corpus['tag']}",))
        reader_b = cur.fetchone()[0]
    scope = AccessScope.unrestricted(user_id=reader)

    def _rows(ds, sc=scope):
        return _execute(conn, ds.bind(values, sc))[2]

    def _col(rows, names, name):
        return [r[names.index(name)] for r in rows]

    # First view: every state in full.
    added = _execute(conn, ds_added.bind(values, scope))
    names_a, rows_added = added[0], added[2]
    corpus_ids = {pid for ids in corpus["paths"].values() for pid in ids}
    assert corpus_ids <= set(_col(rows_added, names_a, "path_id")), (
        "the whole matched set, no baseline yet - nothing held back")
    assert set(_col(rows_added, names_a, "change_kind")) == {"added"}
    assert all(v is None for v in
               _col(rows_added, names_a, "previous_value")), (
        "an added row has no previous value - NULL, never an empty string")
    assert _col(rows_added, names_a, "current_value") == \
        _col(rows_added, names_a, "file_name")
    rows_modified = _execute(conn, ds_modified.bind(values, scope))[2]
    names_m = _execute(conn, ds_modified.bind(values, scope))[0]
    assert len(rows_modified) == 2
    assert _col(rows_modified, names_m, "previous_value") == [
        "report_before_b.txt", "report_before_a.txt"], "newest event first"
    assert _col(rows_modified, names_m, "current_value") == [
        "report_after_b.txt", "report_after_a.txt"]
    assert all(v is not None for v in
               _col(rows_modified, names_m, "detected_at"))
    assert _execute(conn, ds_removed.bind(values, scope))[2] == []

    # A recorded removal (on a ghost occurrence created here so the corpus
    # counts above stay untouched): previous values, no current value.
    with conn, conn.cursor() as cur:
        ghost_pid = document(cur, source_id=corpus["s1"], side_id=side(cur),
                             text=f"{corpus['word']} ghost for removal")[0]
        cur.execute(
            "INSERT INTO path_revisions (path_id, change_kind, old_values,"
            " new_values) VALUES (%s, 'removed', %s::jsonb, %s::jsonb)"
            " RETURNING id",
            (ghost_pid, '{"file_name": "ghost.txt", "file_path": "/seed/ghost"}',
             '{"reason": "seed removal event"}'))
        removed_rev = cur.fetchone()[0]
    rows_removed = _execute(conn, ds_removed.bind(values, scope))[2]
    names_r = _execute(conn, ds_removed.bind(values, scope))[0]
    assert len(rows_removed) == 1
    assert rows_removed[0][names_r.index("revision_id")] == removed_rev
    assert rows_removed[0][names_r.index("previous_value")] == "ghost.txt"
    assert rows_removed[0][names_r.index("current_value")] is None, (
        "a removed row has no current value - not an empty string")
    # The new occurrence is added for a fresh reader, together with the
    # removal: another reader's watermark is their own.
    scope_b = AccessScope.unrestricted(user_id=reader_b)
    rows_added_b = _execute(conn, ds_added.bind(values, scope_b))[2]
    ids_b = set(_col(rows_added_b, names_a, "path_id"))
    assert ghost_pid in ids_b and corpus_ids <= ids_b, (
        "a fresh reader sees every occurrence - corpus and ghost alike - "
        "their watermark is their own")

    # Reading the view moves this reader's watermark: modifications and
    # removals already seen are not re-marked; same-day additions remain
    # visible (date granularity is the ingestion event's own resolution);
    # the old-dated occurrence drops out.
    key = ds_added.bind(values, scope).progress_key
    record_baseline(reader, key, max(r[names_a.index("path_id")]
                                     for r in rows_added_b), conn=conn)
    conn.commit()  # the watermark's NOW() is this transaction's start time;
    # a later event must be recorded in a LATER transaction to be newer.
    assert _execute(conn, ds_modified.bind(values, scope))[2] == []
    assert _execute(conn, ds_removed.bind(values, scope))[2] == []
    rows_added_2 = _execute(conn, ds_added.bind(values, scope))[2]
    assert ghost_pid in set(_col(rows_added_2, names_a, "path_id"))
    assert all(str(v)[:10] == str(datetime.date.today())
               for v in _col(rows_added_2, names_a, "detected_at")), (
        "after the watermark: same-day additions remain visible (the "
        "ingestion event's own resolution is a date) - older ones drop out")

    # An event after the watermark is shown again - exactly it.
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO path_revisions (path_id, change_kind, old_values,"
            " new_values) VALUES (%s, 'modified', %s::jsonb, %s::jsonb)",
            (corpus["paths"][corpus["s1"]][0],
             '{"file_name": "later_before.txt"}',
             '{"file_name": "later_after.txt"}'))
    rows_modified_2 = _execute(conn, ds_modified.bind(values, scope))[2]
    assert len(rows_modified_2) == 1
    assert rows_modified_2[0][names_m.index("previous_value")] == \
        "later_before.txt"


def test_relationship_counts_contexts_not_path_rows(corpus):
    """The tie content lives in two contexts; one of them carries two
    matched paths. The report must answer in contexts, with multiplicity
    kept as a column - never as extra relationship rows."""
    values = REGISTRY.report("relationship").normalize_parameters(
        {"criteria": {"text": corpus["word"]}})
    names, _, rows = _new_dataset(corpus, "relationship.contexts@1", values)
    assert names == [c.name for c in
                     REGISTRY.dataset("relationship.contexts@1").columns]
    # The duplicated tie content: exactly two context rows, whatever the
    # number of path rows.
    tie_rows = [r for r in rows if r[3] == "src_0" or r[3] == "src_1"]
    tie_rows = [r for r in tie_rows if r[5] + r[4] > 0]
    per_hash = {}
    for r in rows:
        per_hash.setdefault(r[1], []).append(r)
    dup = max(per_hash.values(), key=len)
    assert len(dup) == 2, (
        "the same content in two (source, side) pairs is two relationship rows")
    contexts = sorted(r[4] for r in dup)
    assert contexts == [1, 2], (
        "multiplicity inside a context stays a column: 2 paths + 1 path")
    assert all(r[5] == 1 for r in dup), "each context sees exactly one sibling"
    assert all(r[6] == 1 for r in dup), "the sibling is on the other source"
    # A single-context content: repeated paths are not relationships.
    singles = [rs for h, rs in per_hash.items() if len(rs) == 1]
    assert singles and all(rs[0][5] == 0 for rs in singles), (
        "a content in one context has zero sibling contexts")
    # Ordered most cross-posted first.
    sibs = [r[5] for r in rows]
    assert sibs == sorted(sibs, reverse=True)
