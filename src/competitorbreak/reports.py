"""Read-side queries over scraped data (SQLite).

Since there is no SERP/ranking source, "what competitors target" is derived from
the keywords their blog posts are optimised for (primary + tag signals).
"""

from __future__ import annotations

from typing import Any

import sqlite3

from . import db


def topics(
    conn: sqlite3.Connection, domain: str, limit: int = 50, min_posts: int = 1
) -> list[dict[str, Any]]:
    """Most-targeted keywords across a competitor's blog, by post count."""
    sql = """
        SELECT k.term,
               COUNT(DISTINCT p.id) AS posts,
               SUM(CASE WHEN pk.source = 'primary' THEN 1 ELSE 0 END) AS as_primary
          FROM page_keywords pk
          JOIN keywords k ON k.id = pk.keyword_id
          JOIN pages p ON p.id = pk.page_id
          JOIN competitors c ON c.id = p.competitor_id
         WHERE c.domain = ? AND pk.source IN ('primary', 'tag')
         GROUP BY k.term
        HAVING COUNT(DISTINCT p.id) >= ?
         ORDER BY posts DESC, as_primary DESC
         LIMIT ?
    """
    return db.query(conn, sql, [domain, min_posts, limit])


def posts(conn: sqlite3.Connection, domain: str, limit: int = 50) -> list[dict[str, Any]]:
    """Each scraped post with its primary target keyword, its occurrence count
    and density in the post body."""
    sql = """
        SELECT p.primary_keyword, pk.count AS kw_count, pk.density AS kw_density,
               p.word_count, p.published_at, p.title, p.url
          FROM pages p
          JOIN competitors c ON c.id = p.competitor_id
          LEFT JOIN page_keywords pk ON pk.page_id = p.id AND pk.source = 'primary'
         WHERE c.domain = ?
         ORDER BY p.published_at DESC NULLS LAST, p.scraped_at DESC
         LIMIT ?
    """
    return db.query(conn, sql, [domain, limit])


def keyword_density(
    conn: sqlite3.Connection, domain: str, keyword: str, limit: int = 50
) -> list[dict[str, Any]]:
    """For a keyword, how many times it is used in each of a domain's posts."""
    sql = """
        SELECT p.title, p.url, p.word_count,
               MAX(pk.count) AS count, MAX(pk.density) AS density
          FROM page_keywords pk
          JOIN keywords k ON k.id = pk.keyword_id
          JOIN pages p ON p.id = pk.page_id
          JOIN competitors c ON c.id = p.competitor_id
         WHERE c.domain = ? AND k.term = ?
         GROUP BY p.id
         ORDER BY count DESC NULLS LAST
         LIMIT ?
    """
    return db.query(conn, sql, [domain, keyword.strip().lower(), limit])


def post_keywords(
    conn: sqlite3.Connection, url: str, limit: int = 50
) -> list[dict[str, Any]]:
    """All target keywords of a single post with their count and density."""
    sql = """
        SELECT k.term, pk.source, pk.count, pk.density
          FROM page_keywords pk
          JOIN keywords k ON k.id = pk.keyword_id
          JOIN pages p ON p.id = pk.page_id
         WHERE p.url = ?
         ORDER BY pk.count DESC NULLS LAST, pk.score DESC NULLS LAST
         LIMIT ?
    """
    return db.query(conn, sql, [url, limit])


