"""Query analysis: LLM web-search ranking -> scrape each ranking page -> on-page
keywords. For a query like "best HR software in Bangladesh", returns the ranked
websites serially, each with the keywords its ranking page is built around."""

from __future__ import annotations

import datetime

from typing import Callable

from . import keywords
from .llm_search import LLMSearch
from .scraper import Fetcher, scrape_post
from .serp import registrable_domain


def _norm_domain(d: str | None) -> str:
    if not d:
        return ""
    d = d.strip().lower()
    return d[4:] if d.startswith("www.") else d

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def analyze_query(
    llm: LLMSearch,
    fetcher: Fetcher,
    query: str,
    *,
    max_results: int = 5,
    max_keywords: int = 12,
    self_domain: str | None = None,
    log: Logger = _noop,
) -> list[dict]:
    """Return a ranked list of websites for `query`, each with on-page keywords.

    If `self_domain` is given (e.g. "ibos.io"), the row for that site is flagged
    with `is_self: True` so the caller can highlight it and compare its rank."""
    self_reg = _norm_domain(self_domain)
    log(f"  searching: {query!r}")
    # When comparing against our own site, search for it too — so it shows up even
    # if it ranks below `max_results`. We trim competitors to max_results below but
    # always keep the self row.
    ranked = llm.search(query)
    log(f"  {len(ranked)} ranked results")

    kept = ranked[:max_results]
    if self_reg:
        kept_self = any(_norm_domain(r.domain) == self_reg for r in kept)
        if not kept_self:
            extra = next(
                (r for r in ranked if _norm_domain(r.domain) == self_reg), None
            )
            if extra is not None:
                kept.append(extra)
    # Keep results in rank order: an appended self row (ibos.io) lands in its real
    # position slot, not stuck at the end.
    ranked = sorted(kept, key=lambda r: r.position)

    out: list[dict] = []
    for r in ranked:
        is_self = bool(self_reg) and _norm_domain(r.domain) == self_reg
        row: dict = {
            "position": r.position,
            "domain": r.domain,
            "url": r.url,
            "title": r.title,
            "primary_keyword": None,
            "word_count": None,
            "keywords": [],
            "is_self": is_self,
        }
        log(f"  #{r.position} scraping {r.domain}")
        post = scrape_post(fetcher, r.url)
        if post and post.word_count >= 30:
            ex = keywords.extract(
                title=post.title,
                headings=post.headings,
                body=post.body,
                url=post.url,
                tags=post.tags,
                meta_keywords=post.meta_keywords,
            )
            row["primary_keyword"] = ex.primary
            row["word_count"] = post.word_count
            kws = []
            for term, _score in ex.candidates[:max_keywords]:
                count = keywords.count_occurrences(post.body, term)
                kws.append(
                    {"term": term, "count": count,
                     "density": keywords.density(count, post.word_count)}
                )
            row["keywords"] = kws
        out.append(row)
    return out


def to_markdown(query: str, rows: list[dict], source: str = "llm") -> str:
    """Render analysis results as a Markdown report."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        f'# Query analysis: "{query}"',
        "",
        f"_Source: {source} · {len(rows)} results · {now}_",
        "",
    ]

    # Comparison note: where does our own site land vs. the competitors?
    self_row = next((r for r in rows if r.get("is_self")), None)
    if self_row:
        ahead = sum(
            1 for r in rows
            if not r.get("is_self") and r["position"] < self_row["position"]
        )
        lines.append(
            f"> **{self_row['domain']} ranks at position {self_row['position']} / 100** "
            f"for this query — {ahead} competitor(s) rank above it."
        )
        lines.append("")

    for r in rows:
        marker = " ⭐ (us)" if r.get("is_self") else ""
        lines.append(f"## Position {r['position']} / 100 — {r['domain']}{marker}")
        lines.append("")
        lines.append(f"- **URL:** {r['url']}")
        if r.get("title"):
            lines.append(f"- **Title:** {r['title']}")
        if r.get("primary_keyword"):
            lines.append(f"- **Primary keyword:** {r['primary_keyword']}")
        if r.get("word_count") is not None:
            lines.append(f"- **Words:** {r['word_count']}")
        kws = r.get("keywords") or []
        if kws:
            lines.append("")
            lines.append("| Keyword | Count | Density |")
            lines.append("|---|---:|---:|")
            for k in kws:
                lines.append(f"| {k['term']} | {k['count']} | {k['density']}% |")
        lines.append("")
    return "\n".join(lines)

