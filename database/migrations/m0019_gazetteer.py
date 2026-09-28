"""Database gazetteer and place-mention signals (Phase 2).

Tables:

* ``geo_places`` - one row per place (``place_key`` = ``wikidata:Q..``),
  feature type, ISO 3166-1 country codes (an array: disputed or shared places
  keep every code recorded, none is picked), optional WGS84 point, source.
  Places are *retired*, never deleted, when they leave the seed.
* ``geo_place_names`` - multilingual names (en/ar/he/fa/hr) with ISO 15924
  script, name type (endonym / exonym / historical / variant, or
  ``unclassified`` when the source records no native name to compare with),
  a curated ``homograph`` flag and the normalised ``match_key`` the detector
  matches on.
* ``geo_gazetteer_loads`` - every seed load that changed the gazetteer: seed
  version and SHA-256, a fingerprint computed from the stored rows, counts,
  who applied it.
* ``content_signal_places`` - the candidate places of each place-mention
  signal. One candidate = identified; several = ambiguous (kept, not picked).

``content_signals.resolution`` gains ``identified``, allowed only for
``place_mention`` signals (which never carry dates - the existing
resolved-has-range CHECK already enforces that).

``path_geo_mentions`` (m0015, written by the replaced regex scan) becomes a
**view** with the same reading columns over identified, high/medium
confidence place signals. The old table is kept as
``path_geo_mentions_m0015``; its rows still show through the view for
content not yet analysed by the place detector, so upgrading loses nothing.

The initial seed is loaded here through ``services.geo.gazetteer.sync_seed``
(the single loader), inside this migration's transaction.

Downgrade removes the derived place signals (re-creatable by re-detection),
restores the m0015 table and the m0017 resolution CHECK, and drops the
gazetteer tables.
"""

