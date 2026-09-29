"""Analysis kinds and declared analyses.

An **analysis kind** is reviewed code: which dataset roles it reads (with the
columns and row-limit semantics it requires), how it computes its measures
from those rows (``core.analytics.measures``), which thresholds interpret
them (``core.analytics.thresholds``) and which narrative template set speaks
for it (``core.analytics.narrative``).

A declared **analysis** (``Analysis``) binds a kind to concrete dataset keys.
Reports list analyses by key (``id@version``); the runner computes them from
the dataset rows it has just read in the run's single snapshot, so an
analysis never queries the database and is reproducible from the stored
dataset rows alone.

States: ``measured`` or ``not_measurable`` (with a reason). An analysis never
reports a number it did not measure; "not measurable" is a narrative of its
own, not zeros.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from core.criteria.model import sha256_hex

from . import measures, thresholds
from .narrative import TemplateSet

MEASURED = "measured"
NOT_MEASURABLE = "not_measurable"

#: Relative tolerance between the database's G2/Log Ratio (used to rank) and
#: the reference implementation in ``measures`` (used to report).
_CROSS_CHECK_REL_TOL = 1e-9


class AnalysisError(RuntimeError):
    """The inputs contradict the kind's contract; the run must fail."""


@dataclass(frozen=True)
class InputRole:
    semantics: Tuple[str, ...]
    columns: Tuple[str, ...]


@dataclass(frozen=True)
class Kind:
    name: str
    version: int
    inputs: Mapping[str, InputRole]
    thresholds: Tuple[thresholds.Threshold, ...]
    templates: TemplateSet
    compute: Callable[[Mapping[str, Dict[str, Any]], Mapping[str, Any]], Dict[str, Any]]
    parameters: Tuple[str, ...] = ()

    def semantic(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "inputs": {r: {"semantics": list(i.semantics), "columns": list(i.columns)}
                           for r, i in sorted(self.inputs.items())},
                "thresholds": {t.key: t.value for t in self.thresholds},
                "templates": self.templates.fingerprint(),
                "parameters": list(self.parameters)}


# ---------------------------------------------------------------------------
# keyness
# ---------------------------------------------------------------------------

KEYNESS_TEMPLATES = TemplateSet("keyness", 1, {
    "measure.over": (
        "Log-likelihood keyness (G2) and Log Ratio compare %(target_tokens)s counted words "
        "in %(target_contents)s selected contents with %(reference_tokens)s words in the "
        "other %(reference_contents)s visible contents. Listed: the %(shown)s terms used "
        "more often in the selection, highest G2 first."),
    "measure.under": (
        "Log-likelihood keyness (G2) and Log Ratio compare %(target_tokens)s counted words "
        "in %(target_contents)s selected contents with %(reference_tokens)s words in the "
        "other %(reference_contents)s visible contents. Listed: the %(shown)s terms used "
        "less often in the selection, highest G2 first."),
    "measure.not_measurable": (
        "Log-likelihood keyness (G2) compares word frequencies in the selected contents "
        "with the other visible contents."),
    "finding.significant_over": (
        "%(significant)s terms are used significantly more often in the selection than in "
        "the rest of the collection (G2 of at least %(critical)s). The strongest is "
        "\"%(top_term)s\": G2 %(top_g2)s, Log Ratio %(top_log_ratio)s."),
    "finding.significant_under": (
        "%(significant)s terms are used significantly less often in the selection than in "
        "the rest of the collection (G2 of at least %(critical)s). The strongest is "
        "\"%(top_term)s\": G2 %(top_g2)s, Log Ratio %(top_log_ratio)s."),
    "finding.none": (
        "No term reaches G2 %(critical)s: at this level the selection's word frequencies "
        "do not differ from the rest of the collection."),
    "finding.no_target": "No visible content matches the criteria, so there is nothing to compare.",
    "finding.no_target_tokens": (
        "The matching contents have no counted words, so keyness cannot be measured."),
    "finding.no_reference": (
        "The matching contents are the whole visible collection, so there is no reference "
        "to compare them with."),
    "confidence.threshold": (
        "Only terms with G2 of at least %(critical)s count as findings (p < 0.0001; "
        "%(source)s). Every term in the vocabulary is tested, so the weaker significance "
        "levels are not treated as findings."),
    "confidence.not_measurable": "No statistical test was performed.",
    "consequence.significant": (
        "These terms characterise the selection against the rest of the collection. They "
        "show where it differs, not why: read the documents that contain them before "
        "drawing conclusions."),
    "consequence.none": (
        "Any difference between the selection and the collection is not in word frequency "
        "at this level; categories, sources or dates may show one."),
    "consequence.not_measurable": "No conclusion can be drawn from this section.",
    "caveat.default": (
        "Keyness compares stored word forms. It does not merge inflections, synonyms or "
        "languages, so a selection in another language than the collection shows its "
        "function words. Next step: open the documents that contain the top terms."),
    "caveat.unknown_counts": (
        "Keyness compares stored word forms. It does not merge inflections, synonyms or "
        "languages. %(unknown_rows)s word records without a count were left out of every "
        "total. Next step: open the documents that contain the top terms."),
    "caveat.not_measurable": (
        "Next step: widen the criteria, or check that the matching documents were processed."),
})


