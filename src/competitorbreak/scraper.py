"""Blog discovery + post scraping with requests + BeautifulSoup.

Discovery strategy for a domain:
  1. robots.txt -> declared sitemaps
  2. common sitemap locations (/sitemap.xml, /sitemap_index.xml, ...)
  3. fallback: crawl the blog index page and collect same-domain links

Only URLs matching the configured blog path patterns are kept. Each post is then
fetched and parsed into title / headings / tags / meta / body text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; CompetitorBreakBot/0.1; competitive-research)"
)

COMMON_SITEMAPS = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/blog/sitemap.xml",
    "/post-sitemap.xml",
    "/sitemap/sitemap-index.xml",
]


@dataclass
class Post:
    url: str
    title: str | None = None
    h1: str | None = None
    headings: list[str] = field(default_factory=list)
    meta_description: str | None = None
    meta_keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    published_at: str | None = None
    body: str = ""
    word_count: int = 0


class Fetcher:
    """HTTP client with polite delay, robots.txt enforcement, and an optional
    Playwright (headless Chromium) fallback for JavaScript-rendered pages."""

    def __init__(
        self,
        delay: float = 1.0,
        respect_robots: bool = True,
        timeout: int = 20,
        use_playwright: bool = True,
        render_threshold: int = 120,
    ):
        self.delay = delay
        self.respect_robots = respect_robots
        self.timeout = timeout
        self.use_playwright = use_playwright
        # Below this word count a page is assumed to be JS-rendered and re-fetched.
        self.render_threshold = render_threshold
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request = 0.0
        self._pw = None
        self._browser = None

    def _sleep(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                # Fetch robots.txt with our own session (and User-Agent) rather
                # than RobotFileParser.read(), which uses urllib's default UA.
                # CDN/WAF-fronted sites (e.g. Akamai) often 403 that default UA,
                # which makes the parser silently disallow *everything* even when
                # robots.txt actually permits us. Fetching via the session gets
                # the real rules.
                resp = self.session.get(
                    urljoin(base, "/robots.txt"),
                    timeout=self.timeout,
                    headers={"Accept": "text/plain"},
                )
                if resp.status_code == 404:
                    rp = None  # no robots.txt -> allow all
                elif resp.status_code >= 400:
                    rp.disallow_all = True  # forbidden/unavailable -> disallow
                else:
                    rp.parse(resp.text.splitlines())
            except requests.RequestException:  # unreachable robots -> treat as "allow"
                rp = None
            self._robots[base] = rp
        return self._robots[base]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(USER_AGENT, url)

    def get(self, url: str, accept: str = "text/html") -> requests.Response | None:
        if not self.allowed(url):
            return None
        self._sleep()
        try:
            resp = self.session.get(
                url, timeout=self.timeout, headers={"Accept": accept}
            )
        except requests.RequestException:
            return None
        if resp.status_code >= 400:
            return None
        return resp

    # --- Playwright fallback ---------------------------------------------

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright  # lazy import

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        return self._browser

    def render(self, url: str) -> str | None:
        """Return fully rendered HTML via headless Chromium, or None on failure."""
        if not self.allowed(url):
            return None
        self._sleep()
        try:
            browser = self._ensure_browser()
            page = browser.new_page(user_agent=USER_AGENT)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                page.wait_for_timeout(1500)  # give client-side JS time to render
                return page.content()
            finally:
                page.close()
        except Exception:
            return None

    def close(self) -> None:
        """Shut down the Playwright browser if it was started."""
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._browser = None
            self._pw = None


# --- discovery -----------------------------------------------------------

def _origin(domain: str) -> str:
    if domain.startswith("http"):
        parsed = urlparse(domain)
        return f"{parsed.scheme}://{parsed.netloc}"
    return f"https://{domain}"


def _is_blog(url: str, patterns: list[str]) -> bool:
    low = url.lower()
    return any(p.lower() in low for p in patterns)


def _sitemap_urls_from_robots(fetcher: Fetcher, origin: str) -> list[str]:
    resp = fetcher.get(urljoin(origin, "/robots.txt"), accept="text/plain")
    if resp is None:
        return []
    return [
        line.split(":", 1)[1].strip()
        for line in resp.text.splitlines()
        if line.lower().startswith("sitemap:")
    ]


def _parse_sitemap(fetcher: Fetcher, url: str, depth: int = 0) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls) from a sitemap XML."""
    if depth > 4:
        return [], []
    resp = fetcher.get(url, accept="application/xml")
    if resp is None:
        return [], []
    soup = BeautifulSoup(resp.content, "xml")
    nested = [loc.get_text(strip=True) for loc in soup.select("sitemap > loc")]
    pages = [loc.get_text(strip=True) for loc in soup.select("url > loc")]
    # Some sitemaps nest <loc> directly without sitemap/url wrappers.
    if not nested and not pages:
        pages = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    return pages, nested


