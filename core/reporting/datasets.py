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
from core.security.service import ALL_ROLES

from .model import TOKEN_CRITERIA, TOKEN_LIMIT, TOKEN_SCOPE, Column, Dataset

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
        "SELECT p.id AS path_id, p.file_name, p.file_type, p.file_date, "
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
    sql=(f"SELECT COUNT(DISTINCT p.id) AS matched FROM {CANONICAL_FROM} "
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
    "WITH rpt_target AS ("
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
         + "SELECT rpt_w.word AS term, rpt_g.a::bigint AS target_freq,"
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
         + "SELECT rpt_n.c::bigint AS target_tokens, rpt_n.d::bigint AS reference_tokens,"
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

DATASETS: Tuple[Dataset, ...] = (
    SEARCH_RESULTS_MATCHES_V1,
    SEARCH_RESULTS_COUNT_V1,
    TERM_KEYNESS_RANKED_V1,
    TERM_KEYNESS_TOTALS_V1,
)
