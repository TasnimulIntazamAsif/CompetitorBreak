"""Best-effort Google SERP scraping with Playwright (headless Chromium).

For a keyword, runs a Google search and records the organic results (position,
domain, URL, title). This is used to find where competitors rank.

IMPORTANT: scraping Google results is against Google's Terms of Service and is
operationally fragile — Google serves CAPTCHAs, rate-limits by IP, and varies
results by location. Keep query volume low, use a generous delay, and expect
blocks. For reliable data, a paid SERP/SEO API is the proper source.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class SerpResult:
    position: int
    url: str
    domain: str
    title: str | None


class SerpError(RuntimeError):
    pass


class SerpBlocked(SerpError):
    """Google returned a CAPTCHA / 'unusual traffic' interstitial."""


def registrable_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


class SerpScraper:
    def __init__(
        self,
        country: str = "us",
        lang: str = "en",
        delay: float = 4.0,
        timeout: int = 30,
        headless: bool = True,
        max_results: int = 20,
        solve_timeout: int = 180,
    ):
        self.country = country.lower()
        self.lang = lang
        self.delay = delay
        self.timeout = timeout
        self.headless = headless
        self.max_results = max_results
        # Non-headless only: how long to wait for the user to solve a CAPTCHA.
        self.solve_timeout = solve_timeout
        self._pw = None
        self._browser = None
        self._context = None
        self._last = 0.0

    def _ensure_context(self):
        if self._context is None:
            from playwright.sync_api import sync_playwright  # lazy import

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                user_agent=UA, locale=f"{self.lang}-{self.country.upper()}"
            )
        return self._context

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last
        wait = self.delay + random.uniform(0, self.delay)  # jitter
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last = time.monotonic()

    def _dismiss_consent(self, page) -> None:
        """Click Google's cookie-consent 'Accept all' if it appears."""
        for sel in (
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
            "button:has-text('Reject all')",
            "#L2AGLb",
        ):
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _is_blocked(self, page) -> bool:
        try:
            if "/sorry/" in page.url:
                return True
            return page.query_selector("form#captcha-form") is not None
        except Exception:
            return False

    def _wait_for_manual_solve(self, page) -> bool:
        """Non-headless: give the user time to solve the CAPTCHA in the visible
        window. Returns True once the block clears, False on timeout."""
        try:
            page.bring_to_front()
        except Exception:
            pass
        deadline = time.monotonic() + self.solve_timeout
        while time.monotonic() < deadline:
            time.sleep(2)
            self._dismiss_consent(page)
            if not self._is_blocked(page):
                return True
        return False

    def search(self, keyword: str) -> list[SerpResult]:
        """Return organic results for a keyword. Raises SerpBlocked on CAPTCHA.

        When running non-headless, a CAPTCHA pauses and waits for the user to
        solve it in the visible browser window (up to solve_timeout seconds)."""
        self._throttle()
        ctx = self._ensure_context()
        page = ctx.new_page()
        try:
            url = (
                "https://www.google.com/search?"
                f"q={quote_plus(keyword)}&num={self.max_results}"
                f"&hl={self.lang}&gl={self.country}"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            self._dismiss_consent(page)

            if self._is_blocked(page):
                if self.headless:
                    raise SerpBlocked(
                        f"Google CAPTCHA for '{keyword}'. Turn off 'Headless browser' "
                        "in Settings and re-run to solve it yourself."
                    )
                # Visible window: wait for the user to solve the CAPTCHA.
                if not self._wait_for_manual_solve(page):
                    raise SerpBlocked(f"CAPTCHA not solved in time for '{keyword}'")
                page.wait_for_timeout(1000)

            anchors = page.query_selector_all("a:has(h3)")
            results: list[SerpResult] = []
            seen: set[str] = set()
            for a in anchors:
                href = a.get_attribute("href") or ""
                if not href.startswith("http") or href in seen:
                    continue
                dom = registrable_domain(href)
                if "google." in dom or not dom:
                    continue
                seen.add(href)
                h3 = a.query_selector("h3")
                title = h3.inner_text().strip() if h3 else None
                results.append(
                    SerpResult(len(results) + 1, href, dom, title)
                )
                if len(results) >= self.max_results:
                    break
            if not results and page.query_selector("form#captcha-form"):
                raise SerpBlocked(f"Google CAPTCHA for '{keyword}'")
            return results
        finally:
            page.close()

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._context = self._browser = self._pw = None
