"""Orchestration: discover + scrape a competitor's blog and persist keywords."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import sqlite3

from . import db, keywords
from .scraper import Fetcher, discover_posts, scrape_post
from .serp import SerpBlocked, SerpScraper, registrable_domain

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


@dataclass
class CollectResult:
    posts_found: int = 0
    posts_scraped: int = 0


def collect_competitor(
    conn: sqlite3.Connection,
    fetcher: Fetcher,
    competitor: dict,
    *,
    blog_patterns: list[str],
    max_pages: int,
    log: Logger = _noop,
) -> CollectResult:
    comp_id = competitor["id"]
    domain = competitor["domain"]
    result = CollectResult()
    run_id = db.start_run(conn, comp_id)
    conn.commit()  # make the in-progress run visible to the UI immediately

    try:
        log(f"  discovering blog posts for {domain} ...")
        urls = discover_posts(
            fetcher, domain, blog_patterns, max_pages,
            blog_url=competitor.get("blog_url"),
        )
        result.posts_found = len(urls)
        log(f"  found {len(urls)} candidate posts")

        for url in urls:
            post = scrape_post(fetcher, url)
            if post is None or post.word_count < 50:
                continue  # skip unreachable or near-empty pages

            kw = keywords.extract(
                title=post.title,
                headings=post.headings,
                body=post.body,
                url=post.url,
                tags=post.tags,
                meta_keywords=post.meta_keywords,
            )

            page_id = db.upsert_page(
                conn, comp_id, post.url, post.title, post.h1,
                post.meta_description, kw.primary, post.word_count, post.published_at,
            )
            db.clear_page_keywords(conn, page_id)

            def store(term: str, source: str, score: int | None) -> None:
                occ = keywords.count_occurrences(post.body, term)
                dens = keywords.density(occ, post.word_count)
                kid = db.upsert_keyword(conn, term)
                db.insert_page_keyword(
                    conn, page_id, kid, source, score=score, count=occ, density=dens
                )

            if kw.primary:
                store(kw.primary, "primary", None)

            explicit_set = {e.lower() for e in kw.explicit}
            for term in kw.explicit:
                store(term.lower(), "tag", None)

            for term, score in kw.candidates:
                if term in explicit_set:
                    continue
                store(term, "content", score)

            result.posts_scraped += 1
            if result.posts_scraped % 10 == 0:
                log(f"  scraped {result.posts_scraped}/{len(urls)} ...")
                conn.commit()

        db.finish_run(
            conn, run_id, "success", result.posts_found, result.posts_scraped
        )
        log(f"  done: {result.posts_scraped} posts stored")
    except Exception as exc:  # noqa: BLE001
        db.finish_run(
            conn, run_id, "error", result.posts_found, result.posts_scraped, str(exc)
        )
        raise

    return result


@dataclass
class SerpCollectResult:
    keywords_checked: int = 0
    results_stored: int = 0
    blocked: bool = False


def collect_serp(
    conn: sqlite3.Connection,
    serp: SerpScraper,
    terms: list[dict],
    *,
    country: str,
    log: Logger = _noop,
) -> SerpCollectResult:
    """For each (keyword_id, term), scrape Google and store organic results,
    matching result domains to tracked competitors. Stops cleanly on a block."""
    result = SerpCollectResult()
    domain_map = db.competitor_domain_map(conn)
    run_id = db.start_serp_run(conn, country)
    conn.commit()  # make the in-progress run visible to the UI immediately
    try:
        for t in terms:
            term = t["term"]
            kw_id = t["keyword_id"]
            log(f"  SERP: {term!r}")
            try:
                rows = serp.search(term)
            except SerpBlocked as exc:
                db.finish_serp_run(conn, run_id, "blocked", result.keywords_checked, str(exc))
                result.blocked = True
                log(f"  blocked: {exc}")
                return result
            for r in rows:
                db.insert_serp_result(
                    conn, run_id, kw_id, r.position, r.domain, r.url, r.title,
                    competitor_id=domain_map.get(r.domain),
                )
                result.results_stored += 1
            result.keywords_checked += 1
            conn.commit()
        db.finish_serp_run(conn, run_id, "success", result.keywords_checked)
        log(f"  done: {result.keywords_checked} keywords, {result.results_stored} results")
    except Exception as exc:  # noqa: BLE001
        db.finish_serp_run(conn, run_id, "error", result.keywords_checked, str(exc))
        raise
    return result
