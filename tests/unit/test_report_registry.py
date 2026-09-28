"""Step 13: the report registry validates what it registers.

Every check in ``ReportRegistry.validate*`` and every construction guard in
``core.reporting.model`` has a negative control here that must trip it; the
first tests prove the shipped registry passes all of them.
"""

import dataclasses
import json
import os
import shutil

import pytest

from core.criteria.compiler import AccessScope
from core.reporting import (
    REGISTRY,
    Column,
    Dataset,
    HelpTopic,
    Parameter,
    ReportDefinition,
    ReportDefinitionError,
    ReportNotFound,
    ReportParameterError,
    ReportRegistry,
)
from core.reporting import lock as lock_cli
from core.reporting.registry import read_lock

# ---------------------------------------------------------------- helpers

def _ds(**changes):
    base = dict(
        dataset_id="t.rows", version=1, description="test rows", unit="path",
        semantics="capped", row_limit=10,
        columns=(Column("path_id", "integer", False, "File ID"),),
        sql="SELECT p.id AS path_id FROM paths p WHERE {scope} AND p.file_type = %s "
            "ORDER BY p.id LIMIT %s",
        sql_params=("@scope", "kind", "@limit"), roles=("admin", "analyst", "viewer"),
        parameters=("kind",), scope_column="p.source_id")
    base.update(changes)
    return Dataset(**base)


def _report(**changes):
    base = dict(
        report_id="t_report", version=1, title="Search Results Report",
        description="Search criteria", help_topic="reports/t-report", unit="path",
        roles=("admin", "analyst", "viewer"),
        parameters=(Parameter("kind", "enum", "File type", choices=("pdf", "txt")),),
        datasets=("t.rows@1",))
    base.update(changes)
    return ReportDefinition(**base)


def _topic(topic="reports/t-report"):
    return HelpTopic(topic, "Search Results Report", "Search criteria")


def _registry(reports=None, datasets=None, topics=None):
    return ReportRegistry(reports=tuple(reports or (_report(),)),
                          datasets=tuple(datasets or (_ds(),)),
                          help_topics=tuple(topics or (_topic(),)))


# ---------------------------------------------------------------- shipped registry

def test_the_shipped_registry_is_structurally_valid():
    assert REGISTRY.reports, "guard: an empty registry validates trivially"
    assert REGISTRY.validate() == []


def test_every_shipped_string_is_translated_in_every_catalog():
    assert REGISTRY.translation_keys()
    assert REGISTRY.validate_translations() == []


def test_every_shipped_definition_matches_its_pinned_fingerprint():
    assert REGISTRY.validate_lock() == []
    pinned = read_lock()
    assert set(pinned["reports"]) == {r.key for r in REGISTRY.reports}
    assert set(pinned["datasets"]) == {d.key for d in REGISTRY.datasets}


def test_lock_check_cli_passes(capsys):
    assert lock_cli.main(["--check"]) == 0
    assert "0 problem(s)" in capsys.readouterr().out


def test_every_report_declares_a_unit_and_every_dataset_a_limit_meaning():
    for r in REGISTRY.reports:
        assert r.unit
        for key in r.datasets:
            ds = REGISTRY.dataset(key)
            assert ds.semantics in ("exact", "capped", "top_n")
            assert ds.sql.rstrip().endswith("LIMIT %s")


def test_lookup_and_role_visibility():
    assert REGISTRY.report("search_results").key == "search_results@1"
    assert REGISTRY.report("search_results", 1).version == 1
    with pytest.raises(ReportNotFound):
        REGISTRY.report("search_results", 99)
    with pytest.raises(ReportNotFound):
        REGISTRY.report("nope")
    assert REGISTRY.visible_to(None) == ()
    assert REGISTRY.visible_to("viewer")
    reg = _registry(reports=[_report(roles=("admin",))],
                    datasets=[_ds(roles=("admin",))])
    assert reg.visible_to("viewer") == () and len(reg.visible_to("admin")) == 1


