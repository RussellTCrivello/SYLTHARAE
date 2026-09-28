"""Report definitions and datasets: declarations, not behaviour.

A report is a *versioned, code-reviewed declaration*. It names the datasets it
reads, the parameters it accepts, the roles that may run it, the unit it
counts, the help topic that explains it and the translation keys it shows.
A dataset declares its columns (name, type, nullability), its parameterised
SQL, its row limit and what that limit means (``exact`` / ``capped`` /
``top_n``). Nothing here runs a query; ``Dataset.bind`` only produces the SQL
text and the bound parameters the runner (step 14) will execute.

Invariants enforced here (construction) and in ``registry.validate`` (cross
references, translations, fingerprints):

* **Parameterised only.** SQL text comes from code constants. The only
  substitutions are the named slots in ``SQL_SLOTS``, filled from code
  (the criteria compiler, the access scope). Every value is a bound ``%s``.
* **Access before retrieval.** Every dataset either takes its WHERE clause
  from the criteria compiler (which applies ``AccessScope`` in SQL), or has
  an explicit ``{scope}`` slot bound to a declared source column, or states
  in words why it reads nothing source-scoped. There is no fourth option.
* **No silent truncation.** Every dataset ends with ``LIMIT %s`` (token
  ``@limit``); the runner binds ``row_limit + 1`` so it can *see* overflow.
  ``exact`` fails on overflow, ``capped`` records truncation, ``top_n``
  declares it is a ranked prefix. ``capped``/``top_n`` must be ordered.
* **Semantic identity is a fingerprint.** ``fingerprint()`` hashes every
  field that changes what a result *means*. Presentation keys and prose are
  excluded. The lock file (``definitions.lock.json``) pins each
  ``id@version``; a changed fingerprint under an existing version fails the
  check, so a semantic change requires a new version.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.criteria.model import CriteriaError, from_dict as criteria_from_dict, sha256_hex

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: What a report or dataset counts. "Every report declares its unit."
UNITS: Tuple[str, ...] = (
    "path",            # one file occurrence (paths.id)
    "hash",            # one distinct content (hashs.id)
    "context",         # one (hash_id, source_id, side_id) context
    "term",            # one word / keyword
    "source",
    "side",
    "category",
    "signal",          # one content_signals row
    "place",           # one gazetteer place
    "notification",
    "rule",
    "scenario_outcome",
)

#: Declared column type -> PostgreSQL type OIDs it may be read as. Verified
#: against a live cursor in ``tests/integration/test_report_registry_pg.py``.
COLUMN_TYPES: Dict[str, Tuple[int, ...]] = {
    "integer": (23, 21),          # int4, int2
    "bigint": (20,),              # int8 (COUNT(*) is int8)
    "numeric": (1700, 700, 701),  # numeric, float4, float8
    "text": (25, 1043, 1042),     # text, varchar, bpchar
    "date": (1082,),
    "timestamptz": (1184,),
    "boolean": (16,),
    "json": (114, 3802),          # json, jsonb
}

#: What ``row_limit`` means.
SEMANTICS: Tuple[str, ...] = (
    "exact",    # all rows or nothing: overflow fails the run
    "capped",   # at most row_limit rows; overflow is recorded as truncation
    "top_n",    # a ranked prefix of length row_limit, by declaration
)

PARAMETER_TYPES: Tuple[str, ...] = (
    "criteria", "integer", "date", "enum", "boolean", "text",
)

#: Named SQL slots. Each is filled from code, never from a request value.
SQL_SLOTS: Tuple[str, ...] = ("where", "order", "scope")

#: Special parameter tokens in ``Dataset.sql_params``.
TOKEN_CRITERIA = "@criteria"   # params of the compiled {where}
TOKEN_SCOPE = "@scope"         # params of the {scope} predicate
TOKEN_LIMIT = "@limit"         # row_limit + 1, always last

ROW_LIMIT_CEILING = 100_000

_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_HELP_TOPIC = re.compile(r"^[a-z][a-z0-9-]*(/[a-z][a-z0-9-]*)+$")


class ReportDefinitionError(ValueError):
    """A report or dataset declaration is malformed."""


class ReportParameterError(ValueError):
    """A supplied parameter value does not satisfy its declaration."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ReportDefinitionError(message)


