"""Database access layer (scrape pipeline). Supports SQLite (a local file, no
server) and PostgreSQL. The same functions work against both: a target starting
with ``postgres`` opens a Postgres connection wrapped in an adapter that
translates the SQLite-flavoured SQL (``?`` placeholders, ``datetime('now')``,
``GROUP_CONCAT``) to Postgres on the fly."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_PG_PATH = Path(__file__).resolve().parent / "schema_pg.sql"


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _is_postgres(target: str) -> bool:
    return target.startswith("postgres://") or target.startswith("postgresql://")


# --- Postgres adapter: make SQLite-flavoured SQL run on Postgres -------------

_GROUP_CONCAT_DISTINCT = re.compile(r"GROUP_CONCAT\(\s*DISTINCT\s+(.+?)\)", re.IGNORECASE | re.DOTALL)
_GROUP_CONCAT = re.compile(r"GROUP_CONCAT\(\s*(.+?)\)", re.IGNORECASE | re.DOTALL)


def _to_pg(sql: str) -> str:
    """Translate the SQLite SQL used in this module to PostgreSQL."""
    sql = sql.replace("?", "%s")
    sql = sql.replace("datetime('now')", "now()")
    sql = _GROUP_CONCAT_DISTINCT.sub(r"string_agg(DISTINCT (\1)::text, ',')", sql)
    sql = _GROUP_CONCAT.sub(r"string_agg((\1)::text, ',')", sql)
    return sql


class _PgConn:
    """Wraps a psycopg3 connection so db functions can keep calling
    ``conn.execute(sql, params).fetchone()`` with SQLite-style SQL."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: Iterable[Any] = ()):
        p = tuple(params) if params else None
        return self._conn.execute(_to_pg(sql), p)

    def executescript(self, script: str) -> None:
        # Strip SQL line comments first: the schema's header comments contain
        # ';' which would otherwise split a comment into a bogus statement.
        no_comments = re.sub(r"--[^\n]*", "", script)
        for stmt in no_comments.split(";"):
            if stmt.strip():
                self._conn.execute(stmt)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


@contextmanager
def connect(target: str) -> Iterator[Any]:
    """Open a DB connection (SQLite file path or Postgres URL); commit on
    success, rollback on error."""
    if _is_postgres(target):
        import psycopg
        from psycopg.rows import dict_row

        # A remote Postgres can be slow to accept the first connection after an
        # idle period (VPN / route warm-up), so allow a generous connect timeout.
        raw = psycopg.connect(target, row_factory=dict_row, connect_timeout=30)
        conn = _PgConn(raw)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    conn = sqlite3.connect(target, timeout=10)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # concurrent reads during a write
    conn.execute("PRAGMA busy_timeout = 10000")  # wait instead of erroring on lock
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: Any) -> None:
    if isinstance(conn, _PgConn):
        conn.executescript(SCHEMA_PG_PATH.read_text(encoding="utf-8"))
    else:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)


def _migrate(conn: Any) -> None:
    """Lightweight migrations for existing databases (ADD COLUMN IF NOT EXISTS
    on Postgres; PRAGMA-driven on SQLite)."""
    if isinstance(conn, _PgConn):
        conn.execute("ALTER TABLE page_keywords ADD COLUMN IF NOT EXISTS count INTEGER")
        conn.execute("ALTER TABLE page_keywords ADD COLUMN IF NOT EXISTS density REAL")
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(page_keywords)")}
    if "count" not in cols:
        conn.execute("ALTER TABLE page_keywords ADD COLUMN count INTEGER")
    if "density" not in cols:
        conn.execute("ALTER TABLE page_keywords ADD COLUMN density REAL")


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


def get_competitor_by_id(conn: sqlite3.Connection, comp_id: int) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM competitors WHERE id = ?", (comp_id,)
    ).fetchone()


def update_competitor(
    conn: sqlite3.Connection,
    comp_id: int,
    name: str | None,
    is_self: bool,
    blog_url: str | None,
) -> dict[str, Any] | None:
    conn.execute(
        """
        UPDATE competitors
           SET name = ?, is_self = ?, blog_url = ?
         WHERE id = ?
        """,
        (name, 1 if is_self else 0, blog_url, comp_id),
    )
    return get_competitor_by_id(conn, comp_id)


def delete_competitor(conn: sqlite3.Connection, comp_id: int) -> None:
    conn.execute("DELETE FROM competitors WHERE id = ?", (comp_id,))


# --- settings ------------------------------------------------------------

def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


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
    count: int | None = None,
    density: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO page_keywords (page_id, keyword_id, source, score, count, density)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (page_id, keyword_id, source) DO UPDATE
            SET score = excluded.score,
                count = excluded.count,
                density = excluded.density
        """,
        (page_id, keyword_id, source, score, count, density),
    )


# --- SERP (rankings) -----------------------------------------------------

def start_serp_run(conn: sqlite3.Connection, country: str) -> int:
    row = conn.execute(
        "INSERT INTO serp_runs (country, status) VALUES (?, 'running') RETURNING id",
        (country,),
    ).fetchone()
    return row["id"]


def finish_serp_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    keywords_checked: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE serp_runs
           SET status = ?, keywords_checked = ?, error = ?, finished_at = datetime('now')
         WHERE id = ?
        """,
        (status, keywords_checked, error, run_id),
    )


def insert_serp_result(
    conn: sqlite3.Connection,
    serp_run_id: int,
    keyword_id: int,
    position: int | None,
    domain: str | None,
    url: str | None,
    title: str | None,
    competitor_id: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO serp_results
            (serp_run_id, keyword_id, position, domain, url, title, competitor_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (serp_run_id, keyword_id, position, domain, url, title, competitor_id),
    )


def competitor_domain_map(conn: sqlite3.Connection) -> dict[str, int]:
    """Map registrable domain -> competitor id for matching SERP results."""
    out: dict[str, int] = {}
    for r in conn.execute("SELECT id, domain FROM competitors").fetchall():
        dom = r["domain"].lower()
        if dom.startswith("www."):
            dom = dom[4:]
        out[dom] = r["id"]
    return out


def target_terms(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Distinct high-confidence target terms (primary/tag) with their keyword_id,
    ranked by how many posts target them."""
    return conn.execute(
        """
        SELECT k.id AS keyword_id, k.term, COUNT(DISTINCT pk.page_id) AS posts
          FROM page_keywords pk
          JOIN keywords k ON k.id = pk.keyword_id
         WHERE pk.source IN ('primary', 'tag')
         GROUP BY k.id, k.term
         ORDER BY posts DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()


def query(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return conn.execute(sql, tuple(params)).fetchall()
