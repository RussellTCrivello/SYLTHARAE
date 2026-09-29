"""The per-user Latest/Change baseline store, against real PostgreSQL.

Covers migration 0027's schema and the behaviours the Latest/Change reports
depend on: per-reader isolation, monotonic advance (a stale client cannot
un-see rows), the absent-row shape of "never viewed", and the classification
that turns a baseline into the three states the report must distinguish.
"""

import datetime

import psycopg2
import pytest

from core.reporting.baselines import (
    baseline_key,
    classify_rows,
    get_baseline,
    record_baseline,
)


def _connect(pg_db):
    return psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                            user=pg_db["user"], password=pg_db["password"],
                            dbname=pg_db["database"])


def _user(pg_db, username):
    from core.security.service import get_auth_service
    auth = get_auth_service()
    existing = auth.get_user_by_username(username)
    if existing is not None:
        return existing.id
    return auth.create_user(username, "baseline-test-password-123",
                            role="admin").id


class TestTheBaselineStore:
    def test_the_table_exists_with_the_declared_shape(self, pg_db):
        conn = _connect(pg_db)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, is_nullable, data_type"
                " FROM information_schema.columns"
                " WHERE table_name = 'report_baselines'"
                " ORDER BY ordinal_position")
            columns = [(r[0], r[1], r[2]) for r in cur.fetchall()]
        conn.close()
        assert columns == [
            ("user_id", "NO", "integer"),
            ("criteria_hash", "NO", "character"),
            ("last_seen_at", "NO", "timestamp with time zone"),
            ("last_max_id", "NO", "bigint"),
        ]

    def test_a_reader_who_never_viewed_has_no_row_not_a_zero(self, pg_db):
        user_id = _user(pg_db, "baseline_never")
        key = baseline_key("f" * 64)
        assert get_baseline(user_id, key) is None, (
            '"never viewed" must be an absent row, never a zero measurement')

    def test_recording_creates_and_advances_one_row_per_view(self, pg_db):
        user_id = _user(pg_db, "baseline_advancer")
        key = baseline_key("a" * 64)
        first = record_baseline(user_id, key, 100)
        assert first["last_max_id"] == 100
        second = record_baseline(user_id, key, 250)
        assert second["last_max_id"] == 250
        conn = _connect(pg_db)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM report_baselines"
                        " WHERE user_id = %s AND criteria_hash = %s",
                        (user_id, key))
            assert cur.fetchone()[0] == 1, "one reader, one view, one row"
        conn.close()

    def test_a_stale_client_cannot_move_the_baseline_backwards(self, pg_db):
        user_id = _user(pg_db, "baseline_monotonic")
        key = baseline_key("b" * 64)
        record_baseline(user_id, key, 500)
        after = record_baseline(user_id, key, 200)
        assert after["last_max_id"] == 500, (
            "a stale second session must not make the reader un-see rows")

    def test_views_and_readers_are_independent(self, pg_db):
        user_a = _user(pg_db, "baseline_reader_a")
        user_b = _user(pg_db, "baseline_reader_b")
        view_one = baseline_key("c" * 64)
        view_two = baseline_key("d" * 64)
        record_baseline(user_a, view_one, 10)
        assert get_baseline(user_b, view_one) is None
        assert get_baseline(user_a, view_two) is None
        record_baseline(user_a, view_two, 20)
        assert get_baseline(user_a, view_one)["last_max_id"] == 10
        assert get_baseline(user_a, view_two)["last_max_id"] == 20

    def test_the_hash_rejects_non_hex_and_the_id_rejects_negatives(self, pg_db):
        user_id = _user(pg_db, "baseline_constraints")
        conn = _connect(pg_db)
        with conn.cursor() as cur:
            # One aborted statement poisons the transaction; each constraint
            # gets its own connection so both are genuinely exercised.
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO report_baselines (user_id, criteria_hash,"
                    " last_max_id) VALUES (%s, %s, 5)",
                    (user_id, "not-hex"))
        conn.close()
        conn = _connect(pg_db)
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO report_baselines (user_id, criteria_hash,"
                    " last_max_id) VALUES (%s, %s, -1)",
                    (user_id, "e" * 64))
        conn.close()

    def test_the_key_distinguishes_views_of_the_same_criteria(self):
        fingerprint = "0123abcd" * 8
        plain = baseline_key(fingerprint)
        assert plain == baseline_key(fingerprint), "deterministic"
        assert plain != baseline_key(fingerprint, {"page_size": 50}), (
            "a parameter that shapes the view is part of the identity")
        assert baseline_key(fingerprint, {"a": 1, "b": 2}) == \
            baseline_key(fingerprint, {"b": 2, "a": 1}), "order-insensitive"


class TestTheClassification:
    BASELINE = {"last_seen_at": None, "last_max_id": 100}

    def _row(self, path_id, created=None):
        return {"path_id": path_id, "date_creation": created}

    def test_without_a_baseline_everything_is_new(self):
        rows = classify_rows((self._row(1), self._row(5000)), None)
        assert all(r["view_state"] == "new_since_last_view" for r in rows)

    def test_rows_are_split_at_the_recorded_progress(self):
        rows = classify_rows((self._row(50), self._row(100), self._row(101)),
                             self.BASELINE)
        assert [r["view_state"] for r in rows] == [
            "previously_seen", "previously_seen", "new_since_last_view"]

    def test_recently_created_is_reported_alongside_not_instead(self):
        today = datetime.date.today()
        old = today - datetime.timedelta(days=90)
        rows = classify_rows(
            (self._row(30, today), self._row(40, old), self._row(101, today)),
            self.BASELINE)
        assert rows[0]["view_state"] == "previously_seen"
        assert rows[0]["recently_created"] is True, (
            "seen recently does not stop being recently created")
        assert rows[1]["recently_created"] is False
        assert rows[2]["view_state"] == "new_since_last_view"
        assert rows[2]["recently_created"] is True

    def test_never_measured_creation_is_not_recent(self):
        rows = classify_rows((self._row(30, None),), self.BASELINE)
        assert "recently_created" not in rows[0] or \
            rows[0]["recently_created"] is False
