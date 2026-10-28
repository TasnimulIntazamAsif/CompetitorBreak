"""Reverse lookup: given ONE competitor domain, discover the keywords its blog
targets (from its own pages), then use the LLM web-search ranking to check where
that domain shows up for each keyword — and which of its pages is ranking (blog,
homepage, or other).

Honest limitation: the LLM gives an APPROXIMATE position/visibility read, not a
real Google rank. For exact "domain ranks for keyword X at position Y" data you
need a SERP database (Ahrefs / DataForSEO). This is the closest claude-cli can do.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Callable
from urllib.parse import urlparse

from . import keywords
from .llm_search import LLMSearch
from .scraper import Fetcher, discover_all_pages, discover_posts, scrape_post
from .serp import registrable_domain

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _norm_domain(d: str | None) -> str:
    if not d:
        return ""
    d = d.strip().lower()
    return d[4:] if d.startswith("www.") else d


_BLOG_HINTS = (
    "/blog", "/post", "/article", "/news", "/insights", "/resources",
    "/guide", "/stories", "/knowledge",
)


def classify_url(url: str) -> str:
    """Rough guess of what kind of page is ranking, from its URL path."""
    path = (urlparse(url).path or "/").lower()
    if path in ("", "/"):
        return "homepage"
    if any(h in path for h in _BLOG_HINTS):
        return "blog"
    # A dated path (/2024/05/...) is almost always a blog post / article.
    if re.search(r"/(?:19|20)\d\d/", path):
        return "blog"
    return "other"


def _extract_json_array(text: str) -> list:
    """Pull a JSON array out of a (possibly prose-wrapped) LLM answer."""
    if not text:
        return []
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        v = json.loads(t)
        if isinstance(v, list):
            return v
    except ValueError:
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            try:
                v = json.loads(m.group(0))
                if isinstance(v, list):
                    return v
            except ValueError:
                pass
    return []


def _reverse_prompt(domain: str, max_keywords: int) -> str:
    example = '[{"keyword": "example keyword", "position": 1, "url": "https://' + domain + '/page"}]'
    return (
        "You are an SEO analyst with a live web search tool. You MUST use web search to "
        "check real Google results — do NOT answer from memory. Find the keywords that "
        f'the website "{domain}" currently ranks for in Google organic search, focusing '
        "on keywords where one of its pages appears on the first page (positions 1-10). "
        "For each keyword give: the keyword, the approximate ranking position (1 = the "
        f"very top), and the exact URL of the page on {domain} that ranks for it. "
        "Respond with ONLY a JSON array and nothing else — no prose, no markdown fences:\n"
        + example + "\n"
        f"Use only real keywords and real URLs on {domain}. If you cannot verify a "
        f"ranking from search, omit it. Return up to {max_keywords}, best position first."
    )


def reverse_keywords(
    llm: LLMSearch,
    domain: str,
    *,
    max_keywords: int = 15,
    fetcher: Fetcher | None = None,
    max_page_keywords: int = 6,
    log: Logger = _noop,
) -> list[dict]:
    """Ask claude (via web search) directly which keywords `domain` ranks for and
    which page ranks for each. When a `fetcher` is given, each ranking page is
    scraped to add the ranking keyword's density and the page's top keywords."""
    self_reg = _norm_domain(domain)
    prompt = _reverse_prompt(domain, max_keywords)
    log(f"  asking claude (web search) what {domain} ranks for ...")
    text = llm.raw(prompt)  # raw text answer (prompt sent via stdin)

    rows: list[dict] = []
    seen: set[str] = set()
    for it in _extract_json_array(text):
        if not isinstance(it, dict):
            continue
        kw = (it.get("keyword") or "").strip()
        if not kw or kw.lower() in seen:
            continue
        url = it.get("url")
        # Drop rows whose URL is on a different domain (hallucinated / wrong site).
        if url and self_reg and _norm_domain(registrable_domain(url)) != self_reg:
            url = None
        pos = it.get("position") if isinstance(it.get("position"), int) else None
        seen.add(kw.lower())
        rows.append({
            "keyword": kw, "found": True, "position": pos, "url": url,
            "page_type": classify_url(url) if url else None, "title": None,
            "word_count": None, "kw_count": None, "kw_density": None, "keywords": [],
        })
    rows.sort(key=lambda r: (r["position"] is None, r["position"] or 9999))

    if fetcher is not None:
        _add_density(fetcher, rows, max_page_keywords=max_page_keywords, log=log)
    return rows


def _add_density(
    fetcher: Fetcher, rows: list[dict], *, max_page_keywords: int, log: Logger
) -> None:
    """Scrape each ranking URL and fill in the ranking keyword's density plus the
    page's top keywords. Pages are fetched once and cached across rows."""
    cache: dict[str, object] = {}
    for r in rows:
        url = r.get("url")
        if not url:
            continue
        if url not in cache:
            log(f"  scraping {url} for keyword density ...")
            cache[url] = scrape_post(fetcher, url)
        post = cache[url]
        if post is None or post.word_count < 30:
            continue
        r["word_count"] = post.word_count
        cnt = keywords.count_occurrences(post.body, r["keyword"])
        r["kw_count"] = cnt
        r["kw_density"] = keywords.density(cnt, post.word_count)
        ex = keywords.extract(
            title=post.title, headings=post.headings, body=post.body,
            url=post.url, tags=post.tags, meta_keywords=post.meta_keywords,
        )
        page_kws = []
        for term, _score in ex.candidates[:max_page_keywords]:
            c = keywords.count_occurrences(post.body, term)
            page_kws.append(
                {"term": term, "count": c, "density": keywords.density(c, post.word_count)}
            )
        r["keywords"] = page_kws