def gaps(
    conn: sqlite3.Connection, competitor_domain: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Keywords competitors target that our own domain does not.

    Compares the high-confidence target keyword sets (primary + tag).
    """
    params: list[Any] = []
    comp_clause = "c.is_self = 0"
    if competitor_domain:
        comp_clause = "c.domain = ?"
        params.append(competitor_domain)
    params.append(limit)
    sql = f"""
        WITH self_terms AS (
            SELECT DISTINCT pk.keyword_id
              FROM page_keywords pk
              JOIN pages p ON p.id = pk.page_id
              JOIN competitors c ON c.id = p.competitor_id
             WHERE c.is_self = 1 AND pk.source IN ('primary', 'tag')
        )
        SELECT k.term,
               GROUP_CONCAT(DISTINCT c.domain) AS competitors,
               COUNT(DISTINCT p.id) AS posts
          FROM page_keywords pk
          JOIN keywords k ON k.id = pk.keyword_id
          JOIN pages p ON p.id = pk.page_id
          JOIN competitors c ON c.id = p.competitor_id
         WHERE {comp_clause}
           AND pk.source IN ('primary', 'tag')
           AND pk.keyword_id NOT IN (SELECT keyword_id FROM self_terms)
         GROUP BY k.term
         ORDER BY posts DESC
         LIMIT ?
    """
    return db.query(conn, sql, params)


def rankings(
    conn: sqlite3.Connection, domain: str, limit: int = 100
) -> list[dict[str, Any]]:
    """Keywords a tracked domain ranks for in Google (latest position per keyword)."""
    sql = """
        WITH latest AS (
            SELECT sr.keyword_id, sr.position, sr.url, sr.captured_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY sr.keyword_id
                       ORDER BY sr.serp_run_id DESC, sr.captured_at DESC
                   ) AS rn
              FROM serp_results sr
              JOIN competitors c ON c.id = sr.competitor_id
             WHERE c.domain = ?
        )
        SELECT k.term AS keyword, l.position, l.url, l.captured_at
          FROM latest l
          JOIN keywords k ON k.id = l.keyword_id
         WHERE l.rn = 1
         ORDER BY l.position ASC NULLS LAST
         LIMIT ?
    """
    return db.query(conn, sql, [domain, limit])


def serp_keyword(
    conn: sqlite3.Connection, term: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Full latest SERP for a keyword — who ranks where (tracked domains flagged)."""
    sql = """
        SELECT sr.position, sr.domain, sr.url, sr.title,
               c.domain AS tracked
          FROM serp_results sr
          JOIN keywords k ON k.id = sr.keyword_id
          LEFT JOIN competitors c ON c.id = sr.competitor_id
         WHERE k.term = ?
           AND sr.serp_run_id = (
               SELECT MAX(s2.serp_run_id)
                 FROM serp_results s2
                 JOIN keywords k2 ON k2.id = s2.keyword_id
                WHERE k2.term = ?
           )
         ORDER BY sr.position
         LIMIT ?
    """
    return db.query(conn, sql, [term, term, limit])


def ranking_history(
    conn: sqlite3.Connection, domain: str, term: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Position over time for a tracked domain + keyword."""
    sql = """
        SELECT sr.captured_at, sr.position, sr.url
          FROM serp_results sr
          JOIN competitors c ON c.id = sr.competitor_id
          JOIN keywords k ON k.id = sr.keyword_id
         WHERE c.domain = ? AND k.term = ?
         ORDER BY sr.captured_at
         LIMIT ?
    """
    return db.query(conn, sql, [domain, term, limit])


def overlap(
    conn: sqlite3.Connection, competitor_domain: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Keywords both our domain and a competitor target."""
    sql = """
        WITH self_terms AS (
            SELECT DISTINCT pk.keyword_id
              FROM page_keywords pk
              JOIN pages p ON p.id = pk.page_id
              JOIN competitors c ON c.id = p.competitor_id
             WHERE c.is_self = 1 AND pk.source IN ('primary', 'tag')
        )
        SELECT k.term, COUNT(DISTINCT p.id) AS their_posts
          FROM page_keywords pk
          JOIN keywords k ON k.id = pk.keyword_id
          JOIN pages p ON p.id = pk.page_id
          JOIN competitors c ON c.id = p.competitor_id
         WHERE c.domain = ?
           AND pk.source IN ('primary', 'tag')
           AND pk.keyword_id IN (SELECT keyword_id FROM self_terms)
         GROUP BY k.term
         ORDER BY their_posts DESC
         LIMIT ?
    """
    return db.query(conn, sql, [competitor_domain, limit])
