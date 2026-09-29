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

KEYWORD_INTELLIGENCE_V1 = ReportDefinition(
    report_id="keyword_intelligence",
    version=1,
    title="Keyword Intelligence Report",
    description="Every keyword with the number of matched contents that "
                "contain it, how often it occurs in them, and the category "
                "it belongs to. The matched set is the contents your "
                "criteria select; keywords come from the whole collection.",
    help_topic="reports/keyword-intelligence",
    unit="keyword",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
    ),
    datasets=("keyword_intelligence.matches@1",),
)

CATEGORY_ANALYSIS_V1 = ReportDefinition(
    report_id="category_analysis",
    version=1,
    title="Category Analysis Report",
    description="Every category with the contents in the matched set that "
                "contain any of its words, the occurrences counted, and the "
                "category's share of the matched set. Categories overlap "
                "when a word belongs to several categories, so shares can "
                "sum to more than 100%.",
    help_topic="reports/category-analysis",
    unit="category",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
    ),
    datasets=("category_analysis.summary@1",),
)

HORIZON_V1 = ReportDefinition(
    report_id="horizon",
    version=1,
    title="Horizon Report",
    description="The detected dates and relative references in the matched "
                "documents, bucketed against a reference date you choose: "
                "overdue, this week, this month, this quarter, and later. "
                "The same buckets as the Horizon page; the reference date is "
                "recorded with the run, so it can be reproduced.",
    help_topic="reports/horizon",
    unit="signal",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
        Parameter("as_of", "date", "Reference date", required=True),
    ),
    datasets=("horizon.signals@1",),
)

ENTITY_PLACE_V1 = ReportDefinition(
    report_id="entity_place",
    version=1,
    title="Entity & Place Report",
    description="Every gazetteer place detected in the matched documents, "
                "with its identified mentions and - kept separate - its "
                "ambiguous mentions, where the text could refer to this "
                "place or another. Ambiguity is never resolved for you: a "
                "place's identified counts come only from mentions the "
                "detector resolved to it.",
    help_topic="reports/entity-place",
    unit="place",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
    ),
    datasets=("entity_place.mentions@1",),
)

RELATIONSHIP_V1 = ReportDefinition(
    report_id="relationship",
    version=1,
    title="Content Relationships Report",
    description="Where the same content appears across your matched "
                "documents: each source-and-side context with its file "
                "occurrences, and how many other contexts and sources carry "
                "the identical content. Copies within one context are one "
                "context; a different source or side is a different "
                "context.",
    help_topic="reports/relationship",
    unit="context",
    roles=tuple(ALL_ROLES),
    parameters=(
        Parameter("criteria", "criteria", "Search criteria", required=True),
    ),
    datasets=("relationship.contexts@1",),
)

REPORTS: Tuple[ReportDefinition, ...] = (
    SEARCH_RESULTS_V1,
    TERM_KEYNESS_V1,
    TERM_KEYNESS_V2,
    KEYWORD_INTELLIGENCE_V1,
    CATEGORY_ANALYSIS_V1,
    HORIZON_V1,
    ENTITY_PLACE_V1,
    RELATIONSHIP_V1,
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
    HelpTopic(
        topic="reports/keyword-intelligence",
        title="About the Keyword Intelligence Report",
        summary="Shows each keyword's reach within the contents your "
                "criteria matched: how many contents contain it, how often "
                "it occurs, and its category. Keywords are listed even when "
                "nothing matches them.",
    ),
    HelpTopic(
        topic="reports/category-analysis",
        title="About the Category Analysis Report",
        summary="Shows, for each category, how many of the matched contents "
                "contain at least one of its words, how often those words "
                "occur, and the share of the matched set. Because categories "
                "can share words, contents are counted in every category "
                "that applies and shares are per category, not a partition.",
    ),
    HelpTopic(
        topic="reports/horizon",
        title="About the Horizon Report",
        summary="Lists the dates and relative references detected in the "
                "matched documents - when something is promised or expected "
                "to happen - grouped by how soon it arrives relative to the "
                "reference date you choose: overdue, this week, this month, "
                "this quarter, and later. Undated references and purely "
                "past mentions are not part of the horizon.",
    ),
    HelpTopic(
        topic="reports/relationship",
        title="About the Content Relationships Report",
        summary="Shows which contents appear in more than one source or "
                "side: every context of the matched contents with its file "
                "count, and the number of sibling contexts and sibling "
                "sources sharing the identical content. Repeated copies in "
                "the same source-and-side are one context, not several.",
    ),
    HelpTopic(
        topic="reports/entity-place",
        title="About the Entity & Place Report",
        summary="Lists every place from the gazetteer that the detector "
                "found in the matched documents, with how often it was "
                "identified and how often the mention stayed ambiguous - "
                "the text could mean this place or another one (Tripoli, "
                "Georgia). Ambiguous mentions are never counted as "
                "identified: you see both, side by side.",
    ),
)
