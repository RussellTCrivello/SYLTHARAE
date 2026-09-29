"""Registered datasets.

A dataset is a versioned, reviewed query. Changing anything that
``Dataset.semantic()`` covers (SQL, columns, types, nullability, limit,
semantics, roles, parameters, access mechanism) under an existing version is
refused by the lock check: add a new version instead, and keep the old one so
earlier runs stay interpretable.

Joins below use the ``rpt_`` alias prefix so they can never collide with the
aliases inside compiled criteria fragments (``p``, ``hc``, ``h``, ``ct`` and
correlated sub-selects).
"""

from __future__ import annotations

from typing import Tuple

from core.criteria.sql import CANONICAL_FROM
from core.detection import horizon as _horizon
from core.detection import place_intel as _places
from core.detection import temporal_intel as _temporal
from core.security.service import ALL_ROLES

from .model import (TOKEN_CRITERIA, TOKEN_LIMIT, TOKEN_SCOPE,
                   TOKEN_VIEWER_ID, TOKEN_VIEWER_KEY, Column, Dataset)

_READERS = tuple(ALL_ROLES)

SEARCH_RESULTS_MATCHES_V1 = Dataset(
    dataset_id="search_results.matches",
    version=1,
    description=(
        "File occurrences matching the criteria, one row per path, in the "
        "criteria's own sort order ending with the unique path id. Capped: at "
        "most row_limit rows; overflow is detected (row_limit + 1 fetched) "
        "and recorded as truncation, never hidden."),
    unit="path",
    semantics="capped",
    row_limit=5000,
    columns=(
        # paths.context_id is NOT NULL with a foreign key to hash_contexts,
        # whose source_id/side_id are NOT NULL foreign keys: the LEFT JOINs
        # of CANONICAL_FROM always find their row, so these are not nullable.
        Column("path_id", "integer", False, "File ID"),
        Column("file_name", "text", False, "File name"),
        Column("file_type", "text", False, "File type"),
        Column("file_date", "date", False, "File date"),
        Column("hash_id", "integer", False, "Content ID"),
        Column("source_name", "text", False, "Source"),
        Column("side_name", "text", False, "Side"),
    ),
    sql=(
        "SELECT p.id AS path_id, p.file_name, p.file_type, p.file_date, "  # nosec B608 # module constants only (CANONICAL_FROM, keyness thresholds); values are bound parameters
        "hc.hash_id, rpt_src.name AS source_name, rpt_side.name AS side_name "
        f"FROM {CANONICAL_FROM} "
        "LEFT JOIN sources rpt_src ON rpt_src.id = hc.source_id "
        "LEFT JOIN sides rpt_side ON rpt_side.id = hc.side_id "
        "WHERE {where} ORDER BY {order} LIMIT %s"
    ),
    sql_params=(TOKEN_CRITERIA, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
)

SEARCH_RESULTS_COUNT_V1 = Dataset(
    dataset_id="search_results.count",
    version=1,
    description=(
        "Exact number of distinct file occurrences matching the criteria, "
        "so a capped listing can state how many rows it did not show."),
    unit="path",
    semantics="exact",
    row_limit=1,
    columns=(Column("matched", "bigint", False, "Matching files"),),
    sql=(f"SELECT COUNT(DISTINCT p.id) AS matched FROM {CANONICAL_FROM} "  # nosec B608 # module constants only (CANONICAL_FROM, keyness thresholds); values are bound parameters
         "WHERE {where} LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
)

# ---------------------------------------------------------------------------
# Keyness (term frequencies of the selection against the rest of the visible
# collection). One content counts once however many paths/contexts it has:
# the unit of the corpora is the distinct content (hash), words are counted
# from words_hashs.word_count. Rows whose word_count is NULL are unknown, not
# zero: they are left out of every sum and counted in unknown_count_rows.
#
# G2 and Log Ratio are computed set-based in SQL only to *rank* terms; the
# analysis (core.analytics.kinds.keyness) recomputes both for every returned
# row with the reference implementation and fails the run on disagreement.
# ---------------------------------------------------------------------------

#: The literal G2 level below is thresholds.LL_P0001.value; a unit test pins
#: the two together so neither can change alone.
KEYNESS_SIGNIFICANT_G2 = "15.13"

_KEYNESS_CTES = (
    "WITH rpt_target AS ("  # nosec B608 # module constants only (CANONICAL_FROM, keyness thresholds); values are bound parameters
    f" SELECT DISTINCT hc.hash_id FROM {CANONICAL_FROM}"
    " WHERE {where} AND hc.hash_id IS NOT NULL"
    "), rpt_visible AS ("
    " SELECT DISTINCT rpt_hc.hash_id FROM paths rpt_p"
    " JOIN hash_contexts rpt_hc ON rpt_hc.id = rpt_p.context_id"
    " WHERE {scope}"
    "), rpt_counts AS ("
    " SELECT rpt_wh.word_id,"
    " COALESCE(SUM(rpt_wh.word_count) FILTER (WHERE rpt_t.hash_id IS NOT NULL), 0)::float8 AS a,"
    " COALESCE(SUM(rpt_wh.word_count) FILTER (WHERE rpt_t.hash_id IS NULL), 0)::float8 AS b"
    " FROM words_hashs rpt_wh"
    " JOIN rpt_visible rpt_v ON rpt_v.hash_id = rpt_wh.hash_id"
    " LEFT JOIN rpt_target rpt_t ON rpt_t.hash_id = rpt_wh.hash_id"
    " WHERE rpt_wh.word_count IS NOT NULL"
    " GROUP BY rpt_wh.word_id"
    "), rpt_tot AS ("
    " SELECT COALESCE(SUM(a), 0) AS c, COALESCE(SUM(b), 0) AS d FROM rpt_counts"
    "), rpt_g AS ("
    " SELECT rpt_k.word_id, rpt_k.a, rpt_k.b, rpt_n.c, rpt_n.d,"
    " 2 * ((CASE WHEN rpt_k.a > 0 THEN rpt_k.a * LN(rpt_k.a / (rpt_n.c * (rpt_k.a + rpt_k.b)"
    " / (rpt_n.c + rpt_n.d))) ELSE 0 END)"
    " + (CASE WHEN rpt_k.b > 0 THEN rpt_k.b * LN(rpt_k.b / (rpt_n.d * (rpt_k.a + rpt_k.b)"
    " / (rpt_n.c + rpt_n.d))) ELSE 0 END)) AS g2,"
    " LN(((CASE WHEN rpt_k.a = 0 THEN 0.5 ELSE rpt_k.a END) / rpt_n.c)"
    " / ((CASE WHEN rpt_k.b = 0 THEN 0.5 ELSE rpt_k.b END) / rpt_n.d)) / LN(2) AS log_ratio,"
    " SIGN(rpt_k.a::numeric * rpt_n.d::numeric - rpt_k.b::numeric * rpt_n.c::numeric) AS dir"
    " FROM rpt_counts rpt_k CROSS JOIN rpt_tot rpt_n"
    " WHERE rpt_n.c > 0 AND rpt_n.d > 0"
    ") "
)

_KEYNESS_DIRECTION = "CASE WHEN %s = 'under' THEN -1 ELSE 1 END"

TERM_KEYNESS_RANKED_V1 = Dataset(
    dataset_id="term_keyness.ranked",
    version=1,
    description=(
        "Terms whose relative frequency in the contents selected by the "
        "criteria differs from the other visible contents, in the requested "
        "direction (over: more frequent in the selection; under: less), "
        "ranked by log-likelihood G2 descending, then by term (words.word "
        "is unique). Top-N: the row_limit highest-ranked terms by "
        "declaration; the exact number of significant terms is in "
        "term_keyness.totals. Terms with equal relative frequency are in "
        "neither direction."),
    unit="term",
    semantics="top_n",
    row_limit=200,
    columns=(
        Column("term", "text", False, "Term"),
        Column("target_freq", "bigint", False, "Occurrences in the selection"),
        Column("reference_freq", "bigint", False, "Occurrences in the rest of the collection"),
        Column("g2", "numeric", False, "Log-likelihood (G2)"),
        Column("log_ratio", "numeric", False, "Log Ratio"),
    ),
    sql=(_KEYNESS_CTES
         + "SELECT rpt_w.word AS term, rpt_g.a::bigint AS target_freq,"  # nosec B608 # module constants only (CANONICAL_FROM, keyness thresholds); values are bound parameters
         " rpt_g.b::bigint AS reference_freq, rpt_g.g2, rpt_g.log_ratio"
         " FROM rpt_g JOIN words rpt_w ON rpt_w.id = rpt_g.word_id"
         f" WHERE rpt_g.dir = {_KEYNESS_DIRECTION}"
         " ORDER BY rpt_g.g2 DESC, rpt_w.word LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_SCOPE, "direction", TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria", "direction"),
    criteria_param="criteria",
    scope_column="rpt_hc.source_id",
)

TERM_KEYNESS_TOTALS_V1 = Dataset(
    dataset_id="term_keyness.totals",
    version=1,
    description=(
        "Exact corpus sizes for the keyness comparison: counted words and "
        "distinct contents in the selection and in the rest of the visible "
        "collection, the word records without a count (unknown, excluded "
        "from every sum), and the exact number of terms in the requested "
        "direction whose G2 reaches the p < 0.0001 level (15.13)."),
    unit="hash",
    semantics="exact",
    row_limit=1,
    columns=(
        Column("target_tokens", "bigint", False, "Counted words in the selection"),
        Column("reference_tokens", "bigint", False, "Counted words in the rest of the collection"),
        Column("target_contents", "bigint", False, "Contents in the selection"),
        Column("reference_contents", "bigint", False, "Contents in the rest of the collection"),
        Column("unknown_count_rows", "bigint", False, "Word records without a count"),
        Column("significant_terms", "bigint", False, "Significant terms"),
    ),
    sql=(_KEYNESS_CTES
         + "SELECT rpt_n.c::bigint AS target_tokens, rpt_n.d::bigint AS reference_tokens,"  # nosec B608 # module constants only (CANONICAL_FROM, keyness thresholds); values are bound parameters
         " (SELECT COUNT(*) FROM rpt_target rpt_t2"
         " JOIN rpt_visible rpt_v2 ON rpt_v2.hash_id = rpt_t2.hash_id) AS target_contents,"
         " (SELECT COUNT(*) FROM rpt_visible rpt_v3 LEFT JOIN rpt_target rpt_t3"
         " ON rpt_t3.hash_id = rpt_v3.hash_id WHERE rpt_t3.hash_id IS NULL) AS reference_contents,"
         " (SELECT COUNT(*) FROM words_hashs rpt_wn JOIN rpt_visible rpt_v4"
         " ON rpt_v4.hash_id = rpt_wn.hash_id WHERE rpt_wn.word_count IS NULL)"
         " AS unknown_count_rows,"
         f" (SELECT COUNT(*) FROM rpt_g WHERE rpt_g.g2 >= {KEYNESS_SIGNIFICANT_G2}"
         f" AND rpt_g.dir = {_KEYNESS_DIRECTION}) AS significant_terms"
         " FROM rpt_tot rpt_n LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_SCOPE, "direction", TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria", "direction"),
    criteria_param="criteria",
    scope_column="rpt_hc.source_id",
)

# ---------------------------------------------------------------------------
# Keyword Intelligence (step 17)
#
# One row per keyword: the keyword's reach inside the *contents* the
# criteria matched. keywords_hashs is the content-level store
# (hash_id, keyword_id, word_count, UNIQUE (hash_id, keyword_id)) - the
# same content identity the criteria compiler uses - so this dataset
# counts distinct contents and sums the stored per-content counts. A
# keyword whose stored pattern is unreadable fails the query loudly (the
# storage is either JSON from pack_int_list or a legacy blob that
# scripts/repair_data.py rewrites); a count that was never measured
# (word_count IS NULL) is never read as a zero - it is carried in
# unknown_count_rows.
# ---------------------------------------------------------------------------

_KEYWORD_INTELLIGENCE_FROM = (
    "WITH rpt_base AS ("
    " SELECT DISTINCT hc.hash_id AS hash_id FROM " + CANONICAL_FROM + " WHERE {where}"
    "), rpt_corpus AS ("
    " SELECT COUNT(*) AS n FROM rpt_base"
    "), rpt_hits AS ("
    " SELECT rpt_kh.keyword_id, COUNT(*) AS contents,"
    " COALESCE(SUM(rpt_kh.word_count), 0) AS occurrences,"
    " COUNT(*) FILTER (WHERE rpt_kh.word_count IS NULL) AS unknown_count_rows"
    " FROM keywords_hashs rpt_kh"
    " JOIN rpt_base ON rpt_base.hash_id = rpt_kh.hash_id"
    " GROUP BY rpt_kh.keyword_id"
    ") "
)

KEYWORD_INTELLIGENCE_MATCHES_V1 = Dataset(
    dataset_id="keyword_intelligence.matches",
    version=1,
    description=(
        "Every keyword, with the number of contents matched by the criteria "
        "that contain it, the total of the stored per-content counts, and "
        "the count of contents whose stored count is unknown (word_count IS "
        "NULL - excluded from the sum, never read as zero). The keyword "
        "pattern is its words joined with single spaces in pattern order. "
        "Capped: at most row_limit keywords, most widespread first; "
        "overflow is detected (row_limit + 1 fetched) and recorded as "
        "truncation, never hidden."),
    unit="keyword",
    semantics="capped",
    row_limit=1000,
    columns=(
        Column("keyword_id", "integer", False, "Keyword ID"),
        Column("label", "text", False, "Keyword pattern"),
        Column("category_name", "text", False, "Category"),
        Column("contents", "bigint", False, "Matching contents"),
        Column("occurrences", "bigint", False, "Occurrences in matched contents"),
        Column("unknown_count_rows", "bigint", False, "Word records without a count"),
        Column("corpus_contents", "bigint", False, "Contents in the matched set"),
    ),
    sql=(_KEYWORD_INTELLIGENCE_FROM
         + "SELECT rpt_k.id AS keyword_id, rpt_lbl.label, rpt_cw.word AS category_name,"  # nosec B608 # module constants only (_KEYWORD_INTELLIGENCE_FROM); values are bound parameters
         " COALESCE(rpt_h.contents, 0)::bigint AS contents,"
         " COALESCE(rpt_h.occurrences, 0)::bigint AS occurrences,"
         " COALESCE(rpt_h.unknown_count_rows, 0)::bigint AS unknown_count_rows,"
         " rpt_corpus.n::bigint AS corpus_contents"
         " FROM keywords rpt_k"
         " JOIN categorys rpt_c ON rpt_c.id = rpt_k.category_id"
         " JOIN words rpt_cw ON rpt_cw.id = rpt_c.word_id"
         " CROSS JOIN rpt_corpus"
         " LEFT JOIN rpt_hits rpt_h ON rpt_h.keyword_id = rpt_k.id"
         " LEFT JOIN LATERAL ("
         "  SELECT COALESCE(string_agg(rpt_w.word, ' ' ORDER BY rpt_t.ord), '') AS label"
         "  FROM jsonb_array_elements_text(convert_from(rpt_k.keyword, 'UTF8')::jsonb)"
         "   WITH ORDINALITY AS rpt_t(word_id, ord)"
         "  JOIN words rpt_w ON rpt_w.id = rpt_t.word_id::int"
         " ) rpt_lbl ON TRUE"
         " ORDER BY rpt_h.contents DESC NULLS LAST, rpt_lbl.label, rpt_h.keyword_id"
         " LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
)

# ---------------------------------------------------------------------------
# Category Analysis (step 17)
#
# One row per category: which of the *contents* the criteria matched contain
# at least one of the category's words. The base is hash-level (the same
# words_hashs x words_categorys join the criteria compiler uses for its
# category clause), and contents are counted distinctly - two occurrences of
# one content are one content. Categories overlap by design (one word may
# belong to several categories), so shares are per category against the same
# matched set and their sum may exceed 100%; the share is NULL when the
# matched set is empty - an unknown share, never zero.
# ---------------------------------------------------------------------------

_CATEGORY_ANALYSIS_FROM = (
    "WITH rpt_base AS ("
    " SELECT DISTINCT hc.hash_id AS hash_id FROM " + CANONICAL_FROM + " WHERE {where}"
    "), rpt_corpus AS ("
    " SELECT COUNT(*) AS n FROM rpt_base"
    "), rpt_hits AS ("
    " SELECT rpt_wc.category_id,"
    " COUNT(DISTINCT rpt_wh.hash_id) AS contents,"
    " COALESCE(SUM(rpt_wh.word_count), 0) AS occurrences,"
    " COUNT(*) FILTER (WHERE rpt_wh.word_count IS NULL) AS unknown_count_rows"
    " FROM words_hashs rpt_wh"
    " JOIN rpt_base ON rpt_base.hash_id = rpt_wh.hash_id"
    " JOIN words_categorys rpt_wc ON rpt_wc.word_id = rpt_wh.word_id"
    " GROUP BY rpt_wc.category_id"
    ") "
)

CATEGORY_ANALYSIS_SUMMARY_V1 = Dataset(
    dataset_id="category_analysis.summary",
    version=1,
    description=(
        "Every category with: its number of member words and keywords; the "
        "distinct contents matched by the criteria that contain at least one "
        "member word; the total of the stored per-content counts of those "
        "words (word_count IS NULL rows are excluded from the sum and "
        "carried in unknown_count_rows, never read as zero); the size of the "
        "matched set; and the category's share of it (contents / matched "
        "set), NULL when the matched set is empty. Categories overlap when a "
        "word belongs to several categories, so contents are counted in "
        "every category that applies and shares are not a partition. "
        "Capped: at most row_limit categories, most widespread first; "
        "overflow is detected (row_limit + 1 fetched) and recorded as "
        "truncation, never hidden."),
    unit="category",
    semantics="capped",
    row_limit=1000,
    columns=(
        Column("category_id", "integer", False, "Category ID"),
        Column("category_name", "text", False, "Category"),
        Column("member_words", "bigint", False, "Words in category"),
        Column("keywords", "bigint", False, "Keywords in category"),
        Column("contents", "bigint", False, "Matching contents"),
        Column("occurrences", "bigint", False, "Occurrences in matched contents"),
        Column("unknown_count_rows", "bigint", False, "Word records without a count"),
        Column("corpus_contents", "bigint", False, "Contents in the matched set"),
        Column("content_share", "numeric", True, "Share of the matched set"),
    ),
    sql=(_CATEGORY_ANALYSIS_FROM
         + "SELECT rpt_c.id AS category_id, rpt_w.word AS category_name,"  # nosec B608 # module constants only (_CATEGORY_ANALYSIS_FROM); values are bound parameters
         " (SELECT COUNT(*) FROM words_categorys rpt_wc2"
         "  WHERE rpt_wc2.category_id = rpt_c.id)::bigint AS member_words,"
         " (SELECT COUNT(*) FROM keywords rpt_k2"
         "  WHERE rpt_k2.category_id = rpt_c.id)::bigint AS keywords,"
         " COALESCE(rpt_h.contents, 0)::bigint AS contents,"
         " COALESCE(rpt_h.occurrences, 0)::bigint AS occurrences,"
         " COALESCE(rpt_h.unknown_count_rows, 0)::bigint AS unknown_count_rows,"
         " rpt_corpus.n::bigint AS corpus_contents,"
         " CASE WHEN rpt_corpus.n > 0"
         "  THEN COALESCE(rpt_h.contents, 0)::float8 / rpt_corpus.n"
         "  ELSE NULL END AS content_share"
         " FROM categorys rpt_c"
         " JOIN words rpt_w ON rpt_w.id = rpt_c.word_id"
         " CROSS JOIN rpt_corpus"
         " LEFT JOIN rpt_hits rpt_h ON rpt_h.category_id = rpt_c.id"
         " ORDER BY COALESCE(rpt_h.contents, 0) DESC, rpt_w.word, rpt_c.id"
         " LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
)

# ---------------------------------------------------------------------------
# Horizon (step 17)
#
# One row per resolved temporal signal (date_reference / relative_reference
# from the temporal detector) whose content is matched by the criteria,
# bucketed by how soon its event date arrives. The bucket expression and the
# reference-date join are imported from core.detection.horizon - the same
# definition the Signal Explorer runs; nothing here re-derives the buckets.
# The reference date is a declared report parameter (as_of), so a stored run
# is reproducible: the same snapshot and the same as_of give the same
# buckets. The representative document of a signal is the lowest path id
# among the criteria-matched occurrences of its content (signals are
# content-derived since m0011; the evidence sentence locates the text in any
# occurrence). Undated signals are the explorer's separate ``undated`` count,
# never a bucket, and are not listed here; signals whose day has passed
# without being future-oriented are the explorer's ``past`` bucket and are
# not part of the horizon proper.
# ---------------------------------------------------------------------------

_HORIZON_TYPES_SQL = "(" + ", ".join(f"'{t}'" for t in _horizon.HORIZON_SIGNAL_TYPES) + ")"

HORIZON_SIGNALS_V1 = Dataset(
    dataset_id="horizon.signals",
    version=1,
    description=(
        "Resolved temporal signals (date and relative references from the "
        "temporal detector) whose content matches the criteria, bucketed "
        "against the declared reference date: overdue (an end date before "
        "the reference date in a future-oriented sentence), week, month, "
        "quarter, later - the Signal Explorer's own buckets. One row per "
        "signal; its document columns are the first (lowest id) matching "
        "occurrence of the signal's content. Ordered by event date, soonest "
        "first. Capped: at most row_limit signals; overflow is detected "
        "(row_limit + 1 fetched) and recorded as truncation, never hidden. "
        "Confidence, method and the evidence sentence are NULL for signals "
        "stored before provenance was recorded (unknown, not zero); undated "
        "signals are out of scope here."),
    unit="signal",
    semantics="capped",
    row_limit=5000,
    columns=(
        Column("signal_id", "bigint", False, "Signal ID"),
        Column("path_id", "integer", False, "File ID"),
        Column("file_name", "text", False, "File name"),
        Column("event_date", "date", False, "Event date"),
        Column("end_date", "date", True, "End date"),
        Column("bucket", "text", False, "Horizon bucket"),
        Column("signal_type", "text", False, "Signal type"),
        Column("value", "text", False, "Detected value"),
        Column("language", "text", True, "Language"),
        Column("calendar", "text", True, "Calendar"),
        Column("confidence", "text", True, "Confidence"),
        Column("method", "text", True, "Method"),
        Column("detector_ver", "text", False, "Detector version"),
        Column("evidence_sentence", "text", True, "Evidence sentence"),
    ),
    sql=(
        "WITH rpt_base AS ("
        " SELECT DISTINCT hc.hash_id AS hash_id FROM " + CANONICAL_FROM + " WHERE {where}"
        ") "
        "SELECT s.id AS signal_id, rpt_doc.path_id, rpt_doc.file_name,"  # nosec B608 # module constants only (CANONICAL_FROM, core.detection.horizon bucket SQL, detector constants); values are bound parameters
        " s.date_from AS event_date, s.date_to AS end_date,"
        f" {_horizon.bucket_sql()} AS bucket,"
        " s.signal_type, s.value, s.language, s.calendar,"
        " s.confidence, s.method, s.detector_ver, s.evidence_sentence"
        " FROM rpt_base"
        " JOIN content_signals s ON s.hash_id = rpt_base.hash_id"
        " CROSS JOIN LATERAL ("
        "  SELECT p.id AS path_id, p.file_name"
        "  FROM paths p JOIN hash_contexts rpt_hc2 ON rpt_hc2.id = p.context_id"
        "  WHERE rpt_hc2.hash_id = rpt_base.hash_id"
        "  ORDER BY p.id LIMIT 1"
        " ) rpt_doc"
        f" {_horizon.REF_SQL}"
        f" WHERE s.detector = '{_temporal.DETECTOR_NAME}'"
        f" AND s.signal_type IN {_HORIZON_TYPES_SQL}"
        " AND s.date_from IS NOT NULL"
        f" AND {_horizon.bucket_sql()} <> 'past'"
        " ORDER BY s.date_from ASC, s.id ASC"
        " LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, "as_of", TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria", "as_of"),
    criteria_param="criteria",
)

# ---------------------------------------------------------------------------
# Entity / Place (step 17)
#
# One row per gazetteer place detected in the contents the criteria matched.
# The candidates live in content_signal_places (one candidate = identified;
# several = ambiguous, kept and never picked), so ambiguity is preserved
# structurally: a place's identified counts come only from signals resolved
# to it, and its ambiguous counts come from signals where it is merely a
# candidate - neither is folded into the other, and nothing is silently
# selected. place_mention signals that resolved to no place at all
# (resolution 'unresolved') name no place and are out of per-place
# attribution by declaration. Identified signals whose confidence was never
# recorded (pre-m0018 rows) are counted in unknown_confidence_occurrences -
# unknown, never zero. Retired gazetteer places are marked, not hidden.
# The representative document is the lowest path id among the matched
# occurrences of the place's signals (signals are content-derived).
# ---------------------------------------------------------------------------

_ENTITY_PLACE_FROM = (
    "WITH rpt_base AS ("
    " SELECT DISTINCT hc.hash_id AS hash_id FROM " + CANONICAL_FROM + " WHERE {where}"
    "), rpt_hits AS ("
    " SELECT rpt_csp.place_id,"
    " COUNT(DISTINCT s.hash_id) FILTER (WHERE s.resolution = 'identified')"
    "   AS ident_contents,"
    " COUNT(*) FILTER (WHERE s.resolution = 'identified') AS ident_occ,"
    " COUNT(DISTINCT s.hash_id) FILTER (WHERE s.resolution = 'ambiguous')"
    "   AS amb_contents,"
    " COUNT(*) FILTER (WHERE s.resolution = 'ambiguous') AS amb_occ,"
    " COUNT(*) FILTER (WHERE s.resolution = 'identified'"
    "                  AND s.confidence IS NULL) AS unk_conf"
    " FROM content_signals s"
    " JOIN content_signal_places rpt_csp ON rpt_csp.signal_id = s.id"
    " JOIN rpt_base ON rpt_base.hash_id = s.hash_id"
    " WHERE s.detector = '" + _places.DETECTOR_NAME + "'"
    "   AND s.signal_type = '" + _places.SIGNAL_PLACE + "'"
    " GROUP BY rpt_csp.place_id"
    ") "
)

ENTITY_PLACE_MENTIONS_V1 = Dataset(
    dataset_id="entity_place.mentions",
    version=1,
    description=(
        "Every gazetteer place detected in the contents matched by the "
        "criteria, with its identified mentions (signals resolved to this "
        "place) and its ambiguous mentions (signals where this place is one "
        "of the candidates - kept distinct, never merged with identified "
        "and never silently selected), per distinct content and per signal "
        "row. Mentions whose confidence was never recorded are counted "
        "separately (unknown, not zero). Unresolved mentions that name no "
        "place are out of per-place attribution. Retired places carry "
        "their flag. The representative document is the lowest matched path "
        "id of the place's signals. Capped: at most row_limit places, most "
        "identified first; overflow is detected (row_limit + 1 fetched) and "
        "recorded as truncation, never hidden."),
    unit="place",
    semantics="capped",
    row_limit=2000,
    columns=(
        Column("place_id", "integer", False, "Place ID"),
        Column("place_label", "text", False, "Place"),
        Column("feature_type", "text", False, "Feature type"),
        Column("country_codes", "text", False, "Countries"),
        Column("retired", "boolean", False, "Retired"),
        Column("identified_contents", "bigint", False, "Contents (identified)"),
        Column("identified_occurrences", "bigint", False, "Mentions (identified)"),
        Column("ambiguous_contents", "bigint", False, "Contents (ambiguous)"),
        Column("ambiguous_occurrences", "bigint", False, "Mentions (ambiguous)"),
        Column("unknown_confidence_occurrences", "bigint", False,
               "Mentions without confidence"),
        Column("first_path_id", "integer", False, "File ID"),
        Column("first_file_name", "text", False, "File name"),
    ),
    sql=(_ENTITY_PLACE_FROM
         + "SELECT rpt_g.id AS place_id, rpt_g.label AS place_label,"  # nosec B608 # module constants only (_ENTITY_PLACE_FROM, detector constants); values are bound parameters
         " rpt_g.feature_type,"
         " array_to_string(rpt_g.country_codes, ',') AS country_codes,"
         " rpt_g.retired,"
         " rpt_h.ident_contents::bigint AS identified_contents,"
         " rpt_h.ident_occ::bigint AS identified_occurrences,"
         " rpt_h.amb_contents::bigint AS ambiguous_contents,"
         " rpt_h.amb_occ::bigint AS ambiguous_occurrences,"
         " rpt_h.unk_conf::bigint AS unknown_confidence_occurrences,"
         " rpt_doc.path_id AS first_path_id,"
         " rpt_doc.file_name AS first_file_name"
         " FROM rpt_hits rpt_h"
         " JOIN geo_places rpt_g ON rpt_g.id = rpt_h.place_id"
         " JOIN LATERAL ("
         "  SELECT p.id AS path_id, p.file_name"
         "  FROM paths p"
         "  JOIN hash_contexts rpt_hc2 ON rpt_hc2.id = p.context_id"
         "  WHERE rpt_hc2.hash_id IN ("
         "   SELECT s2.hash_id FROM content_signals s2"
         "   JOIN content_signal_places rpt_csp2 ON rpt_csp2.signal_id = s2.id"
         "   WHERE rpt_csp2.place_id = rpt_h.place_id"
         "     AND s2.hash_id IN (SELECT hash_id FROM rpt_base))"
         "  ORDER BY p.id LIMIT 1"
         " ) rpt_doc ON TRUE"
         " ORDER BY rpt_h.ident_contents DESC, rpt_h.ident_occ DESC,"
         " rpt_g.label, rpt_g.id"
         " LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
)


# ---------------------------------------------------------------------------
# Relationship (step 17)
#
# The context identity is the triple (hash_id, source_id, side_id), unique
# in hash_contexts. One row per context among the matched contents: how many
# matched file occurrences it carries (repeated identical triples are the
# same context, not new relationships), and how many *other* contexts and
# sources the same content appears in within the matched set - the
# cross-posting relationship. Distinct contexts are counted, never raw path
# rows. The representative document is the lowest matched path id of the
# context. Capped: at most row_limit contexts, most cross-posted first;
# overflow is detected (row_limit + 1 fetched) and recorded as truncation,
# never hidden.
# ---------------------------------------------------------------------------

_RELATIONSHIP_FROM = (
    "WITH rpt_paths AS ("
    " SELECT p.id AS path_id, p.context_id, hc.hash_id AS hash_id"
    f" FROM {CANONICAL_FROM} WHERE {{where}}"
    "), rpt_hashes AS ("
    " SELECT DISTINCT hash_id FROM rpt_paths"
    "), rpt_sib AS ("
    " SELECT hc.hash_id, COUNT(DISTINCT hc.id) AS ctx_all"
    " FROM hash_contexts hc JOIN rpt_hashes ON rpt_hashes.hash_id = hc.hash_id"
    " GROUP BY hc.hash_id"
    ") "
)

RELATIONSHIP_CONTEXTS_V1 = Dataset(
    dataset_id="relationship.contexts",
    version=1,
    description=(
        "Every context (hash_id, source_id, side_id) of the contents the "
        "criteria matched, with its matched file occurrences, and the "
        "content's cross-posting relationship inside the matched set: the "
        "number of other contexts carrying the same content and the number "
        "of other sources among them. Contexts are counted distinctly - "
        "repeated identical triples are one context, different sources or "
        "sides are different contexts. The representative document is the "
        "lowest matched path id of the context. Capped: at most row_limit "
        "contexts, most cross-posted first; overflow is detected "
        "(row_limit + 1 fetched) and recorded as truncation, never hidden."),
    unit="context",
    semantics="capped",
    row_limit=5000,
    columns=(
        Column("context_id", "integer", False, "Context ID"),
        Column("hash_id", "integer", False, "Content ID"),
        Column("source_name", "text", False, "Source"),
        Column("side_name", "text", False, "Side"),
        Column("paths", "bigint", False, "Matched paths"),
        Column("sibling_contexts", "bigint", False, "Sibling contexts"),
        Column("sibling_sources", "bigint", False, "Sibling sources"),
        Column("first_path_id", "integer", False, "File ID"),
        Column("first_file_name", "text", False, "File name"),
    ),
    sql=(_RELATIONSHIP_FROM
         + "SELECT hc.id AS context_id, hc.hash_id,"  # nosec B608 # module constants only (_RELATIONSHIP_FROM); values are bound parameters
         " rpt_src.name AS source_name, rpt_side.name AS side_name,"
         " (SELECT COUNT(*) FROM rpt_paths rp WHERE rp.context_id = hc.id"
         " )::bigint AS paths,"
         " (rpt_sib.ctx_all - 1)::bigint AS sibling_contexts,"
         " (SELECT COUNT(DISTINCT hc2.source_id) FROM hash_contexts hc2"
         "  JOIN rpt_hashes ON rpt_hashes.hash_id = hc2.hash_id"
         "  WHERE hc2.hash_id = hc.hash_id"
         "    AND hc2.source_id <> hc.source_id)::bigint AS sibling_sources,"
         " rpt_doc.path_id AS first_path_id,"
         " rpt_doc.file_name AS first_file_name"
         " FROM rpt_hashes"
         " JOIN hash_contexts hc ON hc.hash_id = rpt_hashes.hash_id"
         " JOIN rpt_sib ON rpt_sib.hash_id = hc.hash_id"
         " LEFT JOIN sources rpt_src ON rpt_src.id = hc.source_id"
         " LEFT JOIN sides rpt_side ON rpt_side.id = hc.side_id"
         " JOIN LATERAL ("
         "  SELECT rp2.path_id, p2.file_name FROM rpt_paths rp2"
         "  JOIN paths p2 ON p2.id = rp2.path_id"
         "  WHERE rp2.context_id = hc.id ORDER BY rp2.path_id LIMIT 1"
         " ) rpt_doc ON TRUE"
         " ORDER BY (rpt_sib.ctx_all - 1) DESC,"
         " (SELECT COUNT(DISTINCT hc2.source_id) FROM hash_contexts hc2"
         "  JOIN rpt_hashes ON rpt_hashes.hash_id = hc2.hash_id"
         "  WHERE hc2.hash_id = hc.hash_id"
         "    AND hc2.source_id <> hc.source_id) DESC,"
         " rpt_src.name, rpt_side.name, hc.hash_id"
         " LIMIT %s"),
    sql_params=(TOKEN_CRITERIA, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
)

# --- Latest Entries report (step 17) ----------------------------------------
# Read per viewer: the SQL joins this reader's baseline row (bound from the
# authenticated scope, keyed to this view's fingerprint) and classifies every
# row against it *inside the run's snapshot*. The three states the Latest
# report must distinguish are exactly: recently created (a property of the
# data), previously seen (at or before this reader's recorded progress), and
# new since the last view (beyond it - everything, when the reader has no
# baseline yet: a first view is new by definition and is never read as
# "zero seen"). "Never viewed" is an absent baseline row, never a zero id.
LATEST_ENTRIES_V1 = Dataset(
    dataset_id="latest.entries",
    version=1,
    description=(
        "The matched file occurrences, newest first, read per viewer: each "
        "row states whether this reader had already seen it (previously "
        "seen / new since the last view, against the reader's recorded "
        "progress for this view) and whether it was ingested within the "
        "last 30 days (recently created, a property of the data reported "
        "alongside reading progress, not instead of it). Capped: at most "
        "row_limit rows; overflow is detected (row_limit + 1 fetched) and "
        "recorded as truncation, never hidden. Running the report advances "
        "the reader's baseline to the furthest row actually shown - "
        "monotonically, never backwards."),
    unit="path",
    semantics="capped",
    row_limit=5000,
    columns=(
        # paths.context_id is NOT NULL with a foreign key to hash_contexts,
        # whose source_id/side_id are NOT NULL foreign keys; report_baselines
        # is LEFT JOINed - an absent row is "never viewed", not a zero.
        Column("path_id", "integer", False, "File ID"),
        Column("file_name", "text", False, "File name"),
        Column("file_type", "text", False, "File type"),
        Column("file_size", "bigint", False, "File size"),
        Column("file_status", "text", False, "Status"),
        Column("file_date", "date", False, "File date"),
        Column("date_creation", "date", False, "Created"),
        Column("source_name", "text", False, "Source"),
        Column("side_name", "text", False, "Side"),
        Column("view_state", "text", False, "View state"),
        Column("recently_created", "boolean", False, "Recently created"),
    ),
    sql=(
        "WITH rpt_base AS ("
        f"    SELECT DISTINCT p.id AS path_id FROM {CANONICAL_FROM} "
        "    WHERE {where} "
        ")"
        " SELECT p.id AS path_id, p.file_name, p.file_type, p.file_size,"
        " p.file_status, p.file_date, p.date_creation,"
        " rpt_src.name AS source_name, rpt_side.name AS side_name,"
        " CASE WHEN rpt_bl.last_max_id IS NULL THEN 'new_since_last_view'"
        "      WHEN p.id <= rpt_bl.last_max_id THEN 'previously_seen'"
        "      ELSE 'new_since_last_view' END AS view_state,"
        " (p.date_creation >= CURRENT_DATE - 30) AS recently_created"
        " FROM rpt_base"
        " JOIN paths p ON p.id = rpt_base.path_id"
        " LEFT JOIN hash_contexts rpt_hc ON rpt_hc.id = p.context_id"
        " LEFT JOIN sources rpt_src ON rpt_src.id = rpt_hc.source_id"
        " LEFT JOIN sides rpt_side ON rpt_side.id = rpt_hc.side_id"
        " LEFT JOIN report_baselines rpt_bl"
        "     ON rpt_bl.user_id = %s AND rpt_bl.criteria_hash = %s"
        " ORDER BY p.date_creation DESC, p.id DESC"
        " LIMIT %s"),  # nosec B608 # module constants only (CANONICAL_FROM); values are bound parameters
    sql_params=(TOKEN_CRITERIA, TOKEN_VIEWER_ID, TOKEN_VIEWER_KEY, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
    progress_column="path_id",
)

# --- Change Report (step 17) ------------------------------------------------
# What changed in a view since this reader last saw it. *Added* is measured
# from paths.date_creation (the ingestion event); *modified* and *removed*
# are read from the path_revisions log (migration 0029) - the only place
# previous values exist. All three datasets are viewer datasets over the
# same view key: the watermark is this reader's last_seen_at (a time
# watermark - a path has no "modification id"), pinned to UTC for
# reproducibility; a reader without a baseline has seen nothing, so every
# state shows in full - never read as a zero. An empty result is a measured
# "nothing changed", not an unknown.
_CHANGE_FROM = (
    "FROM path_revisions rpt_rev "
    "JOIN paths p ON p.id = rpt_rev.path_id "
    "LEFT JOIN hash_contexts hc ON p.context_id = hc.id "
    "LEFT JOIN hashs h ON hc.hash_id = h.id "
    "LEFT JOIN report_baselines rpt_bl"
    "     ON rpt_bl.user_id = %s AND rpt_bl.criteria_hash = %s "
    "LEFT JOIN sources rpt_src ON rpt_src.id = hc.source_id "
    "LEFT JOIN sides rpt_side ON rpt_side.id = hc.side_id "
    "WHERE rpt_rev.change_kind = '{kind}' "
    "AND rpt_rev.changed_at > COALESCE(rpt_bl.last_seen_at,"
    " to_timestamp(0)) "
    "AND {where} "
    "ORDER BY rpt_rev.changed_at DESC, rpt_rev.id DESC "
    "LIMIT %s"
)

_CHANGE_COLUMNS = (
    Column("path_id", "integer", False, "File ID"),
    Column("file_name", "text", False, "File name"),
    Column("file_type", "text", False, "File type"),
    Column("file_size", "bigint", False, "File size"),
    Column("source_name", "text", False, "Source"),
    Column("side_name", "text", False, "Side"),
    Column("change_kind", "text", False, "Change kind"),
    Column("detected_at", "timestamptz", False, "Detected at"),
    # A revision's JSON carries whichever fields that operation changed;
    # a future operation may record no file_name, so these stay nullable
    # by declaration - unknown, never an empty string.
    Column("previous_value", "text", True, "Previous value"),
    Column("current_value", "text", True, "Current value"),
)

CHANGE_ADDED_V1 = Dataset(
    dataset_id="change.added",
    version=1,
    description=(
        "File occurrences of the matched set ingested since this reader "
        "last viewed this view (everything, when they never have - a first "
        "view shows the full matched set as added, not as a zero). One row "
        "per occurrence, newest first. Capped: at most row_limit rows; "
        "overflow is detected (row_limit + 1 fetched) and recorded as "
        "truncation, never hidden. Running the report advances the "
        "reader's watermark."),
    unit="path",
    semantics="capped",
    row_limit=5000,
    columns=_CHANGE_COLUMNS,
    sql=(
        "WITH rpt_base AS ("
        f"    SELECT DISTINCT p.id AS path_id FROM {CANONICAL_FROM} "
        "    WHERE {where} "
        ") "
        " SELECT p.id AS path_id, p.file_name, p.file_type, p.file_size,"
        " rpt_src.name AS source_name, rpt_side.name AS side_name,"
        " 'added' AS change_kind,"
        " p.date_creation::timestamptz AS detected_at,"
        " NULL::text AS previous_value, p.file_name AS current_value"
        " FROM rpt_base"
        " JOIN paths p ON p.id = rpt_base.path_id"
        " LEFT JOIN hash_contexts rpt_hc ON rpt_hc.id = p.context_id"
        " LEFT JOIN sources rpt_src ON rpt_src.id = rpt_hc.source_id"
        " LEFT JOIN sides rpt_side ON rpt_side.id = rpt_hc.side_id"
        " LEFT JOIN report_baselines rpt_bl"
        "     ON rpt_bl.user_id = %s AND rpt_bl.criteria_hash = %s"
        " WHERE rpt_bl.last_seen_at IS NULL"
        "    OR p.date_creation >="
        " (rpt_bl.last_seen_at AT TIME ZONE 'UTC')::date"
        " ORDER BY p.date_creation DESC, p.id DESC"
        " LIMIT %s"),  # nosec B608 # module constants only (CANONICAL_FROM); values are bound parameters
    sql_params=(TOKEN_CRITERIA, TOKEN_VIEWER_ID, TOKEN_VIEWER_KEY, TOKEN_LIMIT),
    roles=_READERS,
    parameters=("criteria",),
    criteria_param="criteria",
    progress_column="path_id",
)


def _change_revision_dataset(dataset_id, kind, description):
    return Dataset(
        dataset_id=dataset_id,
        version=1,
        description=description,
        unit="path",
        semantics="capped",
        row_limit=5000,
        columns=(Column("revision_id", "bigint", False, "Revision"),)
                + _CHANGE_COLUMNS,
        sql=("SELECT rpt_rev.id AS revision_id, p.id AS path_id,"
             " p.file_name, p.file_type, p.file_size,"
             " rpt_src.name AS source_name, rpt_side.name AS side_name,"
             " rpt_rev.change_kind AS change_kind,"
             " rpt_rev.changed_at AS detected_at,"
             " rpt_rev.old_values->>'file_name' AS previous_value,"
             " rpt_rev.new_values->>'file_name' AS current_value "
             + _CHANGE_FROM.replace("{kind}", kind)),  # nosec B608 # fixed fragments, kind from the module constant pair; values are bound parameters
        sql_params=(TOKEN_VIEWER_ID, TOKEN_VIEWER_KEY, TOKEN_CRITERIA,
                    TOKEN_LIMIT),
        roles=_READERS,
        parameters=("criteria",),
        criteria_param="criteria",
        progress_column="path_id",
    )


CHANGE_MODIFIED_V1 = _change_revision_dataset(
    "change.modified", "modified",
    "Recorded modifications of the matched set's occurrences since this "
    "reader last viewed this view - one row per revision event from the "
    "append-only path_revisions log, with the detection time, the previous "
    "values and the values after the change. Everything the log holds, "
    "when the reader never has. Capped: at most row_limit rows, newest "
    "event first; overflow is detected and recorded as truncation, never "
    "hidden. Running the report advances the reader's watermark.")

CHANGE_REMOVED_V1 = _change_revision_dataset(
    "change.removed", "removed",
    "Recorded removals of the matched set's occurrences since this reader "
    "last viewed this view, from the same append-only log: the previous "
    "values as they were, the detection time, and no current value - a "
    "removed row has none, which is not the same as an empty string. No "
    "removal operation exists yet, so an empty result is the honest "
    "measurement today. Capped: at most row_limit rows, newest event "
    "first; overflow is detected and recorded as truncation, never hidden.")

DATASETS: Tuple[Dataset, ...] = (
    SEARCH_RESULTS_MATCHES_V1,
    SEARCH_RESULTS_COUNT_V1,
    TERM_KEYNESS_RANKED_V1,
    TERM_KEYNESS_TOTALS_V1,
    KEYWORD_INTELLIGENCE_MATCHES_V1,
    CATEGORY_ANALYSIS_SUMMARY_V1,
    HORIZON_SIGNALS_V1,
    ENTITY_PLACE_MENTIONS_V1,
    RELATIONSHIP_CONTEXTS_V1,
    LATEST_ENTRIES_V1,
    CHANGE_ADDED_V1,
    CHANGE_MODIFIED_V1,
    CHANGE_REMOVED_V1,
)