# ---------------------------------------------------------------- validate(): negative controls

def test_valid_fixture_registry_has_no_problems():
    assert _registry().validate() == []


@pytest.mark.parametrize("build, expected", [
    (lambda: _registry(reports=[_report(), _report()]), "duplicate report t_report@1"),
    (lambda: _registry(datasets=[_ds(), _ds()]), "duplicate dataset t.rows@1"),
    (lambda: _registry(topics=[_topic(), _topic()]), "duplicate help topic"),
    (lambda: _registry(reports=[_report(), _report(version=2)]),
     "more than one active version"),
    (lambda: _registry(reports=[_report(version=2)]), "not contiguous"),
    (lambda: _registry(datasets=[_ds(version=2)], reports=[_report(datasets=("t.rows@2",))]),
     "dataset versions [2] are not contiguous"),
    (lambda: _registry(reports=[_report(datasets=("t.rows@1", "t.none@1"))]),
     "dataset 't.none@1' is not registered"),
    (lambda: _registry(reports=[_report(roles=("admin", "root"))]), "unknown roles ['root']"),
    (lambda: _registry(datasets=[_ds(roles=("admin", "guest"))],
                       reports=[_report(roles=("admin",))]), "unknown roles ['guest']"),
    (lambda: _registry(datasets=[_ds(roles=("admin",))]),
     "may run the report but may not read dataset"),
    (lambda: _registry(reports=[_report(help_topic="reports/other")]),
     "help topic 'reports/other' is not declared"),
    (lambda: _registry(topics=[_topic(), _topic("reports/orphan")]),
     "help topic 'reports/orphan' is used by no report"),
    (lambda: _registry(reports=[_report(unit="hash")]), "differs from its primary"),
    (lambda: _registry(reports=[_report(parameters=())]),
     "reads parameter 'kind' that the report does not declare"),
    (lambda: _registry(reports=[_report(parameters=(
        Parameter("kind", "enum", "File type", choices=("pdf", "txt")),
        Parameter("extra", "boolean", "File date", required=False, default=False)))]),
     "parameter 'extra' is read by no dataset"),
    (lambda: _registry(reports=[_report(parameters=(
        Parameter("kind", "criteria", "Search criteria"),))]),
     "criteria parameter 'kind' is bound as a plain value"),
    (lambda: _registry(datasets=[_ds(), _ds(dataset_id="t.unused")]),
     "dataset t.unused@1 is used by no report"),
])
def test_validate_reports_each_problem(build, expected):
    problems = build().validate()
    assert any(expected in p for p in problems), problems


def test_criteria_dataset_needs_a_criteria_parameter():
    crit = REGISTRY.dataset("search_results.matches@1")
    reg = _registry(datasets=[crit], reports=[_report(
        datasets=(crit.key,), parameters=(Parameter("criteria", "text", "Search criteria",
                                                    max_length=10),))])
    assert any("which is a text parameter" in p for p in reg.validate())


def test_validate_collects_every_problem_not_just_the_first():
    reg = _registry(reports=[_report(roles=("root",), help_topic="reports/zz")])
    problems = reg.validate()
    assert any("unknown roles" in p for p in problems)
    assert any("not declared" in p for p in problems)


# ---------------------------------------------------------------- translations

@pytest.fixture
def catalogs(tmp_path):
    src = os.path.join(os.path.dirname(__file__), "..", "..", "translations")
    dst = tmp_path / "translations"
    shutil.copytree(src, dst)
    return dst


def _edit_po(catalogs, lang, old, new):
    path = catalogs / lang / "LC_MESSAGES" / "messages.po"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_translations_missing_from_template_are_reported(catalogs):
    pot = catalogs / "messages.pot"
    pot.write_text(pot.read_text(encoding="utf-8").replace(
        'msgid "Search Results Report"', 'msgid "Search Results Report (gone)"'),
        encoding="utf-8")
    problems = REGISTRY.validate_translations(str(catalogs))
    assert "msgid 'Search Results Report' is missing from messages.pot" in problems


