"""Initial application schema.

Correct dependency order (the original implementation failed on fresh
installs because table creation was executed with hardcoded credentials at
import time and interleaved extension/index creation with DDL).

Tables created here: words, punctuation, categorys, word_categorys, sides,
sources, hashs, paths, contents, titles_content, keywords, words_paths,
keywords_paths, alerts.

Deliberate decisions (documented in docs/DATABASE.md):
* ``hashs`` carries UNIQUE (hash, source_id, side_id): content identity is
  (hash, source, side) - DB-04.
* ``paths.hash_id`` FK has no cascade: deletion of a path triggers hash
  orphan-cleanup in the repository layer (DB-05), never silent cascades.
"""

version = "0001"
name = "initial_schema"

SQL_STATEMENTS = [
    # --- words ----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS words (
        id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        word TEXT UNIQUE NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_words_word ON words (word)",
    # --- punctuation ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS punctuation (
        id SERIAL PRIMARY KEY,
        punctuation_text TEXT UNIQUE NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_punctuation_text ON punctuation (punctuation_text)",
    # --- categorys ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS categorys (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        word_id INTEGER UNIQUE NOT NULL,
        FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_categorys_word_id ON categorys (word_id)",
    # --- word_categorys -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS words_categorys (
        word_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        FOREIGN KEY (category_id) REFERENCES categorys(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT unique_word_category UNIQUE (word_id, category_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wc_word_id ON words_categorys (word_id)",
    "CREATE INDEX IF NOT EXISTS idx_wc_category_id ON words_categorys (category_id)",
    # --- sides ----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS sides (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        importance DECIMAL(5,4) NOT NULL,
        date_creation DATE NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sides_importance ON sides (importance DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sides_date_creation ON sides (date_creation)",
    # --- sources --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        job VARCHAR(255) NOT NULL,
        importance DECIMAL(5,4) NOT NULL,
        country VARCHAR(255) NOT NULL,
        city VARCHAR(255) NULL,
        description VARCHAR(255) NULL,
        accounts VARCHAR(255) NULL,
        note VARCHAR(255) NULL,
        attachments VARCHAR(255) NULL,
        ownership VARCHAR(255) NULL,
        access_status VARCHAR(255) NULL,
        entry_date TIMESTAMP NULL,
        category_id INTEGER NULL,
        FOREIGN KEY (category_id) REFERENCES categorys(id) ON DELETE SET NULL ON UPDATE CASCADE,
        date_creation DATE NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sources_country ON sources (country)",
    "CREATE INDEX IF NOT EXISTS idx_sources_job ON sources (job)",
    "CREATE INDEX IF NOT EXISTS idx_sources_importance ON sources (importance DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sources_date_creation ON sources (date_creation)",
    "CREATE INDEX IF NOT EXISTS idx_sources_ownership ON sources (ownership)",
    "CREATE INDEX IF NOT EXISTS idx_sources_access_status ON sources (access_status)",
    "CREATE INDEX IF NOT EXISTS idx_sources_entry_date ON sources (entry_date)",
    "CREATE INDEX IF NOT EXISTS idx_sources_category_id ON sources (category_id)",
    # --- hashs ----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS hashs (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash CHAR(64) NOT NULL,
        side_id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        FOREIGN KEY (side_id) REFERENCES sides(id),
        FOREIGN KEY (source_id) REFERENCES sources(id),
        UNIQUE (hash, source_id, side_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hashs_hash ON hashs (hash)",
    "CREATE INDEX IF NOT EXISTS idx_hashs_source_id ON hashs (source_id)",
    "CREATE INDEX IF NOT EXISTS idx_hashs_side_id ON hashs (side_id)",
    "CREATE INDEX IF NOT EXISTS idx_hashs_hash_source_side ON hashs (hash, source_id, side_id)",
    # --- paths ----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS paths (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        file_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_size BIGINT NOT NULL CHECK (file_size >= 0),
        file_type VARCHAR(100) NOT NULL,
        file_status VARCHAR(10) NOT NULL CHECK (file_status IN ('Read', 'Unread')) DEFAULT 'Unread',
        file_date DATE NOT NULL,
        date_creation DATE NOT NULL,
        hash_id INTEGER NOT NULL,
        coordinates TEXT NULL,
        FOREIGN KEY (hash_id) REFERENCES hashs(id)
    )
    """,
    # --- contents -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS contents (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        content_data BYTEA,
        content_date DATE NULL,
        path_id INTEGER NOT NULL,
        FOREIGN KEY (path_id) REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_contents_path_id ON contents (path_id)",
    "CREATE INDEX IF NOT EXISTS idx_contents_content_date ON contents (content_date)",
    "CREATE INDEX IF NOT EXISTS idx_contents_path_date ON contents (path_id, content_date)",
    # --- titles_content -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS titles_content (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        title_data BYTEA,
        title_status VARCHAR(10) NOT NULL CHECK (title_status IN ('Main', 'Branch')) DEFAULT 'Main',
        title_content_id INTEGER NULL,
        path_id INTEGER NOT NULL,
        FOREIGN KEY (title_content_id) REFERENCES titles_content(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (path_id) REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_titles_content_path_id ON titles_content (path_id)",
    "CREATE INDEX IF NOT EXISTS idx_titles_content_status ON titles_content (title_status)",
    # --- keywords -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        keyword BYTEA UNIQUE NOT NULL,
        category_id INTEGER NOT NULL,
        FOREIGN KEY (category_id) REFERENCES categorys(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_keywords_category_id ON keywords (category_id)",
    # --- words_paths ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS words_paths (
        path_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        FOREIGN KEY (path_id) REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE,
        word_count INT,
        position_indexer BYTEA NOT NULL
    )
    """,
    # --- keywords_paths -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS keywords_paths (
        path_id INTEGER NOT NULL,
        keyword_id INTEGER NOT NULL,
        FOREIGN KEY (path_id) REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE ON UPDATE CASCADE,
        word_count INT,
        CONSTRAINT unique_keywords_paths_path_keyword UNIQUE (path_id, keyword_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_keywords_paths_path_id ON keywords_paths (path_id)",
    "CREATE INDEX IF NOT EXISTS idx_keywords_paths_keyword_id ON keywords_paths (keyword_id)",
    # --- alerts ---------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id SERIAL PRIMARY KEY,
        type VARCHAR(50) NOT NULL,
        priority VARCHAR(20) NOT NULL,
        title VARCHAR(500) NOT NULL,
        message TEXT NOT NULL,
        file_id INTEGER REFERENCES paths(id) ON DELETE CASCADE,
        file_name VARCHAR(500),
        file_path TEXT,
        event_date DATE,
        metadata JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        read BOOLEAN DEFAULT FALSE,
        dismissed BOOLEAN DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts (type)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_priority ON alerts (priority)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_event_date ON alerts (event_date)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_dismissed ON alerts (dismissed)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_read ON alerts (read)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_file_id ON alerts (file_id)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)