def _msgid(value: str, what: str) -> None:
    _check(isinstance(value, str) and value.strip() == value and bool(value),
           f"{what} must be a non-empty msgid without surrounding whitespace")


# ---------------------------------------------------------------------------
# Columns and parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool
    label: str                     # msgid shown as the column header

    def __post_init__(self) -> None:
        _check(bool(re.match(r"^[a-z][a-z0-9_]*$", self.name or "")),
               f"column name {self.name!r} must be snake_case")
        _check(self.type in COLUMN_TYPES,
               f"column {self.name!r}: type {self.type!r} is not one of {sorted(COLUMN_TYPES)}")
        _check(isinstance(self.nullable, bool),
               f"column {self.name!r}: nullable must be declared True or False")
        _msgid(self.label, f"column {self.name!r} label")

    def semantic(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "nullable": self.nullable}


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str
    label: str                     # msgid
    required: bool = True
    default: Any = None
    choices: Tuple[str, ...] = ()
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    max_length: Optional[int] = None

    def __post_init__(self) -> None:
        _check(bool(re.match(r"^[a-z][a-z0-9_]*$", self.name or "")),
               f"parameter name {self.name!r} must be snake_case")
        _check(self.type in PARAMETER_TYPES,
               f"parameter {self.name!r}: type {self.type!r} is not one of {PARAMETER_TYPES}")
        _msgid(self.label, f"parameter {self.name!r} label")
        _check(not (self.required and self.default is not None),
               f"parameter {self.name!r}: a required parameter cannot have a default")
        if self.type == "enum":
            _check(len(self.choices) >= 2 and len(set(self.choices)) == len(self.choices),
                   f"parameter {self.name!r}: enum needs two or more distinct choices")
        else:
            _check(not self.choices, f"parameter {self.name!r}: choices apply to enum only")
        if self.type != "integer":
            _check(self.minimum is None and self.maximum is None,
                   f"parameter {self.name!r}: minimum/maximum apply to integer only")
        elif self.minimum is not None and self.maximum is not None:
            _check(self.minimum <= self.maximum,
                   f"parameter {self.name!r}: minimum exceeds maximum")
        if self.type == "text":
            _check(isinstance(self.max_length, int) and 0 < self.max_length <= 10_000,
                   f"parameter {self.name!r}: text needs max_length between 1 and 10000")
        else:
            _check(self.max_length is None,
                   f"parameter {self.name!r}: max_length applies to text only")
        if self.default is not None:
            # A default must itself be valid; normalise raises otherwise.
            try:
                self.normalize(self.default)
            except ReportParameterError as exc:
                raise ReportDefinitionError(
                    f"parameter {self.name!r}: invalid default: {exc}") from exc

    def semantic(self) -> Dict[str, Any]:
        return {
            "name": self.name, "type": self.type, "required": self.required,
            "default": self.default, "choices": list(self.choices),
            "minimum": self.minimum, "maximum": self.maximum,
            "max_length": self.max_length,
        }

    def normalize(self, value: Any) -> Any:
        """Validated, canonical (JSON-serialisable) value. ``None`` only for
        an optional parameter without default."""
        if value is None:
            if self.required:
                raise ReportParameterError(f"{self.name} is required")
            return self.default
        if self.type == "criteria":
            if not isinstance(value, Mapping):
                raise ReportParameterError(f"{self.name} must be a criteria object")
            try:
                return criteria_from_dict(dict(value)).to_dict()
            except CriteriaError as exc:
                raise ReportParameterError(f"{self.name}: {exc}") from exc
        if self.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                if isinstance(value, str) and re.fullmatch(r"-?\d{1,12}", value.strip()):
                    value = int(value.strip())
                else:
                    raise ReportParameterError(f"{self.name} must be a whole number")
            if self.minimum is not None and value < self.minimum:
                raise ReportParameterError(f"{self.name} must be at least {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ReportParameterError(f"{self.name} must be at most {self.maximum}")
            return value
        if self.type == "date":
            if not isinstance(value, str):
                raise ReportParameterError(f"{self.name} must be a date (YYYY-MM-DD)")
            try:
                return date.fromisoformat(value.strip()).isoformat()
            except ValueError as exc:
                raise ReportParameterError(f"{self.name} must be a date (YYYY-MM-DD)") from exc
        if self.type == "enum":
            if value not in self.choices:
                raise ReportParameterError(
                    f"{self.name} must be one of: {', '.join(self.choices)}")
            return value
        if self.type == "boolean":
            if not isinstance(value, bool):
                raise ReportParameterError(f"{self.name} must be true or false")
            return value
        # text
        if not isinstance(value, str):
            raise ReportParameterError(f"{self.name} must be text")
        if len(value) > self.max_length:
            raise ReportParameterError(
                f"{self.name} must be at most {self.max_length} characters")
        return value


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def _slots(sql: str) -> List[str]:
    names = []
    for _, name, spec, conv in string.Formatter().parse(sql):
        if name is None:
            continue
        _check(name in SQL_SLOTS and not spec and not conv,
               f"SQL slot {{{name}}} is not one of {SQL_SLOTS} (or carries a format spec)")
        names.append(name)
    return names


@dataclass(frozen=True)
class BoundQuery:
    """SQL text and bound values, ready for ``cursor.execute``."""

    sql: str
    params: Tuple[Any, ...]
    fetch_limit: int               # row_limit + 1
    criteria_fingerprint: Optional[str] = None
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    version: int
    description: str               # reviewer-facing prose; not a msgid
    unit: str
    semantics: str
    row_limit: int
    columns: Tuple[Column, ...]
    sql: str
    sql_params: Tuple[str, ...]    # parameter names and @tokens, in %s order
    roles: Tuple[str, ...]
    parameters: Tuple[str, ...] = ()      # report parameter names it reads
    criteria_param: Optional[str] = None  # which parameter feeds {where}/{order}
    scope_column: Optional[str] = None    # column the {scope} slot restricts
    unscoped_reason: Optional[str] = None

    def __post_init__(self) -> None:
        _check(bool(_ID.match(self.dataset_id or "")),
               f"dataset id {self.dataset_id!r} must be dotted snake_case")
        _check(isinstance(self.version, int) and self.version >= 1,
               f"{self.dataset_id}: version must be an integer >= 1")
        _check(bool(self.description and self.description.strip()),
               f"{self.dataset_id}: description is required")
        _check(self.unit in UNITS, f"{self.dataset_id}: unit {self.unit!r} is not one of {UNITS}")
        _check(self.semantics in SEMANTICS,
               f"{self.dataset_id}: semantics {self.semantics!r} is not one of {SEMANTICS}")
        _check(isinstance(self.row_limit, int) and 1 <= self.row_limit <= ROW_LIMIT_CEILING,
               f"{self.dataset_id}: row_limit must be between 1 and {ROW_LIMIT_CEILING}")
        _check(bool(self.columns), f"{self.dataset_id}: at least one column is required")
        names = [c.name for c in self.columns]
        _check(len(names) == len(set(names)), f"{self.dataset_id}: duplicate column names")
        _check(bool(self.roles), f"{self.dataset_id}: roles are required")

        sql = self.sql
        _check("%(" not in sql, f"{self.dataset_id}: named %(...)s placeholders are not allowed")
        _check(";" not in sql, f"{self.dataset_id}: one statement only")
        slots = _slots(sql)
        _check(len(slots) == len(set(slots)), f"{self.dataset_id}: a slot appears twice")
        tokens = self.sql_params
        _check(tokens.count(TOKEN_LIMIT) == 1 and tokens[-1] == TOKEN_LIMIT,
               f"{self.dataset_id}: {TOKEN_LIMIT} must appear exactly once, last")
        _check(re.search(r"LIMIT\s+%s\s*$", sql) is not None,
               f"{self.dataset_id}: SQL must end with 'LIMIT %s' (bound to row_limit + 1)")
        for token in tokens:
            if token.startswith("@"):
                _check(token in (TOKEN_CRITERIA, TOKEN_SCOPE, TOKEN_LIMIT),
                       f"{self.dataset_id}: unknown token {token!r}")
        plain = [t for t in tokens if not t.startswith("@")]
        _check(sql.replace("%%", "").count("%s") == len(plain) + 1,
               f"{self.dataset_id}: %s count does not match sql_params")
        _check(set(plain) <= set(self.parameters),
               f"{self.dataset_id}: sql_params use undeclared parameters "
               f"{sorted(set(plain) - set(self.parameters))}")

        # Access before retrieval: exactly one declared mechanism.
        by_criteria = self.criteria_param is not None
        by_scope = self.scope_column is not None
        by_reason = bool(self.unscoped_reason and self.unscoped_reason.strip())
        _check(by_criteria + by_scope + by_reason == 1,
               f"{self.dataset_id}: declare exactly one of criteria_param, "
               "scope_column or unscoped_reason")
        if by_criteria:
            _check(self.criteria_param in self.parameters,
                   f"{self.dataset_id}: criteria_param must be one of its parameters")
            _check("where" in slots and tokens.count(TOKEN_CRITERIA) == 1,
                   f"{self.dataset_id}: criteria datasets need {{where}} and {TOKEN_CRITERIA}")
            _check("scope" not in slots and TOKEN_SCOPE not in tokens,
                   f"{self.dataset_id}: the compiler already applies the access scope")
        else:
            _check("where" not in slots and "order" not in slots
                   and TOKEN_CRITERIA not in tokens,
                   f"{self.dataset_id}: {{where}}/{{order}} require criteria_param")
        if by_scope:
            _check(bool(re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", self.scope_column)),
                   f"{self.dataset_id}: scope_column must be alias.column")
            _check("scope" in slots and tokens.count(TOKEN_SCOPE) == 1,
                   f"{self.dataset_id}: scope datasets need {{scope}} and {TOKEN_SCOPE}")
        else:
            _check("scope" not in slots and TOKEN_SCOPE not in tokens,
                   f"{self.dataset_id}: {{scope}} requires scope_column")
        if self.semantics in ("capped", "top_n"):
            _check(re.search(r"ORDER\s+BY", sql, re.IGNORECASE) is not None,
                   f"{self.dataset_id}: {self.semantics} results must be ordered "
                   "(which rows survive the limit must be defined)")
            if "order" not in slots:
                # A static ORDER BY must end in a unique tie-breaker; we can
                # only check that one is written down, the PG test checks it
                # is deterministic.
                pass

    @property
    def key(self) -> str:
        return f"{self.dataset_id}@{self.version}"

    def semantic(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id, "version": self.version,
            "unit": self.unit, "semantics": self.semantics,
            "row_limit": self.row_limit,
            "columns": [c.semantic() for c in self.columns],
            "sql": " ".join(self.sql.split()),
            "sql_params": list(self.sql_params),
            "roles": sorted(self.roles), "parameters": sorted(self.parameters),
            "criteria_param": self.criteria_param,
            "scope_column": self.scope_column,
            "unscoped": bool(self.unscoped_reason),
        }

    def fingerprint(self) -> str:
        return sha256_hex(self.semantic())

    def bind(self, values: Mapping[str, Any], scope) -> BoundQuery:
        """SQL and parameters for already-normalised ``values`` and the
        caller's ``AccessScope``. Pure: executes nothing."""
        from core.criteria.compiler import AccessScope, compile_criteria

        if not isinstance(scope, AccessScope):
            raise ReportDefinitionError(
                f"{self.dataset_id}: an AccessScope is required to bind a dataset")
        fills: Dict[str, str] = {}
        slot_params: Dict[str, Tuple[Any, ...]] = {}
        criteria_fp = None
        notes: Tuple[str, ...] = ()
        if self.criteria_param is not None:
            raw = values.get(self.criteria_param)
            if raw is None:
                raise ReportParameterError(f"{self.criteria_param} is required")
            compiled = compile_criteria(criteria_from_dict(dict(raw)), scope)
            fills["where"] = compiled.where_sql
            fills["order"] = compiled.order_sql
            slot_params[TOKEN_CRITERIA] = tuple(compiled.params)
            criteria_fp = compiled.criteria_fingerprint
            notes = compiled.notes
        if self.scope_column is not None:
            if scope.allowed_source_ids is None:
                fills["scope"] = "TRUE"
                slot_params[TOKEN_SCOPE] = ()
            elif not scope.allowed_source_ids:
                fills["scope"] = "FALSE"
                slot_params[TOKEN_SCOPE] = ()
            else:
                fills["scope"] = f"{self.scope_column} = ANY(%s)"
                slot_params[TOKEN_SCOPE] = (list(scope.allowed_source_ids),)
        sql = self.sql.format(**fills) if fills else self.sql
        params: List[Any] = []
        for token in self.sql_params:
            if token == TOKEN_LIMIT:
                params.append(self.row_limit + 1)
            elif token in slot_params:
                params.extend(slot_params[token])
            else:
                params.append(values.get(token))
        return BoundQuery(sql=" ".join(sql.split()), params=tuple(params),
                          fetch_limit=self.row_limit + 1,
                          criteria_fingerprint=criteria_fp, notes=notes)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportDefinition:
    report_id: str
    version: int
    title: str                     # msgid
    description: str               # msgid
    help_topic: str
    unit: str
    roles: Tuple[str, ...]
    parameters: Tuple[Parameter, ...]
    datasets: Tuple[str, ...]      # dataset keys "id@version"
    status: str = "active"         # active | superseded

    def __post_init__(self) -> None:
        _check(bool(_ID.match(self.report_id or "")) and "." not in self.report_id,
               f"report id {self.report_id!r} must be snake_case")
        _check(isinstance(self.version, int) and self.version >= 1,
               f"{self.report_id}: version must be an integer >= 1")
        _msgid(self.title, f"{self.report_id} title")
        _msgid(self.description, f"{self.report_id} description")
        _check(bool(_HELP_TOPIC.match(self.help_topic or "")),
               f"{self.report_id}: help topic {self.help_topic!r} must look like 'reports/name'")
        _check(self.unit in UNITS, f"{self.report_id}: unit {self.unit!r} is not one of {UNITS}")
        _check(bool(self.roles) and len(set(self.roles)) == len(self.roles),
               f"{self.report_id}: roles are required and must be distinct")
        names = [p.name for p in self.parameters]
        _check(len(names) == len(set(names)), f"{self.report_id}: duplicate parameter names")
        _check(bool(self.datasets) and len(set(self.datasets)) == len(self.datasets),
               f"{self.report_id}: datasets are required and must be distinct")
        for key in self.datasets:
            _check(bool(re.fullmatch(r"[a-z0-9_.]+@\d+", key)),
                   f"{self.report_id}: dataset reference {key!r} must be 'id@version'")
        _check(self.status in ("active", "superseded"),
               f"{self.report_id}: status must be active or superseded")

    @property
    def key(self) -> str:
        return f"{self.report_id}@{self.version}"

    def parameter(self, name: str) -> Optional[Parameter]:
        return next((p for p in self.parameters if p.name == name), None)

    def semantic(self, dataset_fingerprints: Mapping[str, str]) -> Dict[str, Any]:
        return {
            "report_id": self.report_id, "version": self.version,
            "unit": self.unit, "roles": sorted(self.roles),
            "parameters": [p.semantic() for p in self.parameters],
            "datasets": [[key, dataset_fingerprints.get(key)] for key in self.datasets],
        }

    def fingerprint(self, dataset_fingerprints: Mapping[str, str]) -> str:
        return sha256_hex(self.semantic(dataset_fingerprints))

    def normalize_parameters(self, supplied: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Validate every supplied value; unknown names are refused, never
        ignored (a typo must not silently run the unfiltered report)."""
        supplied = dict(supplied or {})
        unknown = sorted(set(supplied) - {p.name for p in self.parameters})
        if unknown:
            raise ReportParameterError(f"unknown parameter(s): {', '.join(unknown)}")
        return {p.name: p.normalize(supplied.get(p.name)) for p in self.parameters}

    def translation_keys(self) -> Tuple[str, ...]:
        return (self.title, self.description) + tuple(p.label for p in self.parameters)


@dataclass(frozen=True)
class HelpTopic:
    topic: str
    title: str                     # msgid
    summary: str                   # msgid

    def __post_init__(self) -> None:
        _check(bool(_HELP_TOPIC.match(self.topic or "")),
               f"help topic {self.topic!r} must look like 'reports/name'")
        _msgid(self.title, f"help topic {self.topic} title")
        _msgid(self.summary, f"help topic {self.topic} summary")