def _close(sql_value, reference) -> bool:
    if sql_value is None or reference is None:
        return sql_value is None and reference is None
    return math.isclose(float(sql_value), reference, rel_tol=_CROSS_CHECK_REL_TOL, abs_tol=1e-9)


def _keyness(inputs: Mapping[str, Dict[str, Any]], params: Mapping[str, Any]) -> Dict[str, Any]:
    direction = params.get("direction") or "over"
    [totals] = inputs["totals"]["rows"]
    ranked = inputs["ranked"]
    c, d = int(totals["target_tokens"]), int(totals["reference_tokens"])
    target_contents = int(totals["target_contents"])
    reference_contents = int(totals["reference_contents"])
    unknown = int(totals["unknown_count_rows"])
    T = KEYNESS_TEMPLATES
    base = {"direction": direction, "target_tokens": c, "reference_tokens": d,
            "target_contents": target_contents, "reference_contents": reference_contents,
            "unknown_count_rows": unknown}

    reason = None
    if target_contents == 0:
        reason = "no_target"
    elif c == 0:
        reason = "no_target_tokens"
    elif reference_contents == 0 or d == 0:
        reason = "no_reference"
    if reason:
        return {"state": NOT_MEASURABLE, "reason": reason, "measures": base, "rows": [],
                "narrative": T.compose([
                    ("measure.not_measurable", {}), (f"finding.{reason}", {}),
                    ("confidence.not_measurable", {}), ("consequence.not_measurable", {}),
                    ("caveat.not_measurable", {})])}

    rows = []
    for r in ranked["rows"]:
        a, b = int(r["target_freq"]), int(r["reference_freq"])
        g2 = measures.log_likelihood(a, b, c, d)
        lr = measures.log_ratio(a, b, c, d)
        if not _close(r["g2"], g2) or not _close(r["log_ratio"], lr):
            raise AnalysisError(
                f"keyness: database and reference G2/Log Ratio disagree for term "
                f"{r['term']!r} (db {r['g2']}/{r['log_ratio']}, reference {g2}/{lr})")
        over = a * d > b * c
        if (direction == "over") != over:
            raise AnalysisError(f"keyness: term {r['term']!r} is not in direction {direction}")
        rows.append({"term": r["term"], "target_freq": a, "reference_freq": b,
                     "target_per_million": a / c * 1e6, "reference_per_million": b / d * 1e6,
                     "g2": g2, "log_ratio": lr, "level": thresholds.ll_level(g2)})

    critical = thresholds.LL_P0001
    significant = int(totals["significant_terms"])
    shown_significant = sum(1 for r in rows if r["g2"] >= critical.value)
    # The exact count covers the whole vocabulary; the listing is a ranked
    # prefix of it, so the significant rows shown can never exceed it.
    if shown_significant > significant:
        raise AnalysisError("keyness: more significant terms listed than counted")
    measures_out = dict(base, shown=len(rows), significant_terms=significant,
                        critical_value=critical.value, listing_truncated=ranked["truncated"])
    if significant and rows:
        top = rows[0]
        finding = (f"finding.significant_{direction}",
                   {"significant": significant, "critical": critical.value,
                    "top_term": top["term"], "top_g2": round(top["g2"], 2),
                    "top_log_ratio": round(top["log_ratio"], 2)})
        consequence = ("consequence.significant", {})
    else:
        finding = ("finding.none", {"critical": critical.value})
        consequence = ("consequence.none", {})
    caveat = (("caveat.unknown_counts", {"unknown_rows": unknown}) if unknown
              else ("caveat.default", {}))
    return {"state": MEASURED, "reason": None, "measures": measures_out, "rows": rows,
            "narrative": T.compose([
                (f"measure.{direction}", {"target_tokens": c, "target_contents": target_contents,
                                          "reference_tokens": d,
                                          "reference_contents": reference_contents,
                                          "shown": len(rows)}),
                finding,
                ("confidence.threshold", {"critical": critical.value, "source": critical.source}),
                consequence, caveat])}


