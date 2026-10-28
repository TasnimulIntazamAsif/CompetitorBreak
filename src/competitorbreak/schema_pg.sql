-- CompetitorANK PostgreSQL schema (mirror of schema.sql). Idempotent.
-- Timestamps set by the app via now() are TIMESTAMP; published_at stays TEXT
-- because scraped values are free-form strings.

CREATE TABLE IF NOT EXISTS competitors (
    id          SERIAL PRIMARY KEY,
    domain      TEXT UNIQUE NOT NULL,
    name        TEXT,
    is_self     INTEGER NOT NULL DEFAULT 0,
    blog_url    TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    id             SERIAL PRIMARY KEY,
    competitor_id  INTEGER REFERENCES competitors(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'running',
    posts_found    INTEGER NOT NULL DEFAULT 0,
    posts_scraped  INTEGER NOT NULL DEFAULT 0,
    started_at     TIMESTAMP NOT NULL DEFAULT now(),
    finished_at    TIMESTAMP,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS keywords (
    id         SERIAL PRIMARY KEY,
    term       TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pages (
    id               SERIAL PRIMARY KEY,
    competitor_id    INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    url              TEXT NOT NULL,
    title            TEXT,
    h1               TEXT,
    meta_description TEXT,
    primary_keyword  TEXT,
    word_count       INTEGER,
    published_at     TEXT,
    scraped_at       TIMESTAMP,
    first_seen       TIMESTAMP NOT NULL DEFAULT now(),
    last_seen        TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (competitor_id, url)
);

CREATE TABLE IF NOT EXISTS page_keywords (
    id          SERIAL PRIMARY KEY,
    page_id     INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    keyword_id  INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    score       INTEGER,
    count       INTEGER,
    density     REAL,
    UNIQUE (page_id, keyword_id, source)
);
CREATE INDEX IF NOT EXISTS idx_page_keywords_kw ON page_keywords (keyword_id);
CREATE INDEX IF NOT EXISTS idx_pages_comp ON pages (competitor_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS serp_runs (
    id                SERIAL PRIMARY KEY,
    country           TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    keywords_checked  INTEGER NOT NULL DEFAULT 0,
    started_at        TIMESTAMP NOT NULL DEFAULT now(),
    finished_at       TIMESTAMP,
    error             TEXT
);

CREATE TABLE IF NOT EXISTS serp_results (
    id            SERIAL PRIMARY KEY,
    serp_run_id   INTEGER REFERENCES serp_runs(id) ON DELETE CASCADE,
    keyword_id    INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    position      INTEGER,
    domain        TEXT,
    url           TEXT,
    title         TEXT,
    competitor_id INTEGER REFERENCES competitors(id) ON DELETE SET NULL,
    captured_at   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_serp_results_kw ON serp_results (keyword_id);
CREATE INDEX IF NOT EXISTS idx_serp_results_comp ON serp_results (competitor_id);