version = "0019"
name = "gazetteer"

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS geo_places (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        place_key VARCHAR(64) NOT NULL,
        label VARCHAR(255) NOT NULL,
        feature_type VARCHAR(16) NOT NULL,
        country_codes CHAR(2)[] NOT NULL DEFAULT '{}',
        latitude DOUBLE PRECISION NULL,
        longitude DOUBLE PRECISION NULL,
        source VARCHAR(40) NOT NULL,
        source_title VARCHAR(255) NULL,
        seed_version VARCHAR(40) NULL,
        retired BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_geo_places_key UNIQUE (place_key),
        CONSTRAINT ck_geo_places_key CHECK (place_key ~ '^[a-z][a-z0-9_]*:[A-Za-z0-9_.-]+$'),
        CONSTRAINT ck_geo_places_label CHECK (length(btrim(label)) > 0),
        CONSTRAINT ck_geo_places_feature CHECK (feature_type IN ('city', 'country', 'region')),
        CONSTRAINT ck_geo_places_countries
            CHECK (array_to_string(country_codes, ',') ~ '^([A-Z]{2}(,[A-Z]{2})*)?$'),
        CONSTRAINT ck_geo_places_point_pair CHECK ((latitude IS NULL) = (longitude IS NULL)),
        CONSTRAINT ck_geo_places_lat CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
        CONSTRAINT ck_geo_places_lon CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS geo_place_names (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        place_id INTEGER NOT NULL REFERENCES geo_places(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        match_key VARCHAR(255) NOT NULL,
        language VARCHAR(8) NOT NULL,
        script CHAR(4) NOT NULL,
        name_type VARCHAR(16) NOT NULL,
        homograph BOOLEAN NOT NULL DEFAULT FALSE,
        source VARCHAR(40) NOT NULL,
        note TEXT NULL,
        CONSTRAINT uq_geo_place_names UNIQUE (place_id, language, match_key),
        CONSTRAINT ck_geo_place_names_name CHECK (length(btrim(name)) > 0),
        CONSTRAINT ck_geo_place_names_key CHECK (length(match_key) > 0),
        CONSTRAINT ck_geo_place_names_language CHECK (language IN ('en', 'ar', 'he', 'fa', 'hr')),
        CONSTRAINT ck_geo_place_names_script CHECK (
            (language IN ('en', 'hr') AND script = 'Latn')
            OR (language IN ('ar', 'fa') AND script = 'Arab')
            OR (language = 'he' AND script = 'Hebr')),
        CONSTRAINT ck_geo_place_names_type CHECK (name_type IN
            ('endonym', 'exonym', 'historical', 'variant', 'unclassified'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_geo_place_names_key ON geo_place_names (match_key)",
    "CREATE INDEX IF NOT EXISTS idx_geo_place_names_place ON geo_place_names (place_id)",
    """
    CREATE TABLE IF NOT EXISTS geo_gazetteer_loads (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        seed_version VARCHAR(40) NOT NULL,
        content_sha256 CHAR(64) NOT NULL,
        fingerprint CHAR(64) NOT NULL,
        place_count INTEGER NOT NULL,
        name_count INTEGER NOT NULL,
        stats JSONB NOT NULL DEFAULT '{}'::jsonb,
        loaded_by VARCHAR(80) NOT NULL,
        loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_geo_loads_sha CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_geo_loads_fp CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_geo_loads_counts CHECK (place_count >= 0 AND name_count >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_signal_places (
        signal_id BIGINT NOT NULL REFERENCES content_signals(id) ON DELETE CASCADE,
        place_id INTEGER NOT NULL REFERENCES geo_places(id) ON DELETE RESTRICT,
        PRIMARY KEY (signal_id, place_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_signal_places_place"
    " ON content_signal_places (place_id, signal_id)",
    "ALTER TABLE content_signals DROP CONSTRAINT IF EXISTS ck_content_signals_resolution",
    """
    ALTER TABLE content_signals
        ADD CONSTRAINT ck_content_signals_resolution CHECK (resolution IN
            ('absolute', 'approximate', 'document_relative', 'ambiguous', 'unresolved',
             'identified')),
        ADD CONSTRAINT ck_content_signals_identified_place
            CHECK (resolution <> 'identified' OR signal_type = 'place_mention')
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_signals_detector"
    " ON content_signals (detector, signal_type, resolution)",
    "ALTER TABLE path_geo_mentions RENAME TO path_geo_mentions_m0015",
    """
    CREATE VIEW path_geo_mentions AS
    SELECT s.hash_id,
           g.label::varchar(255) AS place_name,
           (SELECT string_agg(c.label, ', ' ORDER BY c.label) FROM geo_places c
             WHERE c.feature_type = 'country' AND NOT c.retired
               AND cardinality(c.country_codes) = 1
               AND c.country_codes <@ g.country_codes)::varchar(255) AS country,
           g.latitude, g.longitude,
           count(*)::integer AS mention_count,
           g.place_key,
           'signals'::varchar(16) AS provenance
      FROM content_signals s
      JOIN content_signal_places csp ON csp.signal_id = s.id
      JOIN geo_places g ON g.id = csp.place_id
     WHERE s.detector = 'places' AND s.resolution = 'identified'
       AND s.confidence IN ('high', 'medium') AND g.latitude IS NOT NULL
     GROUP BY s.hash_id, g.id
    UNION ALL
    SELECT m.hash_id, m.place_name, m.country, m.latitude, m.longitude, m.mention_count,
           NULL::varchar(64), 'legacy_m0015'::varchar(16)
      FROM path_geo_mentions_m0015 m
     WHERE NOT EXISTS (SELECT 1 FROM content_signal_runs r
                        WHERE r.hash_id = m.hash_id AND r.detector = 'places'
                          AND r.status <> 'failed')
    """,
]


def upgrade(conn) -> None:
    from services.geo.gazetteer import load_seed_file, sync_seed

    seed = load_seed_file()  # verified before any DDL runs
    with conn.cursor() as cur:
        for statement in _STATEMENTS:
            cur.execute(statement)
        sync_seed(cur, seed, loaded_by="migration:0019")


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP VIEW IF EXISTS path_geo_mentions")
        cur.execute("SELECT to_regclass('path_geo_mentions_m0015') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("ALTER TABLE path_geo_mentions_m0015 RENAME TO path_geo_mentions")
        cur.execute("DROP TABLE IF EXISTS content_signal_places")
        cur.execute("DELETE FROM content_signals WHERE detector = 'places'"
                    " OR resolution = 'identified'")
        cur.execute("DELETE FROM content_signal_runs WHERE detector = 'places'")
        cur.execute("DROP INDEX IF EXISTS idx_content_signals_detector")
        cur.execute("ALTER TABLE content_signals"
                    " DROP CONSTRAINT IF EXISTS ck_content_signals_identified_place")
        cur.execute("ALTER TABLE content_signals DROP CONSTRAINT IF EXISTS ck_content_signals_resolution")
        cur.execute("ALTER TABLE content_signals ADD CONSTRAINT ck_content_signals_resolution"
                    " CHECK (resolution IN ('absolute', 'approximate', 'document_relative',"
                    " 'ambiguous', 'unresolved'))")
        cur.execute("DROP TABLE IF EXISTS geo_gazetteer_loads")
        cur.execute("DROP TABLE IF EXISTS geo_place_names")
        cur.execute("DROP TABLE IF EXISTS geo_places")