def discover_keywords(
    fetcher: Fetcher,
    domain: str,
    *,
    blog_patterns: list[str],
    max_pages: int,
    max_keywords: int,
    log: Logger = _noop,
) -> list[str]:
    """Scrape the domain's blog posts and return the keywords they target —
    each post's primary keyword first, then top content candidates to fill up."""
    log(f"  discovering posts for {domain} ...")
    urls = discover_posts(fetcher, domain, blog_patterns, max_pages)
    if not urls:
        # No detectable blog (non-standard path, or the site is down/maintenance):
        # fall back to the homepage so we still get some candidate keywords.
        home = domain if domain.startswith("http") else f"https://{domain.strip().strip('/')}"
        log(f"  no blog posts found; falling back to homepage {home}")
        urls = [home]
    log(f"  {len(urls)} candidate page(s); extracting keywords ...")

    # Primaries (title-derived target of each post) and the author's own tags /
    # meta keywords are the real intended keywords. Raw body n-grams are noisy
    # ("labor", "men and women"), so we only fall back to the multi-word ones.
    primaries: list[str] = []
    tags: list[str] = []
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(bucket: list[str], term: str | None) -> None:
        if not term:
            return
        t = term.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            bucket.append(t)

    for url in urls:
        post = scrape_post(fetcher, url)
        if post is None or post.word_count < 50:
            continue
        kw = keywords.extract(
            title=post.title, headings=post.headings, body=post.body,
            url=post.url, tags=post.tags, meta_keywords=post.meta_keywords,
        )
        _add(primaries, kw.primary)
        for t in getattr(kw, "explicit", []) or []:
            _add(tags, t)
        # multi-word candidates only (a single generic word like "labor" is useless)
        for term, _score in kw.candidates[:4]:
            if len(term.split()) >= 2:
                _add(candidates, term)

    ordered = primaries + tags + candidates
    return ordered[:max_keywords]


def analyze_competitor(
    llm: LLMSearch,
    fetcher: Fetcher,
    domain: str,
    *,
    blog_patterns: list[str],
    max_pages: int = 8,
    max_keywords: int = 8,
    log: Logger = _noop,
) -> dict:
    """For `domain`, find its target keywords then check where it ranks for each.

    Returns {"domain", "rows": [{keyword, found, position, url, page_type, title}]}
    sorted so keywords the domain ranks for (best position first) come first."""
    self_reg = _norm_domain(domain)
    kws = discover_keywords(
        fetcher, domain, blog_patterns=blog_patterns,
        max_pages=max_pages, max_keywords=max_keywords, log=log,
    )

    rows: list[dict] = []
    for term in kws:
        log(f"  rank check: {term!r}")
        results = llm.search(term)
        hit = next((r for r in results if _norm_domain(r.domain) == self_reg), None)
        if hit is not None:
            rows.append({
                "keyword": term, "found": True, "position": hit.position,
                "url": hit.url, "page_type": classify_url(hit.url), "title": hit.title,
            })
        else:
            rows.append({
                "keyword": term, "found": False, "position": None,
                "url": None, "page_type": None, "title": None,
            })

    # Found keywords first (best position first), then the not-found ones.
    rows.sort(key=lambda r: (not r["found"], r["position"] or 10_000))
    return {"domain": domain, "rows": rows}


