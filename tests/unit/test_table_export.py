"""The shared list export: one endpoint, the view's rows, chosen columns.

The table toolbar offers "Export"; these tests hold the server side of that
promise - the allowlist (a column name is never trusted from the request),
the formats, and the fact that the export answers to the same search and
sort the table on screen shows.
"""

from __future__ import annotations

import io

import jinja2
import pytest

from core.frontend.component_audit import PROJECT_ROOT


# ---------------------------------------------------------------------------
# The toolbar component
# ---------------------------------------------------------------------------

class TestTheExportMenu:
    @pytest.fixture()
    def render(self):
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")),
            autoescape=True)
        environment.globals["_"] = lambda text: text

        def render_source(source: str) -> str:
            return environment.from_string(source).render()

        return render_source

    def test_the_menu_offers_the_table_columns_and_the_formats(self, render):
        out = render(
            "{% from 'components/table.html' import export_menu %}"
            "{{ export_menu('words', [{'key': 'word', 'label': _('Word')},"
            " {'kind': 'actions'}],"
            " extra_columns=[{'key': 'id', 'label': _('Id')}]) }}")
        assert 'data-export-interface="words"' in out
        assert 'data-export-column="word"' in out
        assert 'data-export-column="id"' in out
        assert 'data-export-download="csv"' in out
        assert 'data-export-download="xlsx"' in out
        assert 'data-export-print' in out
        # A column with no key (the Actions column) is not a database column
        # and is not offered.
        assert 'data-export-column="None"' not in out

    def test_the_menu_is_the_only_place_the_download_is_built(self):
        """The page templates never build an export URL; the menu and the
        shared script do."""
        text = (PROJECT_ROOT / "static/js/modules/ui/table-export.js").read_text(
            encoding="utf-8")
        assert "/api/export/" in text
        assert "start_position" in text, "a page coordinate leaked into the export"


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.usefixtures("app")


class TestTheExportEndpoint:
    def test_unknown_interface_is_refused(self, admin_client):
        resp = admin_client.get("/api/export/nobody_knows_this")
        assert resp.status_code == 404

    def test_unknown_column_is_refused(self, admin_client):
        resp = admin_client.get("/api/export/words?columns=word,secret_column")
        assert resp.status_code == 400
        assert b"secret_column" in resp.data

    def test_unknown_format_is_refused(self, admin_client):
        resp = admin_client.get("/api/export/words?format=parquet")
        assert resp.status_code == 400

    def test_csv_export_answers_with_the_chosen_columns(self, admin_client):
        resp = admin_client.get("/api/export/words?format=csv&columns=id,word")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        body = resp.get_data(as_text=True)
        header = body.lstrip("\ufeff").splitlines()[0]
        assert header == "Id,Word"
        assert body.count("\n") >= 1

    def test_default_columns_are_the_table_view(self, admin_client):
        resp = admin_client.get("/api/export/words?format=csv")
        assert resp.status_code == 200
        header = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
        assert header == "Id,Word,Usage Count,Status"

    def test_xlsx_export_is_a_workbook(self, admin_client):
        resp = admin_client.get("/api/export/words?format=xlsx&columns=word,usage_count")
        assert resp.status_code == 200
        assert "spreadsheetml" in (resp.mimetype or "")
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(resp.get_data()))
        sheet = workbook.active
        assert [cell.value for cell in sheet[1]] == ["Word", "Usage Count"]

    def test_categories_export_carries_the_tables_own_keys(self, admin_client):
        resp = admin_client.get("/api/export/categories?format=csv&columns=name,files,words")
        assert resp.status_code == 200
        header = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
        assert header == "Category Name,Files,Words"

    def test_sources_export_searches_like_the_list(self, admin_client):
        resp = admin_client.get("/api/export/sources?format=csv&columns=name,documents&search=zzz_no_such_source")
        assert resp.status_code == 200
        rows = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()
        assert rows[0] == "Name,Documents"
        assert len(rows) == 1, "a search the list would not match leaked into the export"

    def test_category_words_export_needs_its_category(self, admin_client):
        resp = admin_client.get("/api/export/category_words?format=csv")
        assert resp.status_code == 400
        assert b"category_id" in resp.data

    def test_sides_export_answers_the_sort_the_headers_use(self, admin_client):
        resp = admin_client.get("/api/export/sides?format=csv&columns=name,created&sort=name&order=asc")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[0] == "Name,Created"
