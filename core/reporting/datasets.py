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

from .model import TOKEN_CRITERIA, TOKEN_LIMIT, Column, Dataset

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

DATASETS: Tuple[Dataset, ...] = (
    SEARCH_RESULTS_MATCHES_V1,
    SEARCH_RESULTS_COUNT_V1,
)
