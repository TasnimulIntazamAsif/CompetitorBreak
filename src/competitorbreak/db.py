"""SQLite access layer (scrape pipeline). No server required — the database is a
local file. Uses RETURNING (SQLite >= 3.35) and UPSERT (>= 3.24)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection; commit on success, rollback on error."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# --- competitors ---------------------------------------------------------

def upsert_competitor(
    conn: sqlite3.Connection,
    domain: str,
    name: str | None = None,
    is_self: bool = False,
    blog_url: str | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO competitors (domain, name, is_self, blog_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (domain) DO UPDATE
            SET name = COALESCE(excluded.name, competitors.name),
                is_self = excluded.is_self,
                blog_url = COALESCE(excluded.blog_url, competitors.blog_url)
        RETURNING id
        """,
        (domain, name, 1 if is_self else 0, blog_url),
    ).fetchone()
    return row["id"]


def list_competitors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM competitors ORDER BY is_self DESC, domain"
    ).fetchall()


def get_competitor(conn: sqlite3.Connection, domain: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM competitors WHERE domain = ?", (domain,)
    ).fetchone()


# --- runs ----------------------------------------------------------------

def start_run(conn: sqlite3.Connection, competitor_id: int) -> int:
    row = conn.execute(
        "INSERT INTO runs (competitor_id, status) VALUES (?, 'running') RETURNING id",
        (competitor_id,),
    ).fetchone()
    return row["id"]


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    posts_found: int = 0,
    posts_scraped: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE runs
           SET status = ?, posts_found = ?, posts_scraped = ?,
               error = ?, finished_at = datetime('now')
         WHERE id = ?
        """,
        (status, posts_found, posts_scraped, error, run_id),
    )


# --- keywords & pages ----------------------------------------------------

def upsert_keyword(conn: sqlite3.Connection, term: str) -> int:
    row = conn.execute(
        """
        INSERT INTO keywords (term) VALUES (?)
        ON CONFLICT (term) DO UPDATE SET term = excluded.term
        RETURNING id
        """,
        (term,),
    ).fetchone()
    return row["id"]


def upsert_page(
    conn: sqlite3.Connection,
    competitor_id: int,
    url: str,
    title: str | None,
    h1: str | None,
    meta_description: str | None,
    primary_keyword: str | None,
    word_count: int | None,
    published_at: str | None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO pages (competitor_id, url, title, h1, meta_description,
                           primary_keyword, word_count, published_at, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT (competitor_id, url) DO UPDATE
            SET title = excluded.title,
                h1 = excluded.h1,
                meta_description = excluded.meta_description,
                primary_keyword = excluded.primary_keyword,
                word_count = excluded.word_count,
                published_at = excluded.published_at,
                scraped_at = datetime('now'),
                last_seen = datetime('now')
        RETURNING id
        """,
        (competitor_id, url, title, h1, meta_description,
         primary_keyword, word_count, published_at),
    ).fetchone()
    return row["id"]


def clear_page_keywords(conn: sqlite3.Connection, page_id: int) -> None:
    conn.execute("DELETE FROM page_keywords WHERE page_id = ?", (page_id,))


def insert_page_keyword(
    conn: sqlite3.Connection,
    page_id: int,
    keyword_id: int,
    source: str,
    score: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO page_keywords (page_id, keyword_id, source, score)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (page_id, keyword_id, source) DO UPDATE
            SET score = excluded.score
        """,
        (page_id, keyword_id, source, score),
    )


def query(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return conn.execute(sql, tuple(params)).fetchall()
