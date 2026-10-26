"""FastAPI backend for CompetitorBreak.

Exposes the scrape/report core over a small JSON API and serves the static
frontend. Configuration (competitors + scraper settings) is stored in the
SQLite DB so it can be managed entirely from the frontend.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading

# Make the core package importable (src/ layout).
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from competitorbreak import config, db, reports  # noqa: E402
from competitorbreak.collector import collect_competitor, collect_serp  # noqa: E402
from competitorbreak.scraper import Fetcher  # noqa: E402
from competitorbreak.serp import SerpScraper  # noqa: E402

SETTINGS = config.load_settings()
DB_PATH = SETTINGS.require_db()

# Default setting values seeded on first run (from .env / competitors.yaml).
DEFAULT_SETTINGS: dict[str, object] = {
    "blog_path_patterns": SETTINGS.blog_path_patterns,
    "max_pages": SETTINGS.max_pages,
    "crawl_delay": SETTINGS.crawl_delay,
    "respect_robots": SETTINGS.respect_robots,
    # Conservative SERP defaults — keep Google scraping volume low by default.
    "country": "us",
    "serp_delay": 6.0,         # seconds between queries (longer = safer)
    "serp_max_results": 10,    # results captured per keyword
    "serp_limit": 15,          # max keywords per auto SERP run
    "serp_headless": True,
}

app = FastAPI(title="CompetitorBreak API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One scrape run and one SERP run at a time (SQLite + background threads).
_collect_lock = threading.Lock()
_serp_lock = threading.Lock()


def _ensure_setup() -> None:
    """Create the schema and seed default settings if missing."""
    with db.connect(DB_PATH) as conn:
        db.init_schema(conn)
        existing = db.get_settings(conn)
        for key, value in DEFAULT_SETTINGS.items():
            if key not in existing:
                db.set_setting(conn, key, json.dumps(value))


def _read_settings() -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        raw = db.get_settings(conn)
    out: dict[str, object] = {}
    for key, default in DEFAULT_SETTINGS.items():
        if key in raw:
            try:
                out[key] = json.loads(raw[key])
            except (ValueError, TypeError):
                out[key] = default
        else:
            out[key] = default
    return out


@app.on_event("startup")
def _startup() -> None:
    _ensure_setup()


# --- models --------------------------------------------------------------

class CompetitorIn(BaseModel):
    domain: str
    name: str | None = None
    is_self: bool = False
    blog_url: str | None = None


class SettingsIn(BaseModel):
    blog_path_patterns: list[str] | None = None
    max_pages: int | None = None
    crawl_delay: float | None = None
    respect_robots: bool | None = None
    country: str | None = None
    serp_delay: float | None = None
    serp_max_results: int | None = None
    serp_limit: int | None = None
    serp_headless: bool | None = None


class CollectIn(BaseModel):
    domain: str | None = None  # None = all competitors


class SerpIn(BaseModel):
    keywords: list[str] | None = None  # None = auto (top target terms)
    limit: int | None = None           # cap when auto-selecting terms; None = settings default


# --- competitors ---------------------------------------------------------

@app.get("/api/stats")
def stats():
    """Summary counts for the Overview dashboard."""
    with db.connect(DB_PATH) as conn:
        def one(sql: str):
            return conn.execute(sql).fetchone()
        comp = one("SELECT COUNT(*) AS n, COALESCE(SUM(is_self), 0) AS self FROM competitors")
        return {
            "competitors": comp["n"],
            "self": comp["self"],
            "posts": one("SELECT COUNT(*) AS n FROM pages")["n"],
            "keywords": one("SELECT COUNT(*) AS n FROM keywords")["n"],
            "runs": one("SELECT COUNT(*) AS n FROM runs")["n"],
            "serp_runs": one("SELECT COUNT(*) AS n FROM serp_runs")["n"],
            "serp_results": one("SELECT COUNT(*) AS n FROM serp_results")["n"],
        }


@app.get("/api/competitors")
def list_competitors():
    with db.connect(DB_PATH) as conn:
        return db.list_competitors(conn)


@app.post("/api/competitors")
def add_competitor(body: CompetitorIn):
    with db.connect(DB_PATH) as conn:
        comp_id = db.upsert_competitor(
            conn, body.domain, body.name, is_self=body.is_self, blog_url=body.blog_url
        )
        return db.get_competitor_by_id(conn, comp_id)


@app.put("/api/competitors/{comp_id}")
def update_competitor(comp_id: int, body: CompetitorIn):
    with db.connect(DB_PATH) as conn:
        if db.get_competitor_by_id(conn, comp_id) is None:
            raise HTTPException(404, "Competitor not found")
        return db.update_competitor(
            conn, comp_id, body.name, body.is_self, body.blog_url
        )


@app.delete("/api/competitors/{comp_id}")
def delete_competitor(comp_id: int):
    with db.connect(DB_PATH) as conn:
        db.delete_competitor(conn, comp_id)
    return {"deleted": comp_id}


# --- settings ------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return _read_settings()


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    with db.connect(DB_PATH) as conn:
        for key, value in body.model_dump(exclude_none=True).items():
            db.set_setting(conn, key, json.dumps(value))
    return _read_settings()


# --- collect + runs ------------------------------------------------------

def _run_collect(domain: str | None) -> None:
    cfg = _read_settings()
    try:
        fetcher = Fetcher(
            delay=float(cfg["crawl_delay"]),
            respect_robots=bool(cfg["respect_robots"]),
        )
        try:
            with db.connect(DB_PATH) as conn:
                if domain:
                    comp = db.get_competitor(conn, domain)
                    targets = [comp] if comp else []
                else:
                    targets = db.list_competitors(conn)
                for comp in targets:
                    collect_competitor(
                        conn, fetcher, comp,
                        blog_patterns=list(cfg["blog_path_patterns"]),
                        max_pages=int(cfg["max_pages"]),
                    )
                    conn.commit()
        finally:
            fetcher.close()
    finally:
        _collect_lock.release()


@app.post("/api/collect")
def start_collect(body: CollectIn):
    if not _collect_lock.acquire(blocking=False):
        raise HTTPException(409, "A collect run is already in progress")
    thread = threading.Thread(target=_run_collect, args=(body.domain,), daemon=True)
    thread.start()
    return {"status": "started", "domain": body.domain or "all"}


@app.get("/api/collect/status")
def collect_status():
    return {"running": _collect_lock.locked()}


@app.get("/api/runs")
def list_runs(limit: int = 20):
    with db.connect(DB_PATH) as conn:
        return db.query(
            conn,
            """
            SELECT r.id, c.domain, r.status, r.posts_found, r.posts_scraped,
                   r.started_at, r.finished_at, r.error
              FROM runs r
              JOIN competitors c ON c.id = r.competitor_id
             ORDER BY r.id DESC
             LIMIT ?
            """,
            [limit],
        )


# --- reports -------------------------------------------------------------

@app.get("/api/reports/topics")
def report_topics(domain: str, limit: int = 50, min_posts: int = 1):
    with db.connect(DB_PATH) as conn:
        return reports.topics(conn, domain, limit, min_posts)


@app.get("/api/reports/posts")
def report_posts(domain: str, limit: int = 50):
    with db.connect(DB_PATH) as conn:
        return reports.posts(conn, domain, limit)


@app.get("/api/reports/gaps")
def report_gaps(competitor: str | None = None, limit: int = 50):
    with db.connect(DB_PATH) as conn:
        return reports.gaps(conn, competitor, limit)


@app.get("/api/reports/overlap")
def report_overlap(domain: str, limit: int = 50):
    with db.connect(DB_PATH) as conn:
        return reports.overlap(conn, domain, limit)


@app.get("/api/reports/density")
def report_density(domain: str, keyword: str, limit: int = 50):
    with db.connect(DB_PATH) as conn:
        return reports.keyword_density(conn, domain, keyword, limit)


@app.get("/api/reports/post-keywords")
def report_post_keywords(url: str, limit: int = 50):
    with db.connect(DB_PATH) as conn:
        return reports.post_keywords(conn, url, limit)


@app.get("/api/reports/rankings")
def report_rankings(domain: str, limit: int = 100):
    with db.connect(DB_PATH) as conn:
        return reports.rankings(conn, domain, limit)


@app.get("/api/reports/serp")
def report_serp(keyword: str, limit: int = 20):
    with db.connect(DB_PATH) as conn:
        return reports.serp_keyword(conn, keyword, limit)


@app.get("/api/reports/ranking-history")
def report_ranking_history(domain: str, keyword: str, limit: int = 50):
    with db.connect(DB_PATH) as conn:
        return reports.ranking_history(conn, domain, keyword, limit)


# --- keywords + SERP (rankings) ------------------------------------------

@app.get("/api/keywords")
def list_keywords(limit: int = 200):
    """Distinct target terms extracted from scraped posts (for SERP selection)."""
    with db.connect(DB_PATH) as conn:
        return db.target_terms(conn, limit)


def _run_serp(keywords: list[str] | None, limit: int | None) -> None:
    cfg = _read_settings()
    country = str(cfg.get("country", "us"))
    if limit is None:
        limit = int(cfg.get("serp_limit", 15))
    try:
        serp = SerpScraper(
            country=country,
            delay=float(cfg["serp_delay"]),
            max_results=int(cfg["serp_max_results"]),
            headless=bool(cfg["serp_headless"]),
        )
        try:
            with db.connect(DB_PATH) as conn:
                if keywords:
                    terms = [
                        {"keyword_id": db.upsert_keyword(conn, k.strip().lower()),
                         "term": k.strip().lower()}
                        for k in keywords if k.strip()
                    ]
                else:
                    terms = db.target_terms(conn, limit)
                collect_serp(conn, serp, terms, country=country)
        finally:
            serp.close()
    finally:
        _serp_lock.release()


@app.post("/api/serp/collect")
def start_serp(body: SerpIn):
    if not _serp_lock.acquire(blocking=False):
        raise HTTPException(409, "A SERP run is already in progress")
    thread = threading.Thread(
        target=_run_serp, args=(body.keywords, body.limit), daemon=True
    )
    thread.start()
    return {"status": "started"}


@app.get("/api/serp/status")
def serp_status():
    return {"running": _serp_lock.locked()}


@app.get("/api/serp/runs")
def serp_runs(limit: int = 20):
    with db.connect(DB_PATH) as conn:
        return db.query(
            conn,
            """
            SELECT id, country, status, keywords_checked, started_at, finished_at, error
              FROM serp_runs ORDER BY id DESC LIMIT ?
            """,
            [limit],
        )


# --- serve the built frontend (mounted last so /api/* wins) --------------
# The frontend is a Vite + React + TypeScript app; run `npm run build` in
# frontend/ to produce frontend/dist. During development use `npm run dev`
# (Vite on :5173 proxies /api here).

_DIST = ROOT / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
