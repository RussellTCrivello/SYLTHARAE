"""Scenarios: versioned case/outcome logic over contents, with mandatory dry-run.

A scenario classifies every content in its population (its criteria, under
the owner's access scope) into outcomes, by named conditions and ordered
cases (services/monitoring/scenario_model.py). It extends the monitoring
layer of m0020 - same owner model, same addressed notifications in
``alerts`` - rather than adding a second one.

* ``scenarios`` - the current definition (canonical JSON, SHA-256
  fingerprint, version), owner and lifecycle. A scenario starts as a
  ``draft``; it can only become ``active`` with ``activated_dry_run_id``
  pointing at a passed dry-run of the *same* definition fingerprint
  (enforced by the service, and the CHECK makes an active scenario without
  one impossible).
* ``scenario_versions`` - every definition a scenario has had; a semantic
  change creates a new version, history is never overwritten.
* ``scenario_evaluations`` - one row per evaluation *or dry-run*
  (``kind``): version, owner role and access scope, criteria and definition
  fingerprints, reference date, counts, the dry-run report, status, error.
* ``scenario_outcomes`` - the append-only outcome history. A row is written
  when a content's outcome set differs from its previous row (its first
  non-default outcome, a change, or a return to the default), with the
  matched cases, the previous outcomes, derived priority and evidence.
  UPDATE and DELETE are refused by a trigger: results are never overwritten.
  The one exception is the foreign-key cascade when the scenario, the owner
  or the content itself is deleted (users and deduplicated contents are
  hard-deleted elsewhere; refusing would make those operations fail).
  The current outcome of a content is its latest row; a content without a
  row has only ever had the default outcome.

``alerts.scenario_id`` / ``alerts.scenario_evaluation_id`` record which
scenario and evaluation produced a notification; such a notification always
has a recipient (CHECK), like a rule's.

Numbered 0021: 0020 is the monitoring rules.
"""

