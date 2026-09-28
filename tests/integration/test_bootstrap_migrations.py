"""Integration tests: schema bootstrap + migrations (DB-01, DB-02, Gate 2).

These run against a disposable PostgreSQL database (see conftest.py).
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_conn(pg_db):
    import psycopg2

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield conn
    conn.rollback()
    conn.close()


class TestBootstrap:
    def test_all_migrations_applied(self, db_conn):
        from database.migration_runner import current_version, discover_migrations, applied_versions

        migrations = discover_migrations()
        done = set(applied_versions(db_conn))
        assert all(m.version in done for m in migrations)
        assert current_version(db_conn) == migrations[-1].version

    def test_required_tables_exist(self, db_conn):
        expected = {
            "words", "punctuation", "categorys", "words_categorys", "sides",
            "sources", "hashs", "hash_contexts", "paths", "contents", "contents_raw",
            "titles_content", "keywords", "words_hashs", "keywords_hashs", "alerts",
            "users", "sessions", "audit_log", "schema_migrations",
            "translation_overrides",
            # m0016: saved searches moved from data/saved_searches.json
            "saved_searches", "saved_search_imports",
            # m0017: Phase 1 temporal signals, keyed on content (hash_id)
            "content_signals", "content_signal_runs",
            # m0020 / m0021: monitoring rules and scenarios
            "monitoring_rules", "monitoring_rule_versions", "rule_evaluations",
            "rule_subject_ledger", "scenarios", "scenario_versions",
            "scenario_evaluations", "scenario_outcomes",
        }
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public'"
            )
            actual = {row[0] for row in cur.fetchall()}
        missing = expected - actual
        assert not missing, f"Missing tables: {missing}"

    def test_required_indexes_exist(self, db_conn):
        """DB-06: the performance indexes exist after bootstrap."""
        # Content-identity schema (m0011): content-keyed uniques and the
        # context/lineage lookups. The old path-keyed and (hash,source,side)
        # names are gone by design.
        expected = {
            "hashs_hash_key",
            "uq_hash_contexts_identity",
            "uq_words_hashs_hash_word",
            "uq_keywords_hashs_hash_keyword",
            "uq_contents_raw_hash_chunk",
            "idx_words_hashs_word_id",
            "idx_paths_context_id",
            "idx_paths_file_name",
            "idx_paths_file_path",
            "idx_paths_parent_path_id",
            "idx_titles_content_hash_id",
            # m0016
            "idx_saved_searches_owner_created",
            "idx_saved_searches_fingerprint",
            "idx_saved_searches_monitor",
            "uq_saved_searches_legacy",
            "uq_saved_search_imports_sha",
            # m0017
            "uq_content_signals_dedup",
            "idx_content_signals_hash",
            "idx_content_signals_dates",
            "idx_content_signals_type",
            "idx_content_signal_runs_version",
            # m0018
            "idx_content_signals_confidence",
            # m0019
            "uq_geo_places_key",
            "uq_geo_place_names",
            "idx_geo_place_names_key",
            "idx_geo_place_names_place",
            "content_signal_places_pkey",
            "idx_content_signal_places_place",
            "idx_content_signals_detector",
            # m0020
            "uq_monitoring_rules_owner_name",
            "idx_monitoring_rules_active",
            "idx_monitoring_rules_saved_search",
            "monitoring_rule_versions_pkey",
            "idx_rule_evaluations_rule",
            "rule_subject_ledger_pkey",
            "idx_rule_ledger_pending",
            "idx_rule_ledger_alert",
            "idx_alerts_recipient_dismissed_created",
            # m0021
            "uq_scenarios_owner_name",
            "idx_scenarios_active",
            "scenario_versions_pkey",
            "idx_scenario_evaluations_scenario",
            "idx_scenario_outcomes_latest",
            "idx_scenario_outcomes_evaluation",
        }
        with db_conn.cursor() as cur:
            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            actual = {row[0] for row in cur.fetchall()}
        missing = expected - actual
        assert not missing, f"Missing indexes: {missing}"

    def test_pg_trgm_not_required(self, db_conn):
        """DB-07 Option B: application runs without pg_trgm."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            assert cur.fetchone() is None

    def test_fresh_database_bootstrap_is_idempotent(self, pg_db):
        """Gate 2: bootstrap on an initialized database succeeds (no-op)."""
        from database.bootstrap import bootstrap_database

        report = bootstrap_database(pg_db)
        assert report["database_created"] is False
        assert report["applied_migrations"] == []

    def test_fresh_database_creation(self, pg_server, pg_db):
        """Gate 2: bootstrap against a brand new database name creates it."""
        import os as _os

        from database.bootstrap import bootstrap_database

        cfg = dict(pg_db)
        cfg["database"] = f"fresh_boot_{_os.getpid()}"
        report = bootstrap_database(cfg)
        assert report["database_created"] is True
        assert report["applied_migrations"], "migrations must run on fresh DB"