@pytest.mark.parametrize("lang, old, new, expected", [
    ("ar", 'msgstr "تقرير نتائج البحث"', 'msgstr ""', "untranslated"),
    ("he", 'msgstr "דוח תוצאות חיפוש"', 'msgstr "Search Results Report"', "identical"),
    ("hr", 'msgid "Search Results Report"', 'msgid "Search Results Report X"', "is missing"),
    ("fa", '#: core/reporting/definitions.py\nmsgid "Search Results Report"',
     '#: core/reporting/definitions.py\n#, fuzzy\nmsgid "Search Results Report"', "fuzzy"),
])
def test_translation_defects_are_reported(catalogs, lang, old, new, expected):
    _edit_po(catalogs, lang, old, new)
    problems = REGISTRY.validate_translations(str(catalogs))
    assert any(p.startswith(f"[{lang}]") and "Search Results Report" in p and expected in p
               for p in problems), problems


def test_placeholder_drift_is_reported(catalogs):
    reg = _registry(reports=[_report(title="Document %(id)s")])
    # "Document %(id)s" exists in every catalog (monitoring page) - break one.
    _edit_po(catalogs, "hr", 'msgid "Document %(id)s"\nmsgstr "Dokument %(id)s"',
             'msgid "Document %(id)s"\nmsgstr "Dokument %(ident)s"')
    problems = reg.validate_translations(str(catalogs))
    assert any("[hr] 'Document %(id)s' placeholders differ" == p for p in problems), problems


# ---------------------------------------------------------------- fingerprints and the lock

def test_fingerprint_is_stable_and_ignores_prose_and_labels():
    a = _ds()
    assert a.fingerprint() == _ds().fingerprint()
    assert a.fingerprint() == _ds(description="reworded").fingerprint()
    assert a.fingerprint() == _ds(columns=(Column("path_id", "integer", False,
                                                  "File name"),)).fingerprint()
    assert a.fingerprint() == _ds(sql=a.sql.replace(" ", "  ")).fingerprint()


@pytest.mark.parametrize("change", [
    dict(row_limit=11), dict(semantics="top_n"), dict(unit="hash"),
    dict(roles=("admin",)),
    dict(columns=(Column("path_id", "integer", True, "File ID"),)),
    dict(columns=(Column("path_id", "bigint", False, "File ID"),)),
    dict(sql="SELECT p.id AS path_id FROM paths p WHERE {scope} AND p.file_type = %s "
             "ORDER BY p.id DESC LIMIT %s"),
])
def test_fingerprint_changes_with_meaning(change):
    assert _ds().fingerprint() != _ds(**change).fingerprint()


def test_report_fingerprint_follows_its_datasets():
    fp1 = _report().fingerprint({"t.rows@1": _ds().fingerprint()})
    fp2 = _report().fingerprint({"t.rows@1": _ds(row_limit=11).fingerprint()})
    assert fp1 != fp2
    assert fp1 == _report(title="About the Search Results Report").fingerprint(
        {"t.rows@1": _ds().fingerprint()})


def test_editing_a_released_version_fails_the_lock():
    real = REGISTRY.dataset("search_results.matches@1")
    edited = dataclasses.replace(real, row_limit=real.row_limit + 1)
    reg = ReportRegistry(reports=REGISTRY.reports,
                         datasets=tuple(edited if d is real else d for d in REGISTRY.datasets),
                         help_topics=REGISTRY.help_topics)
    problems = reg.validate_lock()
    assert any("search_results.matches@1 changed meaning" in p for p in problems)
    # ...and the report that uses it changes meaning too.
    assert any("report search_results@1 changed meaning" in p for p in problems)


