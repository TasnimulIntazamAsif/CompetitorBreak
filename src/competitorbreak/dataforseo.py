"""DataForSEO SERP provider — a drop-in replacement for the Playwright scraper.

Uses the Google Organic SERP Live (Advanced) endpoint to fetch, for a keyword,
the organic results (position, domain, URL, title). Reliable and CAPTCHA-free;
each live request bills a small amount against your DataForSEO balance. A free
Sandbox base URL returns dummy data for testing without cost.

Docs: https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/
"""

from __future__ import annotations

import base64 as _base64

import requests

from .serp import SerpError, SerpResult, registrable_domain

LIVE_BASE = "https://api.dataforseo.com/v3"
SANDBOX_BASE = "https://sandbox.dataforseo.com/v3"


class DataForSEOError(SerpError):
    pass


def make_auth(login: str | None, password: str | None, b64: str | None) -> str | None:
    """Return the Basic-auth base64 string from an explicit value or login/password."""
    if b64:
        return b64.strip()
    if login and password:
        return _base64.b64encode(f"{login}:{password}".encode()).decode()
    return None


class DataForSEOSerp:
    def __init__(
        self,
        auth: str,
        *,
        country: str = "us",
        lang: str = "en",
        location: str = "United States",
        max_results: int = 20,
        sandbox: bool = False,
        timeout: int = 60,
    ):
        if not auth:
            raise DataForSEOError("DataForSEO credentials are not set.")
        self.auth = auth
        self.country = country.lower()
        self.lang = lang
        self.location = location
        self.max_results = max_results
        self.timeout = timeout
        self.base = SANDBOX_BASE if sandbox else LIVE_BASE
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Basic {self.auth}",
                "Content-Type": "application/json",
            }
        )

    def search(self, keyword: str) -> list[SerpResult]:
        """Return organic results for a keyword via DataForSEO."""
        payload = [
            {
                "keyword": keyword,
                "location_name": self.location,
                "language_code": self.lang,
                "depth": self.max_results,
            }
        ]
        try:
            resp = self.session.post(
                f"{self.base}/serp/google/organic/live/advanced",
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DataForSEOError(f"Request failed: {exc}") from exc

        if resp.status_code == 401:
            raise DataForSEOError("DataForSEO auth failed (401). Check credentials in .env.")
        if resp.status_code >= 400:
            raise DataForSEOError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        tasks = data.get("tasks") or []
        if not tasks:
            raise DataForSEOError(f"No tasks in response: {str(data)[:200]}")
        task = tasks[0]
        if task.get("status_code") != 20000:
            raise DataForSEOError(
                f"Task error {task.get('status_code')}: {task.get('status_message')}"
            )
        result = task.get("result") or []
        items = (result[0].get("items") if result else None) or []

        out: list[SerpResult] = []
        for it in items:
            if it.get("type") != "organic":
                continue
            url = it.get("url") or ""
            if not url:
                continue
            pos = it.get("rank_absolute") or it.get("rank_group")
            domain = registrable_domain(url) or (it.get("domain") or "")
            out.append(SerpResult(pos, url, domain, it.get("title")))
            if len(out) >= self.max_results:
                break
        return out

    def close(self) -> None:  # parity with SerpScraper
        self.session.close()
