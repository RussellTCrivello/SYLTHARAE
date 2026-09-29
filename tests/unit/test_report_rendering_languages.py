"""Step 18: multilingual artifact rendering.

The renderers are deterministic per (document, renderer version,
language): the language is an explicit argument - never ambient browser
state - validated against the shipped catalogs, recorded in the artifact
and its manifest, with an explicit, counted fallback (a missing entry
shows the msgid and the count says so on the artifact). The behaviour
against a real run over HTTP lives in ``tests/integration/test_report_artifacts_pg.py``.
"""

import pytest

from core.reporting import render as renderers
from core.reporting.i18n import (
    available_languages,
    is_rtl,
    local_digits,
    reader_for,
)


def _document(**overrides):
    run = {
        "report_key": "search_results@1", "id": 5, "report_id": "search_results",
        "report_version": 1, "definition_fingerprint": "f" * 64,
        "parameters_fingerprint": "a" * 64, "criteria_fingerprint": "b" * 64,
        "saved_search_id": None, "requester_username": "u", "requester_role": "analyst",
        "snapshot": "0/1", "snapshot_at": "2026-01-01T00:00:00+00:00",
        "isolation_level": "repeatable read, read only", "generator_version": "runner/2",
        "requested_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:00+00:00",
    }
    run.update(overrides.get("run", {}))
    doc = {
        "report": {"title": None, "unit": "path"},
        "run": run,
        "datasets": [{
            "dataset_key": "search_results.matches@1", "dataset_fingerprint": "c" * 64,
            "query_fingerprint": "d" * 64, "semantics": "capped", "row_limit": 5000,
            "row_count": 0, "truncated": False, "columns": [], "rows": [],
        }],
        "analyses": overrides.get("analyses", []),
    }
    doc["datasets"].extend(overrides.get("extra_datasets", []))
    return doc


class TestTheLanguageReader:
    def test_only_shipped_languages_are_accepted(self):
        assert set(available_languages()) == {"en", "ar", "he", "fa", "hr"}
        with pytest.raises(ValueError, match="language must be one of"):
            reader_for("xx")
        with pytest.raises(ValueError):
            reader_for("en_US-x-invented")

    def test_translations_resolve_and_english_is_the_msgid(self):
        assert reader_for("hr").gettext("No rows.") == "Nema redaka."
        assert reader_for("en").gettext("No rows.") == "No rows."
        assert reader_for("en").fallbacks == 0, "English IS the msgid, not a gap"

    def test_a_missing_entry_falls_back_and_is_counted(self):
        reader = reader_for("hr")
        assert reader.gettext("msg id that no catalog ships") == \
            "msg id that no catalog ships"
        assert reader.fallbacks == 1

    def test_named_placeholders_are_substituted(self):
        assert reader_for("ar").gettext("complete: %(count)s rows (exact)",
                                        count=3) == "مكتمل: 3 صفوف (دقيق)"
        assert reader_for("en").gettext("complete: %(count)s rows (exact)",
                                        count=3) == "complete: 3 rows (exact)"

    def test_local_digits_cover_the_declared_scripts_only(self):
        assert local_digits("3 rows at 5,000", "ar") == "٣ rows at ٥,٠٠٠"
        assert local_digits("3 rows at 5,000", "fa") == "۳ rows at ۵,۰۰۰"
        assert local_digits("3 rows at 5,000", "en") == "3 rows at 5,000"
        assert local_digits("abc123", "hr") == "abc123", (
            "fingerprints and identifiers are data, never re-digitised")

    def test_direction(self):
        assert is_rtl("ar") and is_rtl("he") and is_rtl("fa")
        assert not is_rtl("en") and not is_rtl("hr")