KEYNESS = Kind(
    name="keyness", version=1,
    inputs={
        "ranked": InputRole(("top_n",), ("term", "target_freq", "reference_freq", "g2",
                                         "log_ratio")),
        "totals": InputRole(("exact",), ("target_tokens", "reference_tokens",
                                         "target_contents", "reference_contents",
                                         "unknown_count_rows", "significant_terms")),
    },
    thresholds=(thresholds.LL_P05, thresholds.LL_P01, thresholds.LL_P001,
                thresholds.LL_P0001),
    templates=KEYNESS_TEMPLATES,
    compute=_keyness,
    parameters=("direction",),
)

KINDS: Dict[str, Kind] = {k.name: k for k in (KEYNESS,)}


# ---------------------------------------------------------------------------
# Declared analyses
# ---------------------------------------------------------------------------

_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Analysis:
    analysis_id: str
    version: int
    kind: str
    inputs: Mapping[str, str]          # role -> dataset key "id@version"
    description: str                   # reviewer prose
    title: str                         # msgid shown as the section heading

    def __post_init__(self) -> None:
        if not _ID.match(self.analysis_id or ""):
            raise ValueError(f"analysis id {self.analysis_id!r} must be snake_case")
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"{self.analysis_id}: version must be an integer >= 1")
        kind = KINDS.get(self.kind)
        if kind is None:
            raise ValueError(f"{self.analysis_id}: unknown kind {self.kind!r}")
        if set(self.inputs) != set(kind.inputs):
            raise ValueError(f"{self.analysis_id}: inputs must be exactly the roles "
                             f"{sorted(kind.inputs)} of kind {kind.name}")
        if not self.title or self.title.strip() != self.title:
            raise ValueError(f"{self.analysis_id}: title msgid required")

    @property
    def key(self) -> str:
        return f"{self.analysis_id}@{self.version}"

    @property
    def kind_obj(self) -> Kind:
        return KINDS[self.kind]

    def semantic(self, dataset_fingerprints: Mapping[str, str]) -> Dict[str, Any]:
        return {"analysis_id": self.analysis_id, "version": self.version,
                "kind": self.kind_obj.semantic(),
                "inputs": {role: [key, dataset_fingerprints.get(key)]
                           for role, key in sorted(self.inputs.items())}}

    def fingerprint(self, dataset_fingerprints: Mapping[str, str]) -> str:
        return sha256_hex(self.semantic(dataset_fingerprints))

    def compute(self, datasets_by_key: Mapping[str, Dict[str, Any]],
                parameters: Mapping[str, Any]) -> Dict[str, Any]:
        """Result for the run's stored dataset results (keyed by dataset
        key). Validates each input against the kind's contract first."""
        kind = self.kind_obj
        inputs = {}
        for role, spec in kind.inputs.items():
            ds = datasets_by_key.get(self.inputs[role])
            if ds is None:
                raise AnalysisError(f"{self.key}: input {role} ({self.inputs[role]}) was not read")
            if ds["semantics"] not in spec.semantics:
                raise AnalysisError(f"{self.key}: input {role} must be {spec.semantics}, "
                                    f"is {ds['semantics']}")
            names = [c["name"] for c in ds["columns"]]
            missing = [c for c in spec.columns if c not in names]
            if missing:
                raise AnalysisError(f"{self.key}: input {role} lacks columns {missing}")
            inputs[role] = ds
        result = kind.compute(inputs, {p: parameters.get(p) for p in kind.parameters})
        result["analysis_key"] = self.key
        result["kind"] = kind.name
        return result