def test_removing_a_pinned_version_and_adding_an_unpinned_one_fail_the_lock():
    lock = read_lock()
    lock = {k: dict(v) for k, v in lock.items()}
    lock["reports"]["search_results@0"] = "0" * 64
    problems = REGISTRY.validate_lock(lock)
    assert any("search_results@0 is pinned but no longer registered" in p for p in problems)
    del lock["reports"]["search_results@1"]
    problems = REGISTRY.validate_lock(lock)
    assert any("search_results@1 is not pinned" in p for p in problems)


def test_lock_write_adds_but_never_rewrites(tmp_path, monkeypatch, capsys):
    path = tmp_path / "lock.json"
    tampered = read_lock()
    tampered["datasets"]["search_results.count@1"] = "f" * 64
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert lock_cli.write_new_entries(str(path)) == 1
    assert "refused" in capsys.readouterr().err
    assert json.loads(path.read_text())["datasets"]["search_results.count@1"] == "f" * 64

    fresh = tmp_path / "fresh.json"
    assert lock_cli.write_new_entries(str(fresh)) == 0
    assert json.loads(fresh.read_text()) == REGISTRY.fingerprints()


# ---------------------------------------------------------------- construction guards

@pytest.mark.parametrize("change, message", [
    (dict(dataset_id="Bad-Id"), "dotted snake_case"),
    (dict(version=0), "version"),
    (dict(description=" "), "description"),
    (dict(unit="documents"), "unit"),
    (dict(semantics="some"), "semantics"),
    (dict(row_limit=0), "row_limit"),
    (dict(row_limit=100_001), "row_limit"),
    (dict(columns=()), "at least one column"),
    (dict(columns=(Column("a", "text", False, "x"), Column("a", "text", False, "x"))),
     "duplicate column"),
    (dict(roles=()), "roles are required"),
    (dict(sql="SELECT 1 AS path_id FROM paths p WHERE {scope} AND p.file_type = %s "
              "ORDER BY 1"), "LIMIT %s"),
    (dict(sql="SELECT 1 FROM paths p WHERE {scope} AND x = %(kind)s ORDER BY 1 LIMIT %s"),
     "named"),
    (dict(sql="SELECT 1 FROM paths p WHERE {scope} AND p.file_type = %s ORDER BY 1; "
              "SELECT 1 LIMIT %s"), "one statement"),
    (dict(sql="SELECT 1 FROM {table} p WHERE {scope} AND p.file_type = %s ORDER BY 1 "
              "LIMIT %s"), "slot {table}"),
    (dict(sql="SELECT 1 FROM paths p WHERE {scope} AND p.file_type = %s AND 1 = %s "
              "ORDER BY 1 LIMIT %s"), "%s count"),
    (dict(sql_params=("@scope", "kind")), "@limit must appear exactly once, last"),
    (dict(sql_params=("@scope", "@limit", "kind")), "@limit must appear exactly once, last"),
    (dict(sql_params=("@scope", "other", "@limit")), "undeclared parameters"),
    (dict(sql_params=("@scope", "@bogus", "@limit")), "unknown token"),
    (dict(sql="SELECT p.id FROM paths p WHERE {scope} AND p.file_type = %s LIMIT %s"),
     "must be ordered"),
    (dict(scope_column=None), "exactly one of"),
    (dict(unscoped_reason="gazetteer only"), "exactly one of"),
    (dict(scope_column="source_id"), "alias.column"),
    (dict(sql="SELECT p.id FROM paths p WHERE p.file_type = %s ORDER BY p.id LIMIT %s",
          sql_params=("kind", "@limit")), "need {scope}"),
])
def test_dataset_construction_rejects(change, message):
    with pytest.raises(ReportDefinitionError) as info:
        _ds(**change)
    assert message in str(info.value), str(info.value)


def test_unscoped_dataset_needs_no_scope_slot_but_a_reason():
    ds = _ds(sql="SELECT 1 AS path_id FROM paths p WHERE p.file_type = %s ORDER BY 1 "
                 "LIMIT %s", sql_params=("kind", "@limit"), scope_column=None,
             unscoped_reason="reads no source-scoped rows")
    assert ds.bind({"kind": "pdf"}, AccessScope()).params == ("pdf", 11)
    with pytest.raises(ReportDefinitionError, match="requires scope_column"):
        _ds(sql_params=("@scope", "kind", "@limit"), scope_column=None,
            unscoped_reason="x")


