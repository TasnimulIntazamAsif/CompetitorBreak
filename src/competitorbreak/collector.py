"""Orchestration: discover + scrape a competitor's blog and persist keywords."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import psycopg

from . import db, keywords
from .scraper import Fetcher, discover_posts, scrape_post

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


@dataclass
class CollectResult:
    posts_found: int = 0
    posts_scraped: int = 0


def collect_competitor(
    conn: psycopg.Connection,
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

            if kw.primary:
                kid = db.upsert_keyword(conn, kw.primary)
                db.insert_page_keyword(conn, page_id, kid, "primary", score=None)

            explicit_set = {e.lower() for e in kw.explicit}
            for term in kw.explicit:
                kid = db.upsert_keyword(conn, term.lower())
                db.insert_page_keyword(conn, page_id, kid, "tag", score=None)

            for term, score in kw.candidates:
                if term in explicit_set:
                    continue
                kid = db.upsert_keyword(conn, term)
                db.insert_page_keyword(conn, page_id, kid, "content", score=score)

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
