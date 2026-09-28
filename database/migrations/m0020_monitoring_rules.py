"""Monitoring rules: criteria + signal conditions evaluated into notifications.

Extends the existing notification store (``alerts``) instead of adding a
second notification architecture:

* ``alerts.recipient_user_id`` - NULL for system-wide alerts (every existing
  row: similar files, future dates, processing), a user id for alerts
  addressed to one person. A rule's alerts are addressed to the rule's owner,
  and every read path shows a user only rows with a NULL or their own
  recipient. ``alerts.rule_id`` / ``alerts.rule_evaluation_id`` say which rule
  and which evaluation produced the alert.
* ``monitoring_rules`` - the current definition of each rule (canonical JSON,
  SHA-256 fingerprint, version), its owner and lifecycle status. A rule is
  ``disabled`` only with a recorded reason (for example, the owner lost the
  privilege to run it).
* ``monitoring_rule_versions`` - every definition a rule has had. A semantic
  change creates a new version; history is never overwritten.
* ``rule_evaluations`` - one row per evaluation: rule version, owner role and
  access scope at evaluation time, criteria fingerprint, reference date,
  counts per outcome, status and error. Never updated after it finishes.
* ``rule_subject_ledger`` - every subject (a signal, or a content) a rule has
  matched, with its state. The primary key ``(rule_id, subject_key)`` is the
  deduplication: a subject is matched once per rule, whatever happens later.
  States keep the mechanisms distinct:

  ``baseline``    matched when the rule (version) started; not notified
  ``pending``     matched, waiting for a threshold, cooldown or digest
  ``notified``    delivered in ``alert_id``
  ``suppressed``  matched while the rule was suppressed; never notified
  ``expired``     fell out of the threshold window before the threshold was met

Numbered 0020: 0019 is the gazetteer.
"""

