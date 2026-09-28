"""Signal provenance: method, ordinal confidence and the evidence sentence.

Adds to ``content_signals`` what a reader needs to judge a signal without
re-running the detector (detector ``temporal-1.1.0`` and later):

* ``method`` - the pattern that matched;
* ``confidence`` (``high``/``medium``/``low``) and ``confidence_basis`` - an
  ordinal level assigned by a documented rule (core.detection.temporal_intel
  CONFIDENCE_RULES). It is not a probability: no calibration exists, and the
  schema does not pretend otherwise;
* ``evidence_sentence`` with ``sentence_start``/``sentence_end`` - the
  sentence quoted from the stored text, which must contain the signal.

Rows written by ``temporal-1.0.0`` keep NULL in all six columns, meaning
"not recorded by that detector version" - not "low" and not "no sentence".
Re-detection (``POST /api/signals/redetect``, scope ``stale``) replaces them.
The CHECK constraints make those columns all-or-nothing, so a half-recorded
signal cannot exist.
"""

version = "0018"
name = "signal_provenance"

_STATEMENTS = [
    "ALTER TABLE content_signals ADD COLUMN IF NOT EXISTS method VARCHAR(64)",
    "ALTER TABLE content_signals ADD COLUMN IF NOT EXISTS confidence VARCHAR(8)",
    "ALTER TABLE content_signals ADD COLUMN IF NOT EXISTS confidence_basis VARCHAR(40)",
    "ALTER TABLE content_signals ADD COLUMN IF NOT EXISTS evidence_sentence TEXT",
    "ALTER TABLE content_signals ADD COLUMN IF NOT EXISTS sentence_start INTEGER",
    "ALTER TABLE content_signals ADD COLUMN IF NOT EXISTS sentence_end INTEGER",
    """
    ALTER TABLE content_signals
        ADD CONSTRAINT ck_content_signals_confidence
            CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low')),
        ADD CONSTRAINT ck_content_signals_provenance_complete CHECK (
            (method IS NULL) = (confidence IS NULL)
            AND (confidence IS NULL) = (confidence_basis IS NULL)
            AND (confidence IS NULL) = (evidence_sentence IS NULL)
            AND (evidence_sentence IS NULL) = (sentence_start IS NULL)
            AND (sentence_start IS NULL) = (sentence_end IS NULL)),
        ADD CONSTRAINT ck_content_signals_sentence_contains CHECK (
            sentence_start IS NULL
            OR (sentence_start <= char_start AND char_end <= sentence_end
                AND length(evidence_sentence) = sentence_end - sentence_start))
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_signals_confidence"
    " ON content_signals (signal_type, confidence)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in _STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS idx_content_signals_confidence")
        for constraint in ("ck_content_signals_sentence_contains",
                           "ck_content_signals_provenance_complete",
                           "ck_content_signals_confidence"):
            cur.execute(f"ALTER TABLE content_signals DROP CONSTRAINT IF EXISTS {constraint}")
        for column in ("sentence_end", "sentence_start", "evidence_sentence",
                       "confidence_basis", "confidence", "method"):
            cur.execute(f"ALTER TABLE content_signals DROP COLUMN IF EXISTS {column}")
