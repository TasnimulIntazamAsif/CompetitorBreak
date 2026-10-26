-- CompetitorBreak SQLite schema (scrape pipeline). Idempotent.

CREATE TABLE IF NOT EXISTS competitors (
    id          INTEGER PRIMARY KEY,
    domain      TEXT UNIQUE NOT NULL,
    name        TEXT,
    is_self     INTEGER NOT NULL DEFAULT 0,
    blog_url    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per scrape run per competitor.
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY,
    competitor_id  INTEGER REFERENCES competitors(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'running',  -- running | success | error
    posts_found    INTEGER NOT NULL DEFAULT 0,
    posts_scraped  INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT,
    error          TEXT
);

-- Normalised keyword/term dictionary.
CREATE TABLE IF NOT EXISTS keywords (
    id         INTEGER PRIMARY KEY,
    term       TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Scraped blog posts.
CREATE TABLE IF NOT EXISTS pages (
    id               INTEGER PRIMARY KEY,
    competitor_id    INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    url              TEXT NOT NULL,
    title            TEXT,
    h1               TEXT,
    meta_description TEXT,
    primary_keyword  TEXT,
    word_count       INTEGER,
    published_at     TEXT,
    scraped_at       TEXT,
    first_seen       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (competitor_id, url)
);

-- Which keyword/term a post targets, with a relevance score and signal source.
CREATE TABLE IF NOT EXISTS page_keywords (
    id          INTEGER PRIMARY KEY,
    page_id     INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    keyword_id  INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,      -- content | tag | meta | slug | primary
    score       INTEGER,
    UNIQUE (page_id, keyword_id, source)
);
CREATE INDEX IF NOT EXISTS idx_page_keywords_kw ON page_keywords (keyword_id);
CREATE INDEX IF NOT EXISTS idx_pages_comp ON pages (competitor_id);
