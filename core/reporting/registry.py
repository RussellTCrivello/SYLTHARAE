"""The report registry: one place that knows which reports exist.

``validate()`` is the gate. It returns every problem it finds (it does not
stop at the first) and checks:

* ids: report ``id@version`` and dataset ``id@version`` unique; at most one
  *active* version per report id; versions of an id are contiguous from 1;
* datasets: every referenced ``id@version`` is registered; every dataset
  parameter is declared by the report with a compatible type; the criteria
  parameter is a ``criteria`` parameter; report unit agrees with its primary
  (first) dataset;
* roles: known to ``core.security``; a report's roles are a subset of every
  dataset's roles (nobody may run a report whose data they may not read);
* help topics: every report's topic is declared, every declared topic is used;
* translations (``validate_translations``): every msgid is in the template
  and translated - present, non-empty, not fuzzy, placeholders intact - in
  every shipped catalog;
* fingerprints (``validate_lock``): each ``id@version`` matches the pinned
  fingerprint in ``definitions.lock.json``, and no pinned version vanished.

Nothing here authorises a request: the API checks roles through
``core.security``; ``visible_to`` only filters what is offered.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from core.security.service import ALL_ROLES

from core.analytics.kinds import Analysis

from .datasets import DATASETS
from .definitions import ANALYSES, HELP_TOPICS, REPORTS
from .model import Dataset, HelpTopic, ReportDefinition

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "definitions.lock.json")
TRANSLATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "translations")
TRANSLATED_LANGUAGES: Tuple[str, ...] = ("ar", "he", "fa", "hr")
_PLACEHOLDER = re.compile(r"%\([a-z_]+\)[sd]|\{[a-z_]*\}")


SINGULAR = {"datasets": "dataset", "analyses": "analysis", "reports": "report"}


class ReportNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ReportRegistry:
    reports: Tuple[ReportDefinition, ...]
    datasets: Tuple[Dataset, ...]
    help_topics: Tuple[HelpTopic, ...]
    analyses: Tuple[Analysis, ...] = ()

    # ------------------------------------------------------------ lookup
    def dataset(self, key: str) -> Dataset:
        for ds in self.datasets:
            if ds.key == key:
                return ds
        raise ReportNotFound(f"no dataset {key!r}")

    def report(self, report_id: str, version: Optional[int] = None) -> ReportDefinition:
        """A specific version, or the active version when ``version`` is None."""
        candidates = [r for r in self.reports if r.report_id == report_id]
        if version is None:
            candidates = [r for r in candidates if r.status == "active"]
        else:
            candidates = [r for r in candidates if r.version == version]
        if len(candidates) != 1:
            raise ReportNotFound(
                f"no report {report_id!r}" + (f" version {version}" if version else ""))
        return candidates[0]

    def active(self) -> Tuple[ReportDefinition, ...]:
        return tuple(sorted((r for r in self.reports if r.status == "active"),
                            key=lambda r: r.report_id))

    def visible_to(self, role: Optional[str]) -> Tuple[ReportDefinition, ...]:
        return tuple(r for r in self.active() if role in r.roles)

    def analysis(self, key: str) -> Analysis:
        for a in self.analyses:
            if a.key == key:
                return a
        raise ReportNotFound(f"no analysis {key!r}")

    def help_topic(self, topic: str) -> Optional[HelpTopic]:
        return next((h for h in self.help_topics if h.topic == topic), None)

    def dataset_fingerprints(self) -> Dict[str, str]:
        return {ds.key: ds.fingerprint() for ds in self.datasets}

    def analysis_fingerprints(self) -> Dict[str, str]:
        ds_fp = self.dataset_fingerprints()
        return {a.key: a.fingerprint(ds_fp) for a in self.analyses}

    def report_fingerprint(self, report: ReportDefinition) -> str:
        return report.fingerprint(self.dataset_fingerprints(), self.analysis_fingerprints())

    def fingerprints(self) -> Dict[str, Dict[str, str]]:
        ds_fp = self.dataset_fingerprints()
        an_fp = self.analysis_fingerprints()
        return {
            "datasets": dict(sorted(ds_fp.items())),
            "analyses": dict(sorted(an_fp.items())),
            "reports": dict(sorted((r.key, r.fingerprint(ds_fp, an_fp))
                                   for r in self.reports)),
        }

    def translation_keys(self) -> Tuple[str, ...]:
        keys: List[str] = []
        for r in self.reports:
            keys.extend(r.translation_keys())
        for ds in self.datasets:
            keys.extend(c.label for c in ds.columns)
        for h in self.help_topics:
            keys.extend((h.title, h.summary))
        for a in self.analyses:
            keys.append(a.title)
            keys.extend(a.kind_obj.templates.msgids())
        return tuple(dict.fromkeys(keys))

    # ------------------------------------------------------------ validation
    def validate(self) -> List[str]:
        problems: List[str] = []
        problems.extend(_duplicates("dataset", [d.key for d in self.datasets]))
        problems.extend(_duplicates("report", [r.key for r in self.reports]))
        problems.extend(_duplicates("help topic", [h.topic for h in self.help_topics]))

        by_id: Dict[str, List[ReportDefinition]] = {}
        for r in self.reports:
            by_id.setdefault(r.report_id, []).append(r)
        for rid, versions in sorted(by_id.items()):
            active = [r for r in versions if r.status == "active"]
            if len(active) > 1:
                problems.append(f"{rid}: more than one active version")
            numbers = sorted(r.version for r in versions)
            if numbers != list(range(1, len(numbers) + 1)):
                problems.append(f"{rid}: versions {numbers} are not contiguous from 1 "
                                "(a released version was removed)")
        ds_ids: Dict[str, List[int]] = {}
        for ds in self.datasets:
            ds_ids.setdefault(ds.dataset_id, []).append(ds.version)
            unknown = sorted(set(ds.roles) - set(ALL_ROLES))
            if unknown:
                problems.append(f"{ds.key}: unknown roles {unknown}")
        for did, numbers in sorted(ds_ids.items()):
            numbers = sorted(numbers)
            if numbers != list(range(1, len(numbers) + 1)):
                problems.append(f"{did}: dataset versions {numbers} are not contiguous from 1")

        known_ds = {d.key: d for d in self.datasets}
        topics = {h.topic for h in self.help_topics}
        used_topics = set()
        referenced = set()
        for r in self.reports:
            unknown = sorted(set(r.roles) - set(ALL_ROLES))
            if unknown:
                problems.append(f"{r.key}: unknown roles {unknown}")
            if r.help_topic not in topics:
                problems.append(f"{r.key}: help topic {r.help_topic!r} is not declared")
            used_topics.add(r.help_topic)
            for index, key in enumerate(r.datasets):
                ds = known_ds.get(key)
                if ds is None:
                    problems.append(f"{r.key}: dataset {key!r} is not registered")
                    continue
                referenced.add(key)
                if index == 0 and ds.unit != r.unit:
                    problems.append(f"{r.key}: unit {r.unit!r} differs from its primary "
                                    f"dataset {key} unit {ds.unit!r}")
                missing_roles = sorted(set(r.roles) - set(ds.roles))
                if missing_roles:
                    problems.append(f"{r.key}: roles {missing_roles} may run the report "
                                    f"but may not read dataset {key}")
                for name in ds.parameters:
                    param = r.parameter(name)
                    if param is None:
                        problems.append(f"{r.key}: dataset {key} reads parameter "
                                        f"{name!r} that the report does not declare")
                    elif name == ds.criteria_param and param.type != "criteria":
                        problems.append(f"{r.key}: dataset {key} takes criteria from "
                                        f"{name!r}, which is a {param.type} parameter")
                    elif name != ds.criteria_param and param.type == "criteria":
                        problems.append(f"{r.key}: criteria parameter {name!r} is bound "
                                        f"as a plain value in {key}")
            used = {n for k in r.datasets if k in known_ds for n in known_ds[k].parameters}
            for p in r.parameters:
                if p.name not in used:
                    problems.append(f"{r.key}: parameter {p.name!r} is read by no dataset")
        problems.extend(self._validate_analyses(known_ds))
        for key in sorted(set(known_ds) - referenced):
            problems.append(f"dataset {key} is used by no report")
        for topic in sorted(topics - used_topics):
            problems.append(f"help topic {topic!r} is used by no report")
        return problems

    def _validate_analyses(self, known_ds: Mapping[str, Dataset]) -> List[str]:
        problems = _duplicates("analysis", [a.key for a in self.analyses])
        known = {a.key: a for a in self.analyses}
        used = set()
        for r in self.reports:
            for key in r.analyses:
                a = known.get(key)
                if a is None:
                    problems.append(f"{r.key}: analysis {key!r} is not registered")
                    continue
                used.add(key)
                for role, ds_key in sorted(a.inputs.items()):
                    if ds_key not in r.datasets:
                        problems.append(f"{r.key}: analysis {key} reads {ds_key} ({role}), "
                                        "which the report does not read")
                for name in a.kind_obj.parameters:
                    if r.parameter(name) is None:
                        problems.append(f"{r.key}: analysis {key} needs parameter {name!r}")
        for a in self.analyses:
            for role, ds_key in sorted(a.inputs.items()):
                spec = a.kind_obj.inputs[role]
                ds = known_ds.get(ds_key)
                if ds is None:
                    problems.append(f"analysis {a.key}: input {role} {ds_key!r} is not registered")
                    continue
                if ds.semantics not in spec.semantics:
                    problems.append(f"analysis {a.key}: input {role} must be "
                                    f"{'/'.join(spec.semantics)}, {ds_key} is {ds.semantics}")
                missing = [c for c in spec.columns if c not in [x.name for x in ds.columns]]
                if missing:
                    problems.append(f"analysis {a.key}: input {role} {ds_key} lacks {missing}")
        for key in sorted(set(known) - used):
            problems.append(f"analysis {key} is used by no report")
        return problems

    def validate_translations(self, translations_dir: str = TRANSLATIONS_DIR,
                              languages: Iterable[str] = TRANSLATED_LANGUAGES) -> List[str]:
        from babel.messages.pofile import read_po

        problems: List[str] = []
        keys = self.translation_keys()
        pot = os.path.join(translations_dir, "messages.pot")
        with open(pot, "rb") as fh:
            template = read_po(fh)
        for key in keys:
            if template.get(key) is None:
                problems.append(f"msgid {key!r} is missing from messages.pot")
        for lang in ("en",) + tuple(languages):
            path = os.path.join(translations_dir, lang, "LC_MESSAGES", "messages.po")
            with open(path, "rb") as fh:
                catalog = read_po(fh, locale=lang)
            for key in keys:
                message = catalog.get(key)
                if message is None:
                    problems.append(f"[{lang}] msgid {key!r} is missing")
                    continue
                if lang == "en":
                    continue
                if message.fuzzy:
                    problems.append(f"[{lang}] {key!r} is fuzzy")
                if not message.string:
                    problems.append(f"[{lang}] {key!r} is untranslated")
                    continue
                if message.string == key:
                    problems.append(f"[{lang}] {key!r} is identical to its msgid")
                if sorted(_PLACEHOLDER.findall(message.string)) != sorted(
                        _PLACEHOLDER.findall(key)):
                    problems.append(f"[{lang}] {key!r} placeholders differ")
        return problems

    def validate_lock(self, lock: Optional[Mapping[str, Mapping[str, str]]] = None) -> List[str]:
        lock = read_lock() if lock is None else lock
        current = self.fingerprints()
        problems: List[str] = []
        for section in ("datasets", "analyses", "reports"):
            pinned = dict(lock.get(section, {}))
            for key, fp in current[section].items():
                if key not in pinned:
                    problems.append(f"{SINGULAR[section]} {key} is not pinned: run "
                                    "`python -m core.reporting.lock --write`")
                elif pinned[key] != fp:
                    problems.append(
                        f"{SINGULAR[section]} {key} changed meaning (fingerprint "
                        f"{pinned[key][:12]} -> {fp[:12]}): declare a new version "
                        "instead of editing a released one")
            for key in sorted(set(pinned) - set(current[section])):
                problems.append(f"{SINGULAR[section]} {key} is pinned but no longer "
                                "registered: released versions must stay")
        return problems


def _duplicates(kind: str, keys: List[str]) -> List[str]:
    seen, dupes = set(), []
    for key in keys:
        if key in seen:
            dupes.append(f"duplicate {kind} {key}")
        seen.add(key)
    return dupes


def read_lock(path: str = LOCK_PATH) -> Dict[str, Dict[str, str]]:
    if not os.path.exists(path):
        return {"datasets": {}, "analyses": {}, "reports": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


REGISTRY = ReportRegistry(reports=REPORTS, datasets=DATASETS, help_topics=HELP_TOPICS,
                          analyses=ANALYSES)