def test_criteria_datasets_cannot_add_their_own_scope_or_skip_where():
    crit = REGISTRY.dataset("search_results.matches@1")
    with pytest.raises(ReportDefinitionError, match="already applies the access scope"):
        dataclasses.replace(crit, sql=crit.sql.replace("WHERE {where}",
                                                       "WHERE {where} AND {scope}"),
                            sql_params=("@criteria", "@scope", "@limit"))
    with pytest.raises(ReportDefinitionError, match="need {where}"):
        dataclasses.replace(crit, sql="SELECT p.id FROM paths p ORDER BY p.id LIMIT %s",
                            sql_params=("@limit",))
    with pytest.raises(ReportDefinitionError, match="require criteria_param"):
        _ds(sql="SELECT p.id FROM paths p WHERE {scope} AND {where} AND p.file_type = %s "
                "ORDER BY p.id LIMIT %s")


@pytest.mark.parametrize("change, message", [
    (dict(report_id="bad.id"), "snake_case"),
    (dict(title=""), "title"),
    (dict(title=" padded"), "title"),
    (dict(help_topic="search results"), "help topic"),
    (dict(unit="rows"), "unit"),
    (dict(roles=()), "roles"),
    (dict(roles=("admin", "admin")), "distinct"),
    (dict(datasets=()), "datasets"),
    (dict(datasets=("t.rows",)), "id@version"),
    (dict(status="draft"), "status"),
    (dict(parameters=(Parameter("kind", "boolean", "x", required=False),
                      Parameter("kind", "boolean", "x", required=False))), "duplicate"),
])
def test_report_construction_rejects(change, message):
    with pytest.raises(ReportDefinitionError) as info:
        _report(**change)
    assert message in str(info.value)


@pytest.mark.parametrize("kwargs, message", [
    (dict(name="Bad", type="text", label="x", max_length=5), "snake_case"),
    (dict(name="a", type="float", label="x"), "type"),
    (dict(name="a", type="enum", label="x", choices=("one",)), "two or more"),
    (dict(name="a", type="text", label="x", choices=("a", "b"), max_length=3), "enum only"),
    (dict(name="a", type="text", label="x"), "max_length"),
    (dict(name="a", type="date", label="x", minimum=1), "integer only"),
    (dict(name="a", type="integer", label="x", minimum=5, maximum=1), "exceeds"),
    (dict(name="a", type="integer", label="x", required=True, default=3), "cannot have a default"),
    (dict(name="a", type="integer", label="x", required=False, default=9, maximum=5),
     "invalid default"),
])
def test_parameter_construction_rejects(kwargs, message):
    with pytest.raises(ReportDefinitionError) as info:
        Parameter(**kwargs)
    assert message in str(info.value)


# ---------------------------------------------------------------- parameter values

def test_parameter_values_are_validated_and_normalised():
    report = REGISTRY.report("search_results")
    with pytest.raises(ReportParameterError, match="criteria is required"):
        report.normalize_parameters({})
    with pytest.raises(ReportParameterError, match="unknown parameter"):
        report.normalize_parameters({"criteria": {}, "critera": {}})
    with pytest.raises(ReportParameterError, match="criteria object"):
        report.normalize_parameters({"criteria": "text"})
    with pytest.raises(ReportParameterError, match="criteria:"):
        report.normalize_parameters({"criteria": {"date_field": "bogus"}})
    norm = report.normalize_parameters({"criteria": {"text": "alpha"}})
    assert norm["criteria"]["text"] == "alpha"
    assert norm == report.normalize_parameters({"criteria": norm["criteria"]})


