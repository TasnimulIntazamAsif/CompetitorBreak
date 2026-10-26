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
    """Each scraped post with its inferred primary target keyword."""
    sql = """
        SELECT p.primary_keyword, p.word_count, p.published_at, p.title, p.url
          FROM pages p
          JOIN competitors c ON c.id = p.competitor_id
         WHERE c.domain = ?
         ORDER BY p.published_at DESC NULLS LAST, p.scraped_at DESC
         LIMIT ?
    """
    return db.query(conn, sql, [domain, limit])


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
