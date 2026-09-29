"""Report run analyses: analytical results computed from a run's datasets (step 16).

``report_run_analyses`` stores, per declared analysis of a completed run
(``core.analytics.kinds``):

* ``analysis_key`` / ``analysis_fingerprint`` - which analysis, and the
  fingerprint of its meaning (kind, kind version, thresholds, template set,
  input datasets) at run time;
* ``state`` - ``measured`` or ``not_measurable``; a not-measurable result
  names its ``reason`` (CHECK) and a measured one has none;
* ``inputs`` - the dataset keys each input role was read from;
* ``measures`` / ``rows`` - the computed values (JSON);
* ``narrative`` - the five-voice narrative as *template references*: for
  each voice the sentence key, the msgid and the parameters, never rendered
  prose, so the text is reproducible and translatable from the record;
* ``template_set`` / ``template_version`` - the reviewed template set used.

Rows are written in the same transaction that completes the run and never
updated (trigger), like ``report_run_datasets``; deleting the run removes
them (ON DELETE CASCADE).

Numbered 0026: 0025 is the detection run history index.
"""

version = "0026"
name = "report_run_analyses"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS report_run_analyses (
        run_id BIGINT NOT NULL REFERENCES report_runs(id) ON DELETE CASCADE,
        position SMALLINT NOT NULL,
        analysis_key VARCHAR(120) NOT NULL,
        analysis_fingerprint CHAR(64) NOT NULL,
        kind VARCHAR(40) NOT NULL,
        state VARCHAR(16) NOT NULL,
        reason VARCHAR(64) NULL,
        inputs JSONB NOT NULL,
        measures JSONB NOT NULL,
        rows JSONB NOT NULL,
        narrative JSONB NOT NULL,
        template_set VARCHAR(60) NOT NULL,
        template_version INTEGER NOT NULL,
        PRIMARY KEY (run_id, position),
        CONSTRAINT uq_report_run_analyses_key UNIQUE (run_id, analysis_key),
        CONSTRAINT ck_report_run_analyses_state CHECK (state IN ('measured', 'not_measurable')),
        CONSTRAINT ck_report_run_analyses_reason CHECK (
            (state = 'not_measurable') = (reason IS NOT NULL)),
        CONSTRAINT ck_report_run_analyses_fingerprint CHECK (
            analysis_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_report_run_analyses_json CHECK (
            jsonb_typeof(inputs) = 'object' AND jsonb_typeof(measures) = 'object'
            AND jsonb_typeof(rows) = 'array' AND jsonb_typeof(narrative) = 'object'),
        CONSTRAINT ck_report_run_analyses_voices CHECK (
            jsonb_typeof(narrative -> 'voices') = 'array'
            AND jsonb_array_length(narrative -> 'voices') = 5),
        CONSTRAINT ck_report_run_analyses_template CHECK (template_version >= 1)
    )
    """,
    """
    CREATE OR REPLACE FUNCTION report_run_analyses_write_once() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'report run analyses are written once and never updated'
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_report_run_analyses_write_once ON report_run_analyses",
    """
    CREATE TRIGGER trg_report_run_analyses_write_once
        BEFORE UPDATE ON report_run_analyses
        FOR EACH ROW EXECUTE FUNCTION report_run_analyses_write_once()
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS report_run_analyses")
        cur.execute("DROP FUNCTION IF EXISTS report_run_analyses_write_once()")