def rank_keywords(
    llm: LLMSearch,
    terms: list[str],
    *,
    track_domains: list[str],
    log: Logger = _noop,
) -> list[dict]:
    """For each user-supplied keyword, run one SERP search and record where each
    tracked domain (e.g. ibos.io + a competitor) ranks, and which page.

    Returns [{keyword, ranks: {domain: {position, url, page_type} | None}}].
    The llm should be built with self_domain=track_domains so the prompt hunts
    for every tracked domain explicitly."""
    regs = {d: _norm_domain(d) for d in track_domains}
    rows: list[dict] = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        log(f"  rank check: {term!r}")
        results = llm.search(term)
        ranks: dict[str, dict | None] = {}
        for d, reg in regs.items():
            hit = next((r for r in results if _norm_domain(r.domain) == reg), None)
            ranks[d] = (
                {"position": hit.position, "url": hit.url,
                 "page_type": classify_url(hit.url)}
                if hit is not None else None
            )
        rows.append({"keyword": term, "ranks": ranks})
    return rows


def ranks_to_markdown(
    terms_rows: list[dict], track_domains: list[str], source: str = "claude-cli"
) -> str:
    """Render the user-keyword rank check: one row per keyword, one column per
    tracked domain showing 'position (page type)'."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Keyword rank comparison",
        "",
        f"_Source: {source} · {len(terms_rows)} keywords · {now}_",
        "",
        "> Positions are the LLM's approximate read of Google, not exact rank-tracker data.",
        "",
        "| Keyword | " + " | ".join(track_domains) + " |",
        "|---|" + "|".join("---" for _ in track_domains) + "|",
    ]
    for row in terms_rows:
        cells = []
        for d in track_domains:
            info = row["ranks"].get(d)
            if info:
                cells.append(f"#{info['position']} / 100 ({info['page_type']})")
            else:
                cells.append("—")
        lines.append(f"| {row['keyword']} | " + " | ".join(cells) + " |")
    lines.append("")
    # URL footnotes so the ranking page for each cell is recoverable.
    lines.append("## Ranking URLs")
    lines.append("")
    for row in terms_rows:
        for d in track_domains:
            info = row["ranks"].get(d)
            if info:
                lines.append(f"- **{row['keyword']}** — {d}: {info['url']}")
    lines.append("")
    return "\n".join(lines)


def crawl_keywords(
    fetcher: Fetcher,
    domain: str,
    *,
    blog_patterns: list[str],
    max_pages: int = 15,
    max_keywords: int = 10,
    all_pages: bool = False,
    log: Logger = _noop,
) -> list[dict]:
    """Crawl a site's own pages and report, per page, the keywords it is densest
    in — i.e. what each page is optimised for — with that page's URL.

    With all_pages=True, crawls EVERY page in the sitemap (up to max_pages),
    otherwise just blog/content pages.

    Returns [{url, title, word_count, primary, keywords:[{term,count,density}]}].
    No LLM, no rank data: this reads the site's actual content, so it is reliable."""
    log(f"  discovering pages for {domain} ...")
    if all_pages:
        urls = discover_all_pages(fetcher, domain, max_pages)
    else:
        urls = discover_posts(fetcher, domain, blog_patterns, max_pages)
    if not urls:
        home = domain if domain.startswith("http") else f"https://{domain.strip().strip('/')}"
        log(f"  no blog pages found; falling back to homepage {home}")
        urls = [home]
    log(f"  {len(urls)} page(s); extracting keyword density ...")

    pages: list[dict] = []
    for url in urls:
        post = scrape_post(fetcher, url)
        if post is None or post.word_count < 50:
            continue
        kw = keywords.extract(
            title=post.title, headings=post.headings, body=post.body,
            url=post.url, tags=post.tags, meta_keywords=post.meta_keywords,
        )
        top = []
        for term, _score in kw.candidates[:max_keywords]:
            c = keywords.count_occurrences(post.body, term)
            top.append(
                {"term": term, "count": c, "density": keywords.density(c, post.word_count)}
            )
        pages.append({
            "url": post.url, "title": post.title, "word_count": post.word_count,
            "primary": kw.primary, "keywords": top,
        })
        log(f"  {post.url} — {len(top)} keywords")
    return pages