@pytest.mark.parametrize("param, value, expected", [
    (Parameter("n", "integer", "x", minimum=1, maximum=5), "3", 3),
    (Parameter("n", "integer", "x", required=False, default=2), None, 2),
    (Parameter("d", "date", "x"), " 2026-02-03 ", "2026-02-03"),
    (Parameter("e", "enum", "x", choices=("a", "b")), "b", "b"),
    (Parameter("b", "boolean", "x"), False, False),
    (Parameter("t", "text", "x", max_length=3), "abc", "abc"),
    (Parameter("o", "text", "x", required=False, max_length=3), None, None),
])
def test_parameter_accepts(param, value, expected):
    assert param.normalize(value) == expected


@pytest.mark.parametrize("param, value", [
    (Parameter("n", "integer", "x", minimum=1), 0),
    (Parameter("n", "integer", "x", maximum=1), 2),
    (Parameter("n", "integer", "x"), True),
    (Parameter("n", "integer", "x"), "1.5"),
    (Parameter("d", "date", "x"), "2026-02-30"),
    (Parameter("d", "date", "x"), 20260101),
    (Parameter("e", "enum", "x", choices=("a", "b")), "c"),
    (Parameter("b", "boolean", "x"), "true"),
    (Parameter("t", "text", "x", max_length=3), "abcd"),
    (Parameter("t", "text", "x", max_length=3), 5),
])
def test_parameter_rejects(param, value):
    with pytest.raises(ReportParameterError):
        param.normalize(value)


# ---------------------------------------------------------------- binding

def test_bind_uses_the_compiler_and_binds_every_value():
    ds = REGISTRY.dataset("search_results.matches@1")
    hostile = "x'); DROP TABLE paths; --"
    values = REGISTRY.report("search_results").normalize_parameters(
        {"criteria": {"phrases": [hostile], "sources": [7]}})
    bound = ds.bind(values, AccessScope.unrestricted(user_id=1, role="viewer"))
    assert "DROP" not in bound.sql and "paths;" not in bound.sql
    # The value travels only as a bound parameter (the compiler encodes a
    # whole-word phrase as a regex, so match its tokens, not the spacing).
    assert any(isinstance(p, str) and "DROP" in p and "paths;" in p for p in bound.params)
    assert bound.params[-1] == ds.row_limit + 1 == bound.fetch_limit
    assert bound.sql.count("%s") == len(bound.params)
    assert "ORDER BY" in bound.sql and bound.sql.endswith("p.id ASC LIMIT %s")
    assert bound.criteria_fingerprint and len(bound.criteria_fingerprint) == 64
    assert "{" not in bound.sql


def test_bind_applies_the_access_scope_in_sql():
    ds = REGISTRY.dataset("search_results.count@1")
    values = {"criteria": {}}
    restricted = ds.bind(values, AccessScope(user_id=1, role="viewer",
                                             allowed_source_ids=(3, 4)))
    assert "hc.source_id IN (%s,%s)" in restricted.sql and restricted.params[:2] == (3, 4)
    nothing = ds.bind(values, AccessScope(user_id=1, role="viewer", allowed_source_ids=()))
    assert "1=0" in nothing.sql
    scoped = _ds()
    assert scoped.bind({"kind": "pdf"}, AccessScope()).sql.startswith(
        "SELECT p.id AS path_id FROM paths p WHERE TRUE")
    none = scoped.bind({"kind": "pdf"}, AccessScope(allowed_source_ids=()))
    assert "WHERE FALSE" in none.sql and none.params == ("pdf", 11)
    some = scoped.bind({"kind": "pdf"}, AccessScope(allowed_source_ids=(5,)))
    assert "p.source_id = ANY(%s)" in some.sql and some.params == ([5], "pdf", 11)


def test_bind_refuses_without_an_access_scope():
    with pytest.raises(ReportDefinitionError, match="AccessScope"):
        _ds().bind({"kind": "pdf"}, None)
    with pytest.raises(ReportParameterError):
        REGISTRY.dataset("search_results.count@1").bind({}, AccessScope())
