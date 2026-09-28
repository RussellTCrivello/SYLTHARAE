"""Registered report definitions and their help topics.

Only reports whose every dataset is registered and verified appear here. The
remaining catalog (Keyword, Category, Latest, Change, Horizon, Entity/Place,
Relationship, Scenario Outcome, Comprehensive) is added in later steps as its
datasets and analytics exist - see docs/implementation/EXECUTION_STATUS.md.
"""

from __future__ import annotations

from typing import Tuple

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

REPORTS: Tuple[ReportDefinition, ...] = (
    SEARCH_RESULTS_V1,
)

HELP_TOPICS: Tuple[HelpTopic, ...] = (
    HelpTopic(
        topic="reports/search-results",
        title="About the Search Results Report",
        summary="Lists the files that match your criteria at one moment, "
                "states how many matched in total and says so when the list "
                "is shortened.",
    ),
)
