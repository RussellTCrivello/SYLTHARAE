"""Content signals: evidence-bearing detections keyed on content (Phase 1).

``content_signals`` stores what a detector found in a document's text - date
references, document-relative expressions, orientation cues and, later,
place mentions - with the exact surface text, its character offsets in the
stored text (``contents_raw``), the resolved Gregorian range when there is
one, the evidence, and the detector version.

Keyed on ``hash_id`` (content), not ``path_id``: the same bytes seen in two
places are one document with one set of signals, exactly as extraction and
the word index already are (m0011 content identity).

Integrity lives in the schema, not only in code:

* a signal has a resolved date range **iff** its resolution says it was
  resolved (``absolute``/``approximate``/``document_relative``); ambiguous
  and unresolved signals carry no invented date;
* a ``document_relative`` signal names the document date it was resolved
  against (``anchor_date``);
* ``dedup_key`` (SHA-256, computed by the detector over content id, detector
  version, type, value, offsets and anchor - NULL-safe via JSON ``null``) is
  UNIQUE, so concurrent or repeated detection never duplicates a signal.
  ``NULLS NOT DISTINCT`` would need PostgreSQL 15; the key works on every
  supported server.

``content_signal_runs`` records one row per (content, detector): which
version ran, when, against which anchor, whether the text was complete,
truncated, absent or the run failed (with the error). It is what separates
"analysed and found nothing" from "never analysed" - unknown is not zero.
Clock-dependent facts (is this date in the future?) are deliberately **not**
stored: they are computed at read time against an explicit reference date.
"""

version = "0017"
name = "content_signals"

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS content_signals (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        detector VARCHAR(40) NOT NULL,
        detector_ver VARCHAR(40) NOT NULL,
        signal_type VARCHAR(40) NOT NULL,
        value TEXT NOT NULL,
        surface TEXT NOT NULL,
        char_start INTEGER NOT NULL,
        char_end INTEGER NOT NULL,
        language VARCHAR(8),
        calendar VARCHAR(16),
        resolution VARCHAR(24) NOT NULL,
        date_from DATE,
        date_to DATE,
        text_orientation VARCHAR(8),
        anchor_date DATE,
        evidence JSONB NOT NULL,
        dedup_key CHAR(64) NOT NULL,
        detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_content_signals_dedup UNIQUE (dedup_key),
        CONSTRAINT ck_content_signals_dedup_hex CHECK (dedup_key ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_content_signals_type CHECK (signal_type ~ '^[a-z][a-z_]{2,39}$'),
        CONSTRAINT ck_content_signals_value CHECK (length(value) > 0),
        CONSTRAINT ck_content_signals_offsets CHECK (char_start >= 0 AND char_end > char_start),
        CONSTRAINT ck_content_signals_calendar
            CHECK (calendar IS NULL OR calendar IN ('gregorian', 'hijri', 'jalali')),
        CONSTRAINT ck_content_signals_resolution CHECK (resolution IN
            ('absolute', 'approximate', 'document_relative', 'ambiguous', 'unresolved')),
        CONSTRAINT ck_content_signals_range_pair CHECK ((date_from IS NULL) = (date_to IS NULL)),
        CONSTRAINT ck_content_signals_range_order CHECK (date_from IS NULL OR date_from <= date_to),
        CONSTRAINT ck_content_signals_resolved_has_range CHECK (
            (resolution IN ('absolute', 'approximate', 'document_relative'))
            = (date_from IS NOT NULL)),
        CONSTRAINT ck_content_signals_relative_anchor
            CHECK (resolution <> 'document_relative' OR anchor_date IS NOT NULL),
        CONSTRAINT ck_content_signals_orientation
            CHECK (text_orientation IS NULL OR text_orientation IN ('future', 'present', 'past'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_signals_hash ON content_signals (hash_id)",
    "CREATE INDEX IF NOT EXISTS idx_content_signals_dates ON content_signals (date_from, date_to)"
    " WHERE date_from IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_content_signals_type"
    " ON content_signals (signal_type, detector, detector_ver)",
    """
    CREATE TABLE IF NOT EXISTS content_signal_runs (
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        detector VARCHAR(40) NOT NULL,
        detector_ver VARCHAR(40) NOT NULL,
        status VARCHAR(16) NOT NULL,
        anchor_date DATE,
        chars_total BIGINT,
        chars_scanned BIGINT,
        signal_count INTEGER NOT NULL DEFAULT 0,
        trigger VARCHAR(24) NOT NULL,
        job_id TEXT,
        error TEXT,
        ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (hash_id, detector),
        CONSTRAINT ck_signal_runs_status
            CHECK (status IN ('complete', 'truncated', 'no_text', 'failed')),
        CONSTRAINT ck_signal_runs_trigger CHECK (trigger IN ('ingestion', 'redetection')),
        CONSTRAINT ck_signal_runs_error CHECK ((status = 'failed') = (error IS NOT NULL)),
        CONSTRAINT ck_signal_runs_count CHECK (signal_count >= 0),
        CONSTRAINT ck_signal_runs_chars CHECK (
            chars_scanned IS NULL OR chars_total IS NULL OR chars_scanned <= chars_total),
        CONSTRAINT ck_signal_runs_truncated CHECK (
            status <> 'truncated' OR chars_scanned < chars_total)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_signal_runs_version"
    " ON content_signal_runs (detector, detector_ver)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in _STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS content_signal_runs")
        cur.execute("DROP TABLE IF EXISTS content_signals")