def discover_all_pages(fetcher: Fetcher, domain: str, max_pages: int) -> list[str]:
    """Return ALL same-site page URLs from the domain's sitemap(s) — no blog
    filter — so the whole website can be crawled, up to max_pages."""
    origin = _origin(domain)
    host = urlparse(origin).netloc.lower().removeprefix("www.")
    found: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if not url or url in seen:
            return
        try:
            h = urlparse(url).netloc.lower().removeprefix("www.")
        except ValueError:
            return
        if h != host:  # same registrable host only (skip external links)
            return
        seen.add(url)
        found.append(url)

    queue: list[str] = list(_sitemap_urls_from_robots(fetcher, origin))
    queue.extend(urljoin(origin, path) for path in COMMON_SITEMAPS)
    visited_maps: set[str] = set()
    while queue and len(found) < max_pages:
        sm = queue.pop(0)
        if sm in visited_maps:
            continue
        visited_maps.add(sm)
        pages, nested = _parse_sitemap(fetcher, sm)
        for url in pages:
            add(url)
            if len(found) >= max_pages:
                break
        queue.extend(n for n in nested if n not in visited_maps)
    return found[:max_pages]


def discover_posts(
    fetcher: Fetcher,
    domain: str,
    blog_patterns: list[str],
    max_pages: int,
    blog_url: str | None = None,
) -> list[str]:
    """Return a de-duplicated list of blog post URLs for a domain."""
    origin = _origin(domain)
    found: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url and url not in seen and _is_blog(url, blog_patterns):
            seen.add(url)
            found.append(url)

    # Build the sitemap queue.
    queue: list[str] = []
    if blog_url and blog_url.endswith(".xml"):
        queue.append(blog_url)
    queue.extend(_sitemap_urls_from_robots(fetcher, origin))
    queue.extend(urljoin(origin, path) for path in COMMON_SITEMAPS)

    visited_maps: set[str] = set()
    while queue and len(found) < max_pages:
        sm = queue.pop(0)
        if sm in visited_maps:
            continue
        visited_maps.add(sm)
        pages, nested = _parse_sitemap(fetcher, sm)
        for url in pages:
            add(url)
        queue.extend(n for n in nested if n not in visited_maps)

    # Fallback: crawl a blog index page for same-domain blog links.
    if len(found) < max_pages:
        index = blog_url if (blog_url and not blog_url.endswith(".xml")) else None
        if index is None:
            for pat in blog_patterns:
                index = urljoin(origin, pat)
                break
        html = None
        if index:
            resp = fetcher.get(index)
            if resp is not None:
                html = resp.text
            # JS-rendered listing: retry with Playwright if few links came back.
            if fetcher.use_playwright and (html is None or html.count("href") < 10):
                html = fetcher.render(index) or html
        if html:
            soup = BeautifulSoup(html, "lxml")
            host = urlparse(origin).netloc
            for a in soup.find_all("a", href=True):
                url = urljoin(origin, a["href"]).split("#")[0]
                if urlparse(url).netloc == host:
                    add(url)

    return found[:max_pages]


# --- parsing -------------------------------------------------------------

def _meta(soup: BeautifulSoup, **attrs) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def parse_post(url: str, html: str) -> Post:
    soup = BeautifulSoup(html, "lxml")
    post = Post(url=url)

    post.title = (
        (soup.title.string.strip() if soup.title and soup.title.string else None)
        or _meta(soup, property="og:title")
    )

    h1 = soup.find("h1")
    post.h1 = h1.get_text(strip=True) if h1 else None

    post.meta_description = _meta(soup, attrs={"name": "description"}) or _meta(
        soup, property="og:description"
    )

    mk = _meta(soup, attrs={"name": "keywords"})
    if mk:
        post.meta_keywords = [k.strip() for k in mk.split(",") if k.strip()]

    post.published_at = (
        _meta(soup, property="article:published_time")
        or _meta(soup, attrs={"name": "date"})
    )
    if not post.published_at:
        t = soup.find("time")
        if t and t.get("datetime"):
            post.published_at = t["datetime"].strip()

    # Article tags (rel=tag links, article:tag meta, common class names).
    tags: list[str] = []
    for a in soup.find_all("a", rel=True):
        if "tag" in (a.get("rel") or []):
            txt = a.get_text(strip=True)
            if txt:
                tags.append(txt)
    for m in soup.find_all("meta", attrs={"property": "article:tag"}):
        if m.get("content"):
            tags.append(m["content"].strip())
    post.tags = list(dict.fromkeys(tags))  # dedupe, keep order

    # Headings (h1-h3) as strong keyword signals.
    post.headings = [
        h.get_text(" ", strip=True)
        for h in soup.find_all(["h1", "h2", "h3"])
        if h.get_text(strip=True)
    ]

    # Body text from the main content container.
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()
    container = soup.find("article") or soup.find("main") or soup.body or soup
    post.body = container.get_text(" ", strip=True)
    post.word_count = len(post.body.split())

    return post


def scrape_post(fetcher: Fetcher, url: str) -> Post | None:
    """Fetch + parse a post. Falls back to Playwright rendering when the plain
    HTTP response looks empty/JS-rendered (word count below the threshold)."""
    post: Post | None = None
    resp = fetcher.get(url)
    if resp is not None and "html" in resp.headers.get("Content-Type", "").lower():
        post = parse_post(url, resp.text)

    thin = post is None or post.word_count < fetcher.render_threshold
    if thin and fetcher.use_playwright:
        rendered = fetcher.render(url)
        if rendered:
            rendered_post = parse_post(url, rendered)
            # Keep whichever version has more content.
            if post is None or rendered_post.word_count > post.word_count:
                post = rendered_post
    return post
