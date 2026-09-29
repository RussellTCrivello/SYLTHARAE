"""Registered report definitions and their help topics.

Only reports whose every dataset is registered and verified appear here. The
remaining catalog (Keyword, Category, Latest, Change, Horizon, Entity/Place,
Relationship, Scenario Outcome, Comprehensive) is added in later steps as its
datasets and analytics exist - see docs/implementation/EXECUTION_STATUS.md.
"""

from __future__ import annotations

import dataclasses
from typing import Tuple

from core.analytics.kinds import Analysis
from core.security.service import ALL_ROLES

from .model import HelpTopic, Parameter, ReportDefinition

SEARCH_RESULTS_V1 = ReportDefinition(
    report_id="search_results",
    version=1,
    title="Search Results Report",
    description="Every file that matches a saved set of search criteria, "
                "with the exact match count.",
    help_topic="reports/search-results",
    unit="path",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
    ),
    datasets=("search_results.matches@1", "search_results.count@1"),
)

TERM_KEYNESS_ANALYSIS_V1 = Analysis(
    analysis_id="term_keyness",
    version=1,
    kind="keyness",
    inputs={"ranked": "term_keyness.ranked@1", "totals": "term_keyness.totals@1"},
    description="Log-likelihood keyness of the selection's terms against the rest "
                "of the visible collection, with Log Ratio as effect size.",
    title="Distinctive terms",
)

TERM_KEYNESS_V1 = ReportDefinition(
    report_id="term_keyness",
    version=1,
    title="Distinctive Terms Report",
    description="Which words the selected documents use more (or less) often than "
                "the rest of the collection, with how sure that is.",
    help_topic="reports/term-keyness",
    unit="term",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
        Parameter("direction", "enum", "Direction", required=False, default="over",
                  choices=("over", "under"),
                  choice_labels=("Used more often in the selection",
                                 "Used less often in the selection")),
    ),
    datasets=("term_keyness.ranked@1", "term_keyness.totals@1"),
    analyses=("term_keyness@1",),
    # Superseded by v2 (NARR-01 plural-aware narrative); kept so v1 runs stay
    # interpretable and their narratives keep rendering.
    status="superseded",
)

#: Same datasets and measures as v1; only the narrative changes (keyness@2:
#: one count per sentence, plurals through the catalogs' Plural-Forms).
TERM_KEYNESS_ANALYSIS_V2 = dataclasses.replace(TERM_KEYNESS_ANALYSIS_V1, version=2,
                                               kind_version=2)

TERM_KEYNESS_V2 = dataclasses.replace(TERM_KEYNESS_V1, version=2,
                                      analyses=("term_keyness@2",), status="active")

REPORTS: Tuple[ReportDefinition, ...] = (
    SEARCH_RESULTS_V1,
    TERM_KEYNESS_V1,
    TERM_KEYNESS_V2,
)

ANALYSES: Tuple[Analysis, ...] = (
    TERM_KEYNESS_ANALYSIS_V1,
    TERM_KEYNESS_ANALYSIS_V2,
)

HELP_TOPICS: Tuple[HelpTopic, ...] = (
    HelpTopic(
        topic="reports/search-results",
        title="About the Search Results Report",
        summary="Lists the files that match your criteria at one moment, "
                "states how many matched in total and says so when the list "
                "is shortened.",
    ),
    HelpTopic(
        topic="reports/term-keyness",
        title="About the Distinctive Terms Report",
        summary="Compares how often each word occurs in the documents you "
                "selected with the rest of the collection you may see, and "
                "lists the words whose difference is statistically reliable.",
    ),
)
