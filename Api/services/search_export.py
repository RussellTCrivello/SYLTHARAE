"""Authoritative search export: the server decides what is in the file.

The export used to take the browser's own array of rows and turn it into a
spreadsheet. That is a professional defect, not a convenience: the browser can
send a stale page, a truncated list, a filtered view the operator has since
changed, or rows that never existed, and the file that leaves the building
carries whatever it was handed. An export is evidence - it has to be produced
from the query, by the same code that answered the screen.

So this service takes a *query definition* - what was searched for, under which
filters, in which order - and re-runs it against the database. The rows in the
file are the rows the server found. The client's version of the results is
never consulted, and a request that tries to supply one is refused with an
explanation rather than quietly ignored.

Three scopes, because they are three different operations and the operator has
to say which one they mean:

``page``
    Exactly the page of results the reader is looking at.
``filtered``
    The whole result set of the query and filters, not just the visible page.
``dataset``
    Everything the filters would let through, with the query itself dropped.

A scope is never guessed from the payload: an export that quietly widens from
one page to the whole corpus, or narrows from the corpus to a page, is how
someone exports the wrong thing and does not notice until it matters.

Volume is handled honestly. Up to ``max_rows`` rows are produced in one file;
beyond that the export says so - in a response header and in the log - instead
of silently truncating an evidence file. Larger exports belong to the job
architecture, and this service reports the size that makes that decision
possible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: What the operator is exporting. Never inferred.
EXPORT_SCOPES: Tuple[str, ...] = ("page", "filtered", "dataset")

#: What the file will be.
EXPORT_FORMATS: Tuple[str, ...] = ("csv", "excel", "json")

#: Deliberate ceiling for one synchronous export. Above it the honest answer is
#: a persistent job, not a longer request: the size is reported so that
#: decision can be made by the person asking.
MAX_ROWS = 50_000

#: How many rows are fetched per round when the whole result set is wanted.
CHUNK = 1_000

#: Legacy positional shape returned by older search query paths. It is kept
#: only to map tuple rows into the public export schema below.
_RESULT_COLUMNS: Tuple[str, ...] = (
    "id", "file_name", "file_type", "file_size", "file_date",
    "file_status", "source_name", "source_id", "side_name", "side_id",
    "relevance_score", "categories", "snippet",
)

#: The public shape of an exported row, in order. CSV, Excel and JSON all use
#: this one definition; smart and analyst taxonomies stay separate.
COLUMNS: Tuple[str, ...] = (
    "id", "file_name", "file_type", "file_size", "file_date",
    "file_status", "source_name", "source_id", "side_name", "side_id",
    "relevance_score", "smart_categories", "analyst_categories", "snippet",
)

#: Query-definition fields this service accepts. Anything else in the payload
#: is refused, so a client cannot smuggle a result set through an unknown key
#: (`results`, `rows`, `data`, ...) and have it pass unnoticed.
_ACCEPTED = frozenset({
    "query", "export_scope", "analyst_scope", "format", "filename", "page", "per_page",
    "file_type", "source_id", "source_ids", "side_id", "side_ids",
    "category_id", "category_ids", "analyst_category_id",
    "analyst_category_ids", "status", "date_from", "date_to", "sort_by", "sort_order",
    "use_advanced", "use_fulltext", "use_bm25", "use_expansion", "use_fuzzy",
    "case_sensitive", "whole_word", "hide_duplicates",
})

#: Keys that mean "here are the rows, please write them out". Named explicitly
#: so the refusal can say what is wrong instead of "unexpected field".
_RESULT_PAYLOAD_KEYS = frozenset({"results", "rows", "records", "items", "data"})

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ExportRequestError(ValueError):
    """The request cannot be honoured, and the caller is told why."""


@dataclass(frozen=True)
class SearchExportRequest:
    """A validated description of what to export."""

    query: str = ""
    scope: str = "filtered"
    format: str = "csv"
    filename: str = ""
    page: int = 1
    per_page: int = 50
    file_types: Tuple[str, ...] = ()
    source_ids: Tuple[int, ...] = ()
    side_ids: Tuple[int, ...] = ()
    category_ids: Tuple[int, ...] = ()
    analyst_category_ids: Tuple[int, ...] = ()
    file_statuses: Optional[Tuple[str, ...]] = None
    analyst_scope: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    sort_by: str = "relevance"
    sort_order: str = "desc"
    use_advanced: bool = True
    use_fulltext: bool = True
    use_bm25: bool = True
    use_expansion: bool = True
    use_fuzzy: bool = True
    case_sensitive: bool = False
    whole_word: bool = False
    hide_duplicates: bool = False
    definition: Dict[str, Any] = field(default_factory=dict)

    @property
    def limit_offset(self) -> Tuple[int, int]:
        """How many rows to fetch in the first round, and from where."""
        if self.scope == "page":
            return self.per_page, (max(self.page, 1) - 1) * self.per_page
        return min(CHUNK, MAX_ROWS), 0

    @property
    def bounded_query(self) -> str:
        """``dataset`` means the query is dropped; the filters are kept.

        The operator asked for "everything my filters allow", which is the one
        scope where the search terms are deliberately not part of the export.
        """
        return "" if self.scope == "dataset" else self.query


@dataclass
class SearchExportResult:
    """What was exported, measured after the fact."""

    request: SearchExportRequest
    rows: List[Dict[str, Any]] = field(default_factory=list)
    total: int = 0
    truncated: bool = False
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @property
    def exported(self) -> int:
        return len(self.rows)

    @property
    def headers(self) -> Dict[str, str]:
        """What the response says about itself, so the client need not guess."""
        return {
            "X-Export-Scope": self.request.scope,
            "X-Export-Format": self.request.format,
            "X-Export-Rows": str(self.exported),
            "X-Export-Total": str(self.total),
            "X-Export-Truncated": "true" if self.truncated else "false",
            "X-Export-Cap": str(MAX_ROWS),
        }


def _as_ids(value: Any, what: str) -> Tuple[int, ...]:
    if value in (None, "", []):
        return ()
    values: Sequence[Any]
    if isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    out: List[int] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit():
            raise ExportRequestError(f"{what} must be numeric, got {item!r}")
        out.append(int(text))
    return tuple(sorted(set(out)))


def _as_strings(value: Any, what: str) -> Tuple[str, ...]:
    """Validate one or more bounded string filters without losing selections."""
    if value in (None, "", []):
        return ()
    values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    out: List[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 128:
            raise ExportRequestError(f"{what} values must be at most 128 characters.")
        if text not in out:
            out.append(text)
    return tuple(out)


def _as_statuses(payload: Dict[str, Any]) -> Optional[Tuple[str, ...]]:
    """Validate the UI's Read/Unread path status filter."""
    if "status" not in payload:
        return None
    raw = payload.get("status")
    values: Sequence[Any]
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    elif isinstance(raw, str) and "," in raw:
        values = raw.split(",")
    else:
        values = [raw]
    normalized: List[str] = []
    for item in values:
        text = str(item).strip().lower() if item is not None else ""
        if not text:
            continue
        if text == "none":
            if len(values) == 1:
                return ()
            raise ExportRequestError("status 'none' cannot be combined with another status.")
        if text not in ("read", "unread"):
            raise ExportRequestError("status must contain only 'Read', 'Unread', or 'none'.")
        status = text.capitalize()
        if status not in normalized:
            normalized.append(status)
    return tuple(normalized)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_date(value: Any, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not _ISO_DATE.match(text):
        raise ExportRequestError(f"{what} must be YYYY-MM-DD, got {value!r}")
    return text


def parse(payload: Optional[Dict[str, Any]]) -> SearchExportRequest:
    """Validate a request, or explain precisely why it cannot be honoured."""
    if not payload:
        raise ExportRequestError("A JSON body is required.")

    supplied = [key for key in payload if key in _RESULT_PAYLOAD_KEYS]
    if supplied:
        raise ExportRequestError(
            "This endpoint no longer accepts results from the browser "
            f"({', '.join(sorted(supplied))}). Send the query definition - "
            "query, filters, sort, scope, format - and the server will "
            "regenerate the result set itself, so the file cannot contain rows "
            "the database would not return.")

    unknown = sorted(set(payload) - _ACCEPTED)
    if unknown:
        raise ExportRequestError(
            "Unknown export parameters: " + ", ".join(unknown) + ". A query "
            "definition is the only input this endpoint accepts.")

    scope = str(payload.get("export_scope") or "filtered").strip().lower()
    if scope not in EXPORT_SCOPES:
        raise ExportRequestError(
            f"Unknown export_scope {scope!r}. Say which set you mean: "
            + ", ".join(EXPORT_SCOPES) + ".")

    export_format = str(payload.get("format") or "csv").strip().lower()
    if export_format == "xlsx":
        export_format = "excel"
    if export_format not in EXPORT_FORMATS:
        raise ExportRequestError(
            f"Unknown format {export_format!r}. Supported: "
            + ", ".join(EXPORT_FORMATS) + ".")

    try:
        page = max(int(payload.get("page", 1) or 1), 1)
        per_page = min(max(int(payload.get("per_page", 50) or 50), 1), 200)
    except (TypeError, ValueError):
        raise ExportRequestError("page and per_page must be whole numbers.") from None

    sort_by = str(payload.get("sort_by") or "relevance").strip().lower()
    sort_order = str(payload.get("sort_order") or "desc").strip().lower()
    if sort_by not in {"relevance", "date", "name", "type", "size"}:
        raise ExportRequestError("sort_by must be relevance, date, name, type, or size.")
    if sort_order not in ("asc", "desc"):
        raise ExportRequestError("sort_order must be 'asc' or 'desc'.")

    query = str(payload.get("query") or "").strip()
    analyst_scope = payload.get("analyst_scope")
    if analyst_scope is not None:
        analyst_scope = str(analyst_scope).strip().lower()
        if analyst_scope not in {"uncategorized", "categorized", "all"}:
            raise ExportRequestError("analyst_scope must be uncategorized, categorized, or all.")
    file_statuses = _as_statuses(payload)

    request = SearchExportRequest(
        query=query,
        scope=scope,
        format=export_format,
        filename=re.sub(
            r"\.(?:csv|xlsx?|json)$", "",
            str(payload.get("filename") or "").strip(),
            flags=re.IGNORECASE,
        ),
        page=page,
        per_page=per_page,
        file_types=_as_strings(payload.get("file_type"), "file_type"),
        source_ids=_as_ids(payload.get("source_ids")
                           if payload.get("source_ids") is not None
                           else payload.get("source_id"), "source_id"),
        side_ids=_as_ids(payload.get("side_ids")
                         if payload.get("side_ids") is not None
                         else payload.get("side_id"), "side_id"),
        category_ids=_as_ids(payload.get("category_ids")
                             if payload.get("category_ids") is not None
                             else payload.get("category_id"), "category_id"),
        analyst_category_ids=_as_ids(
            payload.get("analyst_category_ids")
            if payload.get("analyst_category_ids") is not None
            else payload.get("analyst_category_id"), "analyst_category_id"),
        file_statuses=file_statuses,
        analyst_scope=analyst_scope,
        date_from=_as_date(payload.get("date_from"), "date_from"),
        date_to=_as_date(payload.get("date_to"), "date_to"),
        sort_by=sort_by,
        sort_order=sort_order,
        use_advanced=_as_bool(payload.get("use_advanced"), True),
        use_fulltext=_as_bool(payload.get("use_fulltext"), True),
        use_bm25=_as_bool(payload.get("use_bm25"), True),
        use_expansion=_as_bool(payload.get("use_expansion"), True),
        use_fuzzy=_as_bool(payload.get("use_fuzzy"), True),
        case_sensitive=_as_bool(payload.get("case_sensitive"), False),
        whole_word=_as_bool(payload.get("whole_word"), False),
        hide_duplicates=_as_bool(payload.get("hide_duplicates"), False),
    )
    # The definition is kept beside the request: it is what the audit line and
    # any future export record need, and it is the whole of what was asked for.
    object.__setattr__(request, "definition", {
        "query": request.bounded_query,
        "scope": request.scope,
        "format": request.format,
        "file_type": list(request.file_types),
        "file_statuses": list(request.file_statuses) if request.file_statuses is not None else None,
        "analyst_scope": request.analyst_scope,
        "source_ids": list(request.source_ids),
        "side_ids": list(request.side_ids),
        "category_ids": list(request.category_ids),
        "analyst_category_ids": list(request.analyst_category_ids),
        "date_from": request.date_from,
        "date_to": request.date_to,
        "sort_by": request.sort_by,
        "sort_order": request.sort_order,
        "use_advanced": request.use_advanced,
        "use_fulltext": request.use_fulltext,
        "use_bm25": request.use_bm25,
        "use_expansion": request.use_expansion,
        "use_fuzzy": request.use_fuzzy,
        "case_sensitive": request.case_sensitive,
        "whole_word": request.whole_word,
        "hide_duplicates": request.hide_duplicates,
    })
    return request


# ---------------------------------------------------------------------------
# Running the query
# ---------------------------------------------------------------------------
def _search_once(request: SearchExportRequest, limit: int, offset: int,
                 analyst_scope: str):
    """One round of the same search the screen ran."""
    from Api.services.search_service import SearchService

    query = request.bounded_query
    if (request.use_advanced or request.hide_duplicates
            or request.case_sensitive or request.whole_word):
        return SearchService.advanced_search(
            query=query,
            file_type=list(request.file_types) or None,
            source_id=request.source_ids[0] if len(request.source_ids) == 1 else None,
            side_id=request.side_ids[0] if len(request.side_ids) == 1 else None,
            date_from=request.date_from,
            date_to=request.date_to,
            category_id=(request.category_ids[0]
                         if len(request.category_ids) == 1 else None),
            source_ids=list(request.source_ids) or None,
            side_ids=list(request.side_ids) or None,
            category_ids=list(request.category_ids) or None,
            sort_by=request.sort_by,
            sort_order=request.sort_order,
            limit=limit,
            offset=offset,
            use_bm25=request.use_bm25,
            use_expansion=request.use_expansion,
            use_fuzzy=request.use_fuzzy,
            analyst_scope=analyst_scope,
            analyst_category_ids=(list(request.analyst_category_ids) or None),
            file_statuses=(list(request.file_statuses)
                           if request.file_statuses is not None else None),
            hide_duplicates=request.hide_duplicates,
            case_sensitive=request.case_sensitive,
            whole_word=request.whole_word,
        )
    if request.use_fulltext and query:
        return SearchService.full_text_search(
            query=query,
            file_type=request.file_types[0] if len(request.file_types) == 1 else None,
            source_id=request.source_ids[0] if len(request.source_ids) == 1 else None,
            side_id=request.side_ids[0] if len(request.side_ids) == 1 else None,
            date_from=request.date_from,
            date_to=request.date_to,
            category_id=(request.category_ids[0]
                         if len(request.category_ids) == 1 else None),
            sort_by=request.sort_by,
            sort_order=request.sort_order,
            limit=limit,
            offset=offset,
            analyst_scope=analyst_scope,
            hide_duplicates=request.hide_duplicates,
        )
    return SearchService.simple_search(
        query=query, limit=limit, offset=offset, analyst_scope=analyst_scope)


def normalise(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """Every row in one shape, whatever the search path returned.

    The search service answers with tuples on some paths and dictionaries on
    others. A file that changes shape depending on how the query happened to be
    answered is a file nobody can trust, so the shape is fixed here. Older
    query tuples and dictionaries call the smart taxonomy ``categories``;
    exports publish it as ``smart_categories`` beside analyst categories.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            values = list(row) if isinstance(row, (list, tuple)) else [row]
            item = {
                column: values[index] if index < len(values) else None
                for index, column in enumerate(_RESULT_COLUMNS)
            }

        if "id" not in item:
            item["id"] = item.get("file_id")
        if "snippet" not in item:
            item["snippet"] = item.get("match_snippet") or item.get("line_content")
        if item.get("smart_categories") is None:
            item["smart_categories"] = item.get("categories")
        out.append({column: _clean(item.get(column)) for column in COLUMNS})
    return out


def _clean(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return value


def resolve(request: SearchExportRequest, analyst_scope: str) -> SearchExportResult:
    """Re-run the query the request describes and collect the rows to export."""
    result = SearchExportResult(request=request,
                                started_at=datetime.now().isoformat(timespec="seconds"))
    limit, offset = request.limit_offset
    rows, total = _search_once(request, limit, offset, analyst_scope)
    collected = normalise(rows)
    result.total = int(total or 0)

    if request.scope != "page":
        # The whole set, in rounds, up to the cap. Nothing is silently cut: the
        # result says whether it was, and by how much.
        while len(collected) < MAX_ROWS and len(collected) < result.total:
            more, _ = _search_once(request, min(CHUNK, MAX_ROWS - len(collected)),
                                   len(collected), analyst_scope)
            if not more:
                break
            collected.extend(normalise(more))
        result.truncated = result.total > len(collected)

    result.rows = collected
    result.completed_at = datetime.now().isoformat(timespec="seconds")
    logger.info(
        "search export: scope=%s format=%s query=%r filters=%s rows=%d total=%d "
        "truncated=%s", request.scope, request.format, request.bounded_query,
        {key: value for key, value in request.definition.items()
         if key.endswith("_ids") and value}, result.exported, result.total,
        result.truncated)
    return result


def suggested_filename(request: SearchExportRequest) -> str:
    """A stable, harmless name: the export's own description, sanitised."""
    def _safe(value: str, limit: int) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
        # A name is a name: no separators, no traversal, no double dots.
        cleaned = re.sub(r"\.{2,}", ".", cleaned).strip("._-")
        return cleaned[:limit]

    if request.filename:
        # A name supplied by the operator is the name of the export, not a
        # prefix to which the server silently appends a timestamp.
        return _safe(request.filename, 80) or "export"
    stem = _safe(request.query or "all", 40)
    base = f"search_{stem or 'all'}_{request.scope}"
    return f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def export_bytes(result: SearchExportResult):
    """Serialize the same published columns to CSV, Excel, or JSON."""
    from Api.services.document_intelligence import spreadsheet_safe_text

    rows = result.rows
    if result.request.format == "csv":
        import csv
        from io import BytesIO, StringIO

        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: spreadsheet_safe_text(row.get(column, ""))
                             for column in COLUMNS})
        return BytesIO(output.getvalue().encode("utf-8-sig")), "text/csv", "csv"

    if result.request.format == "excel":
        from io import BytesIO

        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise ImportError("openpyxl is required for Excel export") from exc

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Search Results"
        sheet.append(list(COLUMNS))
        for row in rows:
            sheet.append([spreadsheet_safe_text(row.get(column, ""))
                          for column in COLUMNS])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        output.seek(0)
        return (output,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xlsx")

    from Api.services.export_service import ExportService

    return (ExportService.export_search_results_json(rows),
            "application/json", "json")


__all__ = [
    "CHUNK",
    "COLUMNS",
    "EXPORT_FORMATS",
    "EXPORT_SCOPES",
    "ExportRequestError",
    "MAX_ROWS",
    "SearchExportRequest",
    "SearchExportResult",
    "export_bytes",
    "normalise",
    "parse",
    "resolve",
    "suggested_filename",
]