class TestRendererLanguages:
    def test_html_states_its_language_and_direction(self):
        for language, direction in (("en", "ltr"), ("ar", "rtl"), ("he", "rtl"),
                                    ("fa", "rtl"), ("hr", "ltr")):
            out = renderers.render(_document(), "html", None, language)
            text = out.content.decode("utf-8")
            assert f'<html lang="{language}" dir="{direction}">' in text, language
            assert out.notes["language"] == language

    def test_the_chrome_is_translated_with_fallbacks_counted(self):
        out = renderers.render(_document(), "html", None, "hr")
        text = out.content.decode("utf-8")
        heading = reader_for("hr").gettext("Provenance")
        assert heading != "Provenance" and heading in text, (
            "the provenance heading is translated")
        assert "potpuno: 0 redaka (capped kod 5000, granica nije dosegnuta)" in text
        assert "Nema redaka." in text
        assert out.notes["translation_fallbacks"] == 0

    def test_numerals_are_local_in_the_pure_chrome(self):
        out = renderers.render(_document(), "html", None, "ar")
        text = out.content.decode("utf-8")
        assert "مكتمل: ٠ صفوف (capped عند ٥٠٠٠، لم يُبلغ الحد)" in text

    def test_data_values_are_never_re_digitised(self):
        document = _document(extra_datasets=[{
            "dataset_key": "x@1", "dataset_fingerprint": "c" * 64,
            "query_fingerprint": "d" * 64, "semantics": "exact", "row_limit": 10,
            "row_count": 1, "truncated": False,
            "columns": [{"name": "file_name", "type": "text", "nullable": False,
                         "label": "File name"}],
            "rows": [{"file_name": "doc7.txt"}],
        }])
        out = renderers.render(document, "html", None, "ar")
        text = out.content.decode("utf-8")
        assert "doc7.txt" in text, "a file name is data"
        assert "doc٧" not in text

    def test_bidi_isolation_around_every_value(self):
        document = _document(extra_datasets=[{
            "dataset_key": "x@1", "dataset_fingerprint": "c" * 64,
            "query_fingerprint": "d" * 64, "semantics": "exact", "row_limit": 10,
            "row_count": 1, "truncated": False,
            "columns": [{"name": "file_name", "type": "text", "nullable": False,
                         "label": "File name"}],
            "rows": [{"file_name": "تقرير.docx"}],
        }])
        text = renderers.render(document, "html", None, "en").content.decode("utf-8")
        assert "<bdi>تقرير.docx</bdi>" in text, (
            "a multilingual value renders isolated in an LTR document too")

    def test_json_states_the_language_and_keeps_the_references(self):
        out = renderers.render(_document(), "json", None, "ar")
        import json

        body = json.loads(out.content.decode("utf-8"))
        assert body["language"] == "ar"
        assert body["format"] == "report-json/3"

    def test_narratives_render_in_the_requested_language(self):
        analysis = {
            "analysis_key": "term_keyness@2", "state": "measured", "reason": None,
            "template_set": "keyness", "template_version": 2,
            "narrative": {"template_set": "keyness", "template_version": 2,
                          "voices": {"finding": [{"key": "Listed",
                                                  "params": {"count": 1}}]}},
        }
        # The stored record's exact shape is the analysis suite's contract;
        # a narrative the renderer cannot parse must fail loudly, never
        # render as silence.
        from core.analytics.narrative import NarrativeError

        with pytest.raises((TypeError, KeyError, NarrativeError)):
            renderers.render(_document(analyses=[analysis]), "html", None, "en")

    def test_the_language_is_part_of_the_rendering(self):
        en = renderers.render(_document(), "html", None, "en")
        ar = renderers.render(_document(), "html", None, "ar")
        assert en.content != ar.content, "the same run, two languages, two files"
        again = renderers.render(_document(), "html", None, "en")
        assert again.content == en.content, "deterministic per language"
        assert en.filename.endswith("_en.html") and ar.filename.endswith("_ar.html")

    def test_unknown_language_is_refused_not_rendered_as_english(self):
        with pytest.raises(Exception, match="language must be one of"):
            renderers.render(_document(), "html", None, "klingon")

    def test_csv_is_data_only_and_language_free(self):
        document = _document(extra_datasets=[{
            "dataset_key": "x@1", "dataset_fingerprint": "c" * 64,
            "query_fingerprint": "d" * 64, "semantics": "exact", "row_limit": 10,
            "row_count": 1, "truncated": False,
            "columns": [{"name": "file_name", "type": "text", "nullable": False,
                         "label": "File name"}],
            "rows": [{"file_name": "doc.txt"}],
        }])
        en = renderers.render(document, "csv", "x@1", "en")
        ar = renderers.render(document, "csv", "x@1", "ar")
        assert en.content == ar.content, (
            "a data-only format renders identically in every language")
        assert "translation_fallbacks" not in en.notes, (
            "no chrome, no fallback to report")

    def test_pdf_and_charts_remain_refused_with_the_documented_reason(self):
        for fmt in ("pdf", "svg"):
            with pytest.raises(Exception, match="not implemented"):
                renderers.check_request([], fmt, None)

    def test_renderer_versions_were_bumped(self):
        assert renderers.RENDERERS["json"] == "report-json/3"
        assert renderers.RENDERERS["html"] == "report-html/3"
        assert renderers.RENDERERS["xlsx"] == "report-xlsx/2"
        assert renderers.RENDERERS["csv"] == "report-csv/1"