def crawl_keyword_index(
    fetcher: Fetcher,
    domain: str,
    *,
    blog_patterns: list[str],
    all_pages: bool = False,
    max_pages: int = 15,
    top_per_page: int = 20,
    min_count: int = 2,
    min_words: int = 2,
    log: Logger = _noop,
) -> list[dict]:
    """Crawl the site and build a keyword -> URLs index: for each specific keyword,
    which pages contain it and how often (frequency).

    Returns [{keyword, total, pages, urls:[{url, count}]}], sorted by total
    frequency. "Specific" = multi-word phrases appearing at least `min_count`
    times on a page, so generic single words are filtered out."""
    log(f"  discovering pages for {domain} ...")
    if all_pages:
        urls = discover_all_pages(fetcher, domain, max_pages)
    else:
        urls = discover_posts(fetcher, domain, blog_patterns, max_pages)
    if not urls:
        home = domain if domain.startswith("http") else f"https://{domain.strip().strip('/')}"
        urls = [home]
    log(f"  {len(urls)} page(s); building keyword index ...")

    # keyword -> {url: count}
    index: dict[str, dict[str, int]] = {}
    for url in urls:
        post = scrape_post(fetcher, url)
        if post is None or post.word_count < 50:
            continue
        ex = keywords.extract(
            title=post.title, headings=post.headings, body=post.body,
            url=post.url, tags=post.tags, meta_keywords=post.meta_keywords,
        )
        for term, _score in ex.candidates[:top_per_page]:
            if len(term.split()) < min_words:
                continue  # keep specific multi-word phrases only
            c = keywords.count_occurrences(post.body, term)
            if c < min_count:
                continue
            index.setdefault(term, {})[post.url] = c
        log(f"  indexed {post.url}")

    rows: list[dict] = []
    for term, urlmap in index.items():
        pairs = sorted(
            ({"url": u, "count": c} for u, c in urlmap.items()),
            key=lambda x: -x["count"],
        )
        rows.append({
            "keyword": term, "total": sum(urlmap.values()),
            "pages": len(urlmap), "urls": pairs,
        })
    # Most-used keywords first; ties broken by how many pages use them.
    rows.sort(key=lambda r: (-r["total"], -r["pages"]))
    return rows


def index_to_markdown(domain: str, rows: list[dict]) -> str:
    """Render the keyword index in the competitor spreadsheet format:
    Keyword | URL | Frequency, keyword spanning its URL rows."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        f"# Competitor keyword index: {domain}",
        "",
        f"_{len(rows)} keywords · {now}_",
        "",
        "| Keyword | URL | Frequency |",
        "|---|---|---:|",
    ]
    for r in rows:
        first = True
        for u in r["urls"]:
            kw = r["keyword"] if first else ""
            lines.append(f"| {kw} | {u['url']} | {u['count']} |")
            first = False
    lines.append("")
    return "\n".join(lines)


def crawl_to_markdown(domain: str, pages: list[dict]) -> str:
    """Render the site crawl: each page with its top keywords by density."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        f"# Site keyword crawl: {domain}",
        "",
        f"_{len(pages)} pages · {now}_",
        "",
    ]
    for p in pages:
        lines.append(f"## {p['title'] or p['url']}")
        lines.append("")
        lines.append(f"- **URL:** {p['url']}")
        if p.get("primary"):
            lines.append(f"- **Primary keyword:** {p['primary']}")
        if p.get("word_count") is not None:
            lines.append(f"- **Words:** {p['word_count']}")
        if p.get("keywords"):
            lines.append("")
            lines.append("| Keyword | Count | Density |")
            lines.append("|---|---:|---:|")
            for k in p["keywords"]:
                lines.append(f"| {k['term']} | {k['count']} | {k['density']}% |")
        lines.append("")
    return "\n".join(lines)


def to_markdown(domain: str, rows: list[dict], source: str = "claude-cli") -> str:
    """Render the competitor keyword-ranking check as a Markdown report."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    found = [r for r in rows if r["found"]]
    lines = [
        f"# Keyword ranking check: {domain}",
        "",
        f"_Source: {source} · {len(rows)} keywords · {len(found)} ranking · {now}_",
        "",
        "> Positions are the LLM's approximate read of Google, not exact rank-tracker data.",
        "",
        "| Keyword | Position | Count | Density | Page | URL |",
        "|---|---:|---:|---:|---|---|",
    ]
    for r in rows:
        pos = f"{r['position']} / 100" if r.get("position") is not None else "—"
        cnt = r.get("kw_count")
        cnt_s = str(cnt) if cnt is not None else "—"
        dens = r.get("kw_density")
        dens_s = f"{dens}%" if dens is not None else "—"
        page = r.get("page_type") or "—"
        url = r.get("url") or "_not found_"
        lines.append(f"| {r['keyword']} | {pos} | {cnt_s} | {dens_s} | {page} | {url} |")
    lines.append("")

    # Per-page breakdown: the top keywords actually on each ranking page.
    detailed = [r for r in rows if r.get("keywords")]
    if detailed:
        lines.append("## On-page keywords per ranking URL")
        lines.append("")
        for r in detailed:
            lines.append(f"### {r['keyword']} — {r['url']}")
            if r.get("word_count") is not None:
                lines.append(f"_{r['word_count']} words_")
            lines.append("")
            lines.append("| Keyword | Count | Density |")
            lines.append("|---|---:|---:|")
            for k in r["keywords"]:
                lines.append(f"| {k['term']} | {k['count']} | {k['density']}% |")
            lines.append("")
    return "\n".join(lines)
