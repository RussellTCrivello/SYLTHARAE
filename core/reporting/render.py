"""Artifact renderers: a completed run's stored datasets -> bytes (step 15).

Pure functions. Input is the *run document* - the run's recorded provenance
and the rows it stored (``services/reporting/artifacts.py`` builds it from
``report_runs`` / ``report_run_datasets``); nothing here touches the database,
the clock or the environment. The same document and renderer version give the
same bytes, byte for byte: generation time and the generating user belong to
the manifest, never to the content, and the XLSX container's timestamps are
pinned to the run's snapshot time.

Every rendering states, inside the content, the report and version, the run,
the snapshot, and for every dataset its limit semantics, its row count and
whether it was shortened - a reader of the file alone can tell a complete
listing from a capped one.

Values: the stored JSON values (dates and timestamps as ISO 8601 text,
numerics as exact decimal text). NULL is kept distinct where the format can
express it: JSON ``null``; HTML shows a marked "(none)"; CSV and XLSX have no
NULL, so an empty cell - the JSON artifact of the same run is the exact form,
and every manifest says how NULL was written (``null_representation``).

Spreadsheet safety: text that a spreadsheet would evaluate (leading ``=``,
``+``, ``-``, ``@``) is written with a leading apostrophe in CSV and XLSX,
using the repository's existing guard (``spreadsheet_safe_text``). Characters
XLSX cannot store (C0 controls) are replaced with U+FFFD and counted; the
count is reported, never silent.

Analyses (step 16): JSON and HTML carry every stored analysis of the run -
its state, measures, rows and the five-voice narrative. The narrative is
stored as template references; it is rendered here from the reviewed msgids
(English until the multilingual renderer of step 18) and the JSON also keeps
the references, so the text can be re-rendered in any catalog language. CSV
renders one dataset and XLSX the datasets; the manifest of every artifact
lists the run's analyses and says whether the artifact includes them.

PDF and SVG charts are multilingual rendering (step 18) and are not produced
here; asking for them is refused, not approximated.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

#: Renderer identity per format. Bump the number whenever the bytes a format
#: produces for the same run would change.
RENDERERS: Dict[str, str] = {
    "json": "report-json/2",     # 2: analyses section
    "csv": "report-csv/1",
    "xlsx": "report-xlsx/1",
    "html": "report-html/2",     # 2: analyses section
}
#: Formats whose content includes the run's analyses.
WITH_ANALYSES = frozenset({"json", "html"})
MEDIA_TYPES: Dict[str, str] = {
    "json": "application/json",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
}
#: Formats that render exactly one dataset (``dataset_key`` required).
SINGLE_DATASET = frozenset({"csv"})
#: Declared but produced in a later step; refused with this reason.
NOT_YET = {"pdf": "PDF is produced by the server-side multilingual renderer (step 18)",
           "svg": "charts are produced by the multilingual renderer (step 18)"}
NULL_REPRESENTATION = {"json": "JSON null", "html": "the marked text (none)",
                       "csv": "empty cell (indistinguishable from empty text; the JSON "
                              "artifact of the same run is exact)",
                       "xlsx": "empty cell (indistinguishable from empty text; the JSON "
                               "artifact of the same run is exact)"}

_XLSX_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class RenderError(ValueError):
    """The request cannot be rendered (unknown format, missing dataset...)."""


@dataclass
class Rendering:
    content: bytes
    format: str
    renderer_version: str
    media_type: str
    filename: str
    dataset_key: Optional[str]
    notes: Dict[str, Any] = field(default_factory=dict)


def safe_filename(*parts: Any) -> str:
    """A filename from server-side identifiers only, restricted to
    ``[A-Za-z0-9._-]`` (matches the table's CHECK)."""
    text = "_".join(str(p) for p in parts if p not in (None, ""))
    text = _FILENAME_UNSAFE.sub("-", text).strip("-._")
    text = re.sub(r"\.{2,}", ".", text)
    return (text or "report")[:180]


def canonical_json(value: Any) -> bytes:
    """The one serialisation used for JSON artifacts and manifest digests."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _dataset(document: Dict[str, Any], key: Optional[str]) -> Dict[str, Any]:
    for ds in document["datasets"]:
        if ds["dataset_key"] == key:
            return ds
    raise RenderError(f"the run has no dataset {key!r}")


def _completeness(ds: Dict[str, Any]) -> str:
    if ds["semantics"] == "exact":
        return f"complete: {ds['row_count']} rows (exact)"
    if ds["truncated"]:
        kind = "top-N" if ds["semantics"] == "top_n" else "capped"
        return (f"shortened: the first {ds['row_count']} rows are included; more rows "
                f"existed ({kind} at {ds['row_limit']})")
    return f"complete: {ds['row_count']} rows ({ds['semantics']} at {ds['row_limit']}, not reached)"


def _provenance_pairs(document: Dict[str, Any]) -> List[Tuple[str, Any]]:
    run = document["run"]
    return [
        ("report", run["report_key"]),
        ("title", document["report"].get("title")),
        ("unit", document["report"].get("unit")),
        ("definition fingerprint", run["definition_fingerprint"]),
        ("run", run["id"]),
        ("parameters fingerprint", run["parameters_fingerprint"]),
        ("criteria fingerprint", run["criteria_fingerprint"]),
        ("saved search", run["saved_search_id"]),
        ("requested by", run["requester_username"]),
        ("requester role", run["requester_role"]),
        ("snapshot", run["snapshot"]),
        ("snapshot time", run["snapshot_at"]),
        ("isolation", run["isolation_level"]),
        ("runner", run["generator_version"]),
        ("requested at", run["requested_at"]),
        ("finished at", run["finished_at"]),
    ]


def _analysis_text(analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    from core.analytics.narrative import render as render_narrative, source_ngettext

    return render_narrative(analysis["narrative"], lambda msgid: msgid,
                            ngettext=source_ngettext)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

def _render_json(document, dataset_key):
    body = {
        "format": RENDERERS["json"],
        "report": document["report"],
        "run": document["run"],
        "datasets": [dict(ds, completeness=_completeness(ds)) for ds in document["datasets"]],
        "analyses": [dict(a, text=_analysis_text(a)) for a in document.get("analyses", ())],
    }
    return canonical_json(body) + b"\n", {}


def _render_csv(document, dataset_key):
    from Api.services.document_intelligence import spreadsheet_safe_text

    ds = _dataset(document, dataset_key)
    names = [c["name"] for c in ds["columns"]]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(names)
    guarded = 0
    for row in ds["rows"]:
        cells = []
        for name in names:
            text = _cell_text(row.get(name))
            safe = spreadsheet_safe_text(text) if isinstance(row.get(name), str) else text
            guarded += safe != text
            cells.append(safe)
        writer.writerow(cells)
    return out.getvalue().encode("utf-8-sig"), {"formula_guarded_cells": guarded}


def _pin_zip(data: bytes, date_time: Tuple[int, int, int, int, int, int]) -> bytes:
    """Rewrite a ZIP container with fixed entry timestamps (content unchanged)."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            pinned = zipfile.ZipInfo(info.filename, date_time=date_time)
            pinned.compress_type = zipfile.ZIP_DEFLATED
            pinned.external_attr = 0o600 << 16
            target.writestr(pinned, source.read(info.filename))
    return out.getvalue()


def _render_xlsx(document, dataset_key):
    from openpyxl import Workbook

    from Api.services.document_intelligence import spreadsheet_safe_text

    notes = {"formula_guarded_cells": 0, "replaced_control_characters": 0}

    def text(value: str) -> str:
        cleaned, n = _XLSX_ILLEGAL.subn("\ufffd", value)
        notes["replaced_control_characters"] += n
        safe = spreadsheet_safe_text(cleaned)
        notes["formula_guarded_cells"] += safe != cleaned
        return safe

    def cell(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int) and abs(value) < 2 ** 53:
            return value
        if isinstance(value, float):
            return value
        return text(_cell_text(value))

    workbook = Workbook()
    info = workbook.active
    info.title = "Provenance"
    info.append(["field", "value"])
    for label, value in _provenance_pairs(document):
        info.append([label, cell(value)])
    info.append([])
    info.append(["dataset", "sheet", "semantics", "row limit", "rows", "truncated",
                 "completeness", "query fingerprint"])
    used = {"Provenance"}
    sheets = []
    for index, ds in enumerate(document["datasets"], start=1):
        base = re.sub(r"[\[\]\*\?/\\:]", "_", ds["dataset_key"].split("@")[0])[:26] or "data"
        name = base if base not in used else f"{base[:23]}_{index}"
        used.add(name)
        sheets.append((name, ds))
        info.append([ds["dataset_key"], name, ds["semantics"], ds["row_limit"],
                     ds["row_count"], "yes" if ds["truncated"] else "no",
                     _completeness(ds), ds["query_fingerprint"]])
    for name, ds in sheets:
        sheet = workbook.create_sheet(name)
        names = [c["name"] for c in ds["columns"]]
        sheet.append(names)
        for row in ds["rows"]:
            sheet.append([cell(row.get(n)) for n in names])
        sheet.freeze_panes = "A2"
    pinned = _snapshot_time(document)
    workbook.properties.creator = "SYLTHARAE " + RENDERERS["xlsx"]
    workbook.properties.created = pinned
    workbook.properties.modified = pinned
    workbook.properties.lastModifiedBy = None
    buffer = io.BytesIO()
    # openpyxl's save() stamps "now" into docProps; write through its
    # ExcelWriter instead so the pinned times stay, then pin the ZIP entries.
    from openpyxl.writer.excel import ExcelWriter

    archive = zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    ExcelWriter(workbook, archive).save()
    workbook.close()
    data = _pin_zip(buffer.getvalue(), (max(pinned.year, 1980), pinned.month, pinned.day,
                                        pinned.hour, pinned.minute, pinned.second))
    return data, notes


def _snapshot_time(document):
    import datetime

    raw = document["run"].get("snapshot_at")
    moment = datetime.datetime.fromisoformat(raw)
    if moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return moment.replace(microsecond=0)


_HTML_STYLE = (
    "body{font-family:system-ui,'Segoe UI','Noto Sans','Noto Sans Arabic','Noto Sans Hebrew',"
    "sans-serif;margin:1.5rem;color:#111}table{border-collapse:collapse;margin:.5rem 0 1.5rem}"
    "th,td{border:1px solid #bbb;padding:.25rem .5rem;text-align:start;vertical-align:top}"
    "th{background:#f0f0f0}.none{color:#777;font-style:italic}.short{color:#8a4b00;"
    "font-weight:600}.fp{font-family:monospace;font-size:.85em}")


def _render_html(document, dataset_key):
    e = lambda v: html.escape(_cell_text(v), quote=True)   # noqa: E731
    run, report = document["run"], document["report"]
    parts = ["<!DOCTYPE html>", '<html lang="en" dir="ltr"><head><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
             'style-src \'unsafe-inline\'">',
             f"<title>{e(report.get('title') or run['report_key'])} - run {e(run['id'])}</title>",
             f"<style>{_HTML_STYLE}</style></head><body>",
             f"<h1>{e(report.get('title') or run['report_key'])}</h1>",
             "<h2>Provenance</h2><table><tbody>"]
    for label, value in _provenance_pairs(document):
        shown = ('<span class="none">(none)</span>' if value is None
                 else f'<bdi class="fp">{e(value)}</bdi>')
        parts.append(f"<tr><th scope=\"row\">{e(label)}</th><td>{shown}</td></tr>")
    parts.append("</tbody></table>")
    for a in document.get("analyses", ()):
        parts.append(f"<h2>{e(a.get('title') or a['analysis_key'])}</h2>")
        state = ("measured" if a["state"] == "measured"
                 else f"not measurable ({a['reason']})")
        parts.append(f"<p>Analysis <bdi class=\"fp\">{e(a['analysis_key'])}</bdi>, {e(state)}; "
                     f"templates <bdi class=\"fp\">{e(a['template_set'])}@"
                     f"{e(a['template_version'])}</bdi>.</p><dl>")
        for voice in _analysis_text(a):
            parts.append(f"<dt>{e(voice['voice'].capitalize())}</dt><dd><bdi>{e(voice['text'])}"
                         "</bdi></dd>")
        parts.append("</dl>")
    for ds in document["datasets"]:
        cls = ' class="short"' if ds["truncated"] else ""
        parts.append(f"<h2><bdi>{e(ds['dataset_key'])}</bdi></h2>")
        parts.append(f"<p{cls}>{e(_completeness(ds))}. Query fingerprint "
                     f"<span class=\"fp\">{e(ds['query_fingerprint'])}</span>.</p>")
        parts.append("<table><thead><tr>")
        for column in ds["columns"]:
            parts.append(f"<th scope=\"col\" title=\"{e(column['name'])}\">"
                         f"{e(column.get('label') or column['name'])}</th>")
        parts.append("</tr></thead><tbody>")
        names = [c["name"] for c in ds["columns"]]
        for row in ds["rows"]:
            parts.append("<tr>")
            for name in names:
                value = row.get(name)
                parts.append('<td><span class="none">(none)</span></td>' if value is None
                             else f"<td><bdi>{e(value)}</bdi></td>")
            parts.append("</tr>")
        if not ds["rows"]:
            parts.append(f"<tr><td colspan=\"{max(1, len(names))}\">No rows.</td></tr>")
        parts.append("</tbody></table>")
    parts.append("</body></html>\n")
    return "\n".join(parts).encode("utf-8"), {}


_RENDER: Dict[str, Callable] = {"json": _render_json, "csv": _render_csv,
                                "xlsx": _render_xlsx, "html": _render_html}


def check_request(document_datasets: List[str], fmt: Any, dataset_key: Any) -> Tuple[str, Optional[str]]:
    """Validate a format/dataset request against a run's dataset keys."""
    if not isinstance(fmt, str) or not fmt:
        raise RenderError("format is required")
    if fmt in NOT_YET:
        raise RenderError(f"{fmt} is not available yet: {NOT_YET[fmt]}")
    if fmt not in RENDERERS:
        raise RenderError(f"format must be one of: {', '.join(sorted(RENDERERS))}")
    if fmt in SINGLE_DATASET:
        if not isinstance(dataset_key, str) or dataset_key not in document_datasets:
            raise RenderError(f"{fmt} renders one dataset: dataset_key must be one of "
                              f"{', '.join(document_datasets)}")
        return fmt, dataset_key
    if dataset_key not in (None, ""):
        raise RenderError(f"{fmt} renders every dataset of the run; omit dataset_key")
    return fmt, None


def render(document: Dict[str, Any], fmt: str, dataset_key: Optional[str] = None) -> Rendering:
    fmt, dataset_key = check_request([d["dataset_key"] for d in document["datasets"]],
                                     fmt, dataset_key)
    content, notes = _RENDER[fmt](document, dataset_key)
    run = document["run"]
    filename = safe_filename("report", run["report_id"], f"v{run['report_version']}",
                             f"run{run['id']}",
                             dataset_key.split("@")[0] if dataset_key else None) + "." + fmt
    notes = dict(notes, null_representation=NULL_REPRESENTATION[fmt])
    return Rendering(content=content, format=fmt, renderer_version=RENDERERS[fmt],
                     media_type=MEDIA_TYPES[fmt], filename=filename,
                     dataset_key=dataset_key, notes=notes)