version = "0021"
name = "scenarios"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS scenarios (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        definition JSONB NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'draft',
        disabled_reason VARCHAR(64) NULL,
        activated_dry_run_id BIGINT NULL,
        activated_at TIMESTAMPTZ NULL,
        baselined_version INTEGER NOT NULL DEFAULT 0,
        last_evaluated_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_scenarios_name CHECK (length(btrim(name)) > 0),
        CONSTRAINT ck_scenarios_version CHECK (version >= 1),
        CONSTRAINT ck_scenarios_baselined
            CHECK (baselined_version >= 0 AND baselined_version <= version),
        CONSTRAINT ck_scenarios_fingerprint CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_scenarios_status
            CHECK (status IN ('draft', 'active', 'paused', 'disabled', 'archived')),
        CONSTRAINT ck_scenarios_disabled_reason
            CHECK ((status = 'disabled') = (disabled_reason IS NOT NULL)),
        CONSTRAINT ck_scenarios_active_dry_run
            CHECK (status <> 'active' OR activated_dry_run_id IS NOT NULL)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_scenarios_owner_name"
    " ON scenarios (owner_user_id, lower(name)) WHERE status <> 'archived'",
    "CREATE INDEX IF NOT EXISTS idx_scenarios_active ON scenarios (id) WHERE status = 'active'",
    """
    CREATE TABLE IF NOT EXISTS scenario_versions (
        scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        definition JSONB NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (scenario_id, version),
        CONSTRAINT ck_scenario_versions_fingerprint
            CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scenario_evaluations (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
        scenario_version INTEGER NOT NULL,
        kind VARCHAR(16) NOT NULL,
        trigger VARCHAR(16) NOT NULL,
        job_id TEXT NULL,
        requested_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        status VARCHAR(16) NOT NULL,
        owner_role VARCHAR(20) NULL,
        access_scope JSONB NULL,
        criteria_fingerprint CHAR(64) NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        reference_date DATE NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL,
        counts JSONB NOT NULL DEFAULT '{}'::jsonb,
        report JSONB NULL,
        error TEXT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        finished_at TIMESTAMPTZ NULL,
        CONSTRAINT ck_scenario_evaluations_kind CHECK (kind IN ('evaluation', 'dry_run')),
        CONSTRAINT ck_scenario_evaluations_trigger
            CHECK (trigger IN ('manual', 'ingestion', 'redetection', 'all_scenarios',
                               'schedule')),
        CONSTRAINT ck_scenario_evaluations_status
            CHECK (status IN ('completed', 'passed', 'invalid', 'failed', 'owner_revoked',
                              'skipped_busy', 'not_active')),
        CONSTRAINT ck_scenario_evaluations_error CHECK ((status = 'failed') = (error IS NOT NULL)),
        -- passed / invalid are dry-run verdicts; completed / not_active /
        -- skipped_busy are evaluation outcomes.
        CONSTRAINT ck_scenario_evaluations_kind_status CHECK (
            (kind = 'dry_run' AND status IN ('passed', 'invalid', 'failed', 'owner_revoked'))
            OR (kind = 'evaluation' AND status IN ('completed', 'failed', 'owner_revoked',
                                                   'skipped_busy', 'not_active'))),
        CONSTRAINT ck_scenario_evaluations_report
            CHECK (kind = 'evaluation' OR status IN ('failed', 'owner_revoked')
                   OR report IS NOT NULL)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scenario_evaluations_scenario"
    " ON scenario_evaluations (scenario_id, kind, id DESC)",
    "ALTER TABLE scenarios DROP CONSTRAINT IF EXISTS fk_scenarios_activated_dry_run",
    "ALTER TABLE scenarios ADD CONSTRAINT fk_scenarios_activated_dry_run"
    " FOREIGN KEY (activated_dry_run_id) REFERENCES scenario_evaluations(id)",
    """
    CREATE TABLE IF NOT EXISTS scenario_outcomes (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
        evaluation_id BIGINT NOT NULL REFERENCES scenario_evaluations(id) ON DELETE CASCADE,
        scenario_version INTEGER NOT NULL,
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        outcomes TEXT[] NOT NULL,
        matched_cases TEXT[] NOT NULL,
        previous_outcomes TEXT[] NULL,
        delivery VARCHAR(16) NOT NULL,
        priority VARCHAR(8) NULL,
        priority_basis JSONB NULL,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        recorded_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT ck_scenario_outcomes_nonempty CHECK (cardinality(outcomes) >= 1),
        CONSTRAINT ck_scenario_outcomes_delivery
            CHECK (delivery IN ('baseline', 'recorded', 'notified', 'overflow')),
        CONSTRAINT ck_scenario_outcomes_priority
            CHECK (priority IS NULL OR priority IN ('high', 'medium', 'low')),
        CONSTRAINT ck_scenario_outcomes_notified_priority
            CHECK (delivery NOT IN ('notified', 'overflow') OR priority IS NOT NULL)
    )
    """,
    # The current outcome of a content: its latest row.
    "CREATE INDEX IF NOT EXISTS idx_scenario_outcomes_latest"
    " ON scenario_outcomes (scenario_id, hash_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_scenario_outcomes_evaluation"
    " ON scenario_outcomes (evaluation_id)",
    """
    CREATE OR REPLACE FUNCTION scenario_outcomes_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        -- A cascade from a parent that is gone (the owner deleted, the
        -- content deleted by deduplication) removes the history with its
        -- subject; any other UPDATE or DELETE is refused.
        IF TG_OP = 'DELETE' AND (
                NOT EXISTS (SELECT 1 FROM scenarios WHERE id = OLD.scenario_id)
                OR NOT EXISTS (SELECT 1 FROM scenario_evaluations WHERE id = OLD.evaluation_id)
                OR NOT EXISTS (SELECT 1 FROM hashs WHERE id = OLD.hash_id)) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'scenario_outcomes is append-only (% refused)', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END
    $$
    """,
    "DROP TRIGGER IF EXISTS trg_scenario_outcomes_append_only ON scenario_outcomes",
    "CREATE TRIGGER trg_scenario_outcomes_append_only BEFORE UPDATE OR DELETE"
    " ON scenario_outcomes FOR EACH ROW EXECUTE FUNCTION scenario_outcomes_append_only()",
    # alerts: provenance of scenario notifications.
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS scenario_id INTEGER NULL"
    " REFERENCES scenarios(id) ON DELETE SET NULL",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS scenario_evaluation_id BIGINT NULL"
    " REFERENCES scenario_evaluations(id) ON DELETE SET NULL",
    "ALTER TABLE alerts DROP CONSTRAINT IF EXISTS ck_alerts_scenario_addressed",
    "ALTER TABLE alerts ADD CONSTRAINT ck_alerts_scenario_addressed"
    " CHECK (scenario_id IS NULL OR recipient_user_id IS NOT NULL)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE alerts DROP CONSTRAINT IF EXISTS ck_alerts_scenario_addressed")
        # Scenario alerts are addressed; they go with the scenario tables
        # (m0020's downgrade removes the recipient column and addressed rows).
        cur.execute("DELETE FROM alerts WHERE scenario_id IS NOT NULL"
                    " OR scenario_evaluation_id IS NOT NULL")
        cur.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS scenario_evaluation_id")
        cur.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS scenario_id")
        cur.execute("DROP TABLE IF EXISTS scenario_outcomes")
        cur.execute("DROP FUNCTION IF EXISTS scenario_outcomes_append_only()")
        cur.execute("ALTER TABLE scenarios DROP CONSTRAINT IF EXISTS fk_scenarios_activated_dry_run")
        cur.execute("DROP TABLE IF EXISTS scenario_evaluations")
        cur.execute("DROP TABLE IF EXISTS scenario_versions")
        cur.execute("DROP TABLE IF EXISTS scenarios")