version = "0020"
name = "monitoring_rules"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS monitoring_rules (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        definition JSONB NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        saved_search_id INTEGER NULL REFERENCES saved_searches(id) ON DELETE SET NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        disabled_reason VARCHAR(64) NULL,
        suppressed_until TIMESTAMPTZ NULL,
        baselined_version INTEGER NOT NULL DEFAULT 0,
        last_evaluated_at TIMESTAMPTZ NULL,
        last_notified_at TIMESTAMPTZ NULL,
        last_digest_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_monitoring_rules_name CHECK (length(btrim(name)) > 0),
        CONSTRAINT ck_monitoring_rules_version CHECK (version >= 1),
        CONSTRAINT ck_monitoring_rules_baselined
            CHECK (baselined_version >= 0 AND baselined_version <= version),
        CONSTRAINT ck_monitoring_rules_fingerprint CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_monitoring_rules_status
            CHECK (status IN ('active', 'paused', 'disabled', 'archived')),
        CONSTRAINT ck_monitoring_rules_disabled_reason
            CHECK ((status = 'disabled') = (disabled_reason IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_rules_owner_name"
    " ON monitoring_rules (owner_user_id, lower(name)) WHERE status <> 'archived'",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_rules_active"
    " ON monitoring_rules (id) WHERE status = 'active'",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_rules_saved_search"
    " ON monitoring_rules (saved_search_id) WHERE saved_search_id IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS monitoring_rule_versions (
        rule_id INTEGER NOT NULL REFERENCES monitoring_rules(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        definition JSONB NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (rule_id, version),
        CONSTRAINT ck_rule_versions_fingerprint CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_evaluations (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        rule_id INTEGER NOT NULL REFERENCES monitoring_rules(id) ON DELETE CASCADE,
        rule_version INTEGER NOT NULL,
        trigger VARCHAR(16) NOT NULL,
        job_id TEXT NULL,
        status VARCHAR(16) NOT NULL,
        owner_role VARCHAR(20) NULL,
        access_scope JSONB NULL,
        criteria_fingerprint CHAR(64) NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        reference_date DATE NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL,
        counts JSONB NOT NULL DEFAULT '{}'::jsonb,
        error TEXT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        finished_at TIMESTAMPTZ NULL,
        CONSTRAINT ck_rule_evaluations_trigger
            CHECK (trigger IN ('manual', 'ingestion', 'redetection', 'all_rules', 'schedule')),
        CONSTRAINT ck_rule_evaluations_status
            CHECK (status IN ('completed', 'failed', 'owner_revoked', 'skipped_busy', 'not_active')),
        CONSTRAINT ck_rule_evaluations_error CHECK ((status = 'failed') = (error IS NOT NULL))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rule_evaluations_rule"
    " ON rule_evaluations (rule_id, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS rule_subject_ledger (
        rule_id INTEGER NOT NULL REFERENCES monitoring_rules(id) ON DELETE CASCADE,
        subject_key VARCHAR(200) NOT NULL,
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        signal_id BIGINT NULL,
        rule_version INTEGER NOT NULL,
        state VARCHAR(16) NOT NULL,
        group_key VARCHAR(200) NOT NULL,
        confidence VARCHAR(8) NULL,
        event_date_from DATE NULL,
        event_date_to DATE NULL,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        first_matched_at TIMESTAMPTZ NOT NULL,
        matched_evaluation_id BIGINT NOT NULL REFERENCES rule_evaluations(id) ON DELETE CASCADE,
        state_changed_at TIMESTAMPTZ NOT NULL,
        notified_evaluation_id BIGINT NULL REFERENCES rule_evaluations(id) ON DELETE SET NULL,
        alert_id INTEGER NULL REFERENCES alerts(id) ON DELETE SET NULL,
        PRIMARY KEY (rule_id, subject_key),
        CONSTRAINT ck_rule_ledger_state
            CHECK (state IN ('baseline', 'pending', 'notified', 'suppressed', 'expired')),
        CONSTRAINT ck_rule_ledger_notified
            CHECK ((state = 'notified') = (notified_evaluation_id IS NOT NULL)),
        CONSTRAINT ck_rule_ledger_confidence
            CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rule_ledger_pending"
    " ON rule_subject_ledger (rule_id, first_matched_at) WHERE state = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_rule_ledger_alert"
    " ON rule_subject_ledger (alert_id) WHERE alert_id IS NOT NULL",
    # alerts: addressed notifications and their provenance.
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS recipient_user_id INTEGER NULL"
    " REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rule_id INTEGER NULL"
    " REFERENCES monitoring_rules(id) ON DELETE SET NULL",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rule_evaluation_id BIGINT NULL"
    " REFERENCES rule_evaluations(id) ON DELETE SET NULL",
    "ALTER TABLE alerts DROP CONSTRAINT IF EXISTS ck_alerts_rule_addressed",
    # A rule alert always has a recipient: a rule alert without one would be
    # shown to everybody.
    "ALTER TABLE alerts ADD CONSTRAINT ck_alerts_rule_addressed"
    " CHECK (rule_id IS NULL OR recipient_user_id IS NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_recipient_dismissed_created"
    " ON alerts (recipient_user_id, dismissed, created_at DESC)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS idx_alerts_recipient_dismissed_created")
        cur.execute("ALTER TABLE alerts DROP CONSTRAINT IF EXISTS ck_alerts_rule_addressed")
        # Addressed alerts cannot survive as system-wide ones: removing the
        # recipient would disclose them to every user.
        cur.execute("DELETE FROM alerts WHERE recipient_user_id IS NOT NULL")
        cur.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS rule_evaluation_id")
        cur.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS rule_id")
        cur.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS recipient_user_id")
        cur.execute("DROP TABLE IF EXISTS rule_subject_ledger")
        cur.execute("DROP TABLE IF EXISTS rule_evaluations")
        cur.execute("DROP TABLE IF EXISTS monitoring_rule_versions")
        cur.execute("DROP TABLE IF EXISTS monitoring_rules")
