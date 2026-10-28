"""FastAPI backend for CompetitorBreak.

Exposes the scrape/report core over a small JSON API and serves the static
frontend. Configuration (competitors + scraper settings) is stored in the
SQLite DB so it can be managed entirely from the frontend.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import sys
import threading
import uuid

# Make the core package importable (src/ layout).
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from competitorbreak import config, db, reports  # noqa: E402
from competitorbreak.collector import collect_competitor, collect_serp  # noqa: E402
from competitorbreak.scraper import Fetcher  # noqa: E402
from competitorbreak.serp import SerpScraper  # noqa: E402
from competitorbreak.dataforseo import DataForSEOSerp  # noqa: E402
from competitorbreak.llm_search import LLMSearch, ClaudeCLISearch  # noqa: E402
from competitorbreak.analyze import analyze_query, to_markdown  # noqa: E402
from competitorbreak import competitor_keywords as ckw  # noqa: E402
from competitorbreak import exporters  # noqa: E402

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
    "serp_source": "playwright",       # playwright | dataforseo
    "dataforseo_location": "United States",
    "llm_source": "claude_cli",        # claude_cli | anthropic
    "llm_model": "claude-haiku-5-5",   # model for query analysis (fast + light)
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
    serp_source: str | None = None
    dataforseo_location: str | None = None
    llm_source: str | None = None
    llm_model: str | None = None


class CollectIn(BaseModel):
    domain: str | None = None  # None = all competitors


class SerpIn(BaseModel):
    keywords: list[str] | None = None  # None = auto (top target terms)
    limit: int | None = None           # cap when auto-selecting terms; None = settings default


class AnalyzeIn(BaseModel):
    query: str
    max_results: int = 5


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
    source = str(cfg.get("serp_source", "playwright"))
    if limit is None:
        limit = int(cfg.get("serp_limit", 15))
    try:
        if source == "dataforseo":
            auth = SETTINGS.dataforseo_auth()
            if not auth:
                with db.connect(DB_PATH) as conn:
                    rid = db.start_serp_run(conn, country)
                    db.finish_serp_run(
                        conn, rid, "error", 0,
                        "DataForSEO credentials missing in .env "
                        "(DATAFORSEO_LOGIN/PASSWORD or DATAFORSEO_Base64_format)",
                    )
                return
            serp = DataForSEOSerp(
                auth=auth,
                country=country,
                location=str(cfg.get("dataforseo_location", "United States")),
                max_results=int(cfg["serp_max_results"]),
                sandbox=SETTINGS.dataforseo_sandbox,
            )
        else:
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


# --- query analysis (background: LLM web search can take a minute) ----------

_analyze_jobs: dict[str, dict] = {}
_analyze_lock = threading.Lock()
EXPORTS = ROOT / "exports"


def _run_analyze(run_id: str, query: str, n: int) -> None:
    job = _analyze_jobs[run_id]
    cfg = _read_settings()
    source = str(cfg.get("llm_source", "claude_cli"))
    model = str(cfg.get("llm_model", "claude-haiku-5-5"))
    # Our own site, for the rank comparison — the competitor marked is_self.
    self_domain = None
    try:
        with db.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT domain FROM competitors WHERE is_self = 1 LIMIT 1"
            ).fetchone()
            if row:
                self_domain = row["domain"]
    except Exception:  # noqa: BLE001 - comparison is best-effort
        self_domain = None
    try:
        if source == "dataforseo":
            auth = SETTINGS.dataforseo_auth()
            if not auth:
                raise RuntimeError("DataForSEO credentials are not set in .env.")
            llm = DataForSEOSerp(
                auth=auth,
                country=str(cfg.get("country", "us")),
                location=str(cfg.get("dataforseo_location", "United States")),
                max_results=n,
                sandbox=SETTINGS.dataforseo_sandbox,
            )
        elif source == "claude_cli":
            llm = ClaudeCLISearch(model=model, max_results=n, self_domain=self_domain)
        else:
            key = SETTINGS.anthropic_api_key
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set in .env.")
            llm = LLMSearch(api_key=key, model=model, max_results=n, self_domain=self_domain)
        fetcher = Fetcher(
            delay=0.5, respect_robots=bool(cfg["respect_robots"]), use_playwright=False
        )
        try:
            rows = analyze_query(llm, fetcher, query, max_results=n, self_domain=self_domain)
        finally:
            llm.close()
            fetcher.close()

        md = to_markdown(query, rows, source=source)
        md_file = None
        try:
            EXPORTS.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40] or "query"
            md_file = f"analyze-{slug}-{run_id[:8]}.md"
            (EXPORTS / md_file).write_text(md, encoding="utf-8")
        except Exception:  # noqa: BLE001 - file write is best-effort
            md_file = None

        job.update(status="success", results=rows, markdown=md, md_file=md_file)
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
    finally:
        _analyze_lock.release()


@app.post("/api/analyze/start")
def analyze_start(body: AnalyzeIn):
    cfg = _read_settings()
    if str(cfg.get("llm_source", "claude_cli")) == "anthropic" and not SETTINGS.anthropic_api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY is not set in .env.")
    if not _analyze_lock.acquire(blocking=False):
        raise HTTPException(409, "An analysis is already running.")
    n = max(1, min(body.max_results, 10))
    run_id = uuid.uuid4().hex
    _analyze_jobs[run_id] = {
        "status": "running", "query": body.query, "results": None,
        "error": None, "markdown": None, "md_file": None,
    }
    threading.Thread(target=_run_analyze, args=(run_id, body.query, n), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/analyze/status")
def analyze_status(run_id: str):
    job = _analyze_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown analysis run.")
    return {"status": job["status"], "error": job["error"],
            "count": len(job["results"] or [])}


@app.get("/api/analyze/results")
def analyze_results(run_id: str):
    job = _analyze_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown analysis run.")
    return {
        "status": job["status"], "error": job["error"],
        "results": job["results"] or [],
        "markdown": job["markdown"], "md_file": job["md_file"],
    }


# --- competitor keywords (domain -> ranking keywords, via claude web search) --

_ckw_jobs: dict[str, dict] = {}
_ckw_lock = threading.Lock()


def _run_competitor_keywords(run_id: str, domain: str, n: int) -> None:
    job = _ckw_jobs[run_id]
    cfg = _read_settings()
    model = str(cfg.get("llm_model", "claude-haiku-5-5"))
    try:
        llm = ClaudeCLISearch(model=model)
        # Playwright renders JS / gets past bot-blocks so density can be computed.
        fetcher = Fetcher(
            delay=0.5, respect_robots=bool(cfg["respect_robots"]), use_playwright=True
        )
        try:
            rows = ckw.reverse_keywords(llm, domain, max_keywords=n, fetcher=fetcher)
        finally:
            llm.close()
            fetcher.close()

        md = ckw.to_markdown(domain, rows, source="claude-cli")
        md_file = None
        try:
            EXPORTS.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:40] or "domain"
            md_file = f"competitor-keywords-{slug}-{run_id[:8]}.md"
            (EXPORTS / md_file).write_text(md, encoding="utf-8")
        except Exception:  # noqa: BLE001 - file write is best-effort
            md_file = None

        job.update(status="success", results=rows, markdown=md, md_file=md_file)
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
    finally:
        _ckw_lock.release()


class CompetitorKeywordsIn(BaseModel):
    domain: str
    max_keywords: int = 15


@app.post("/api/competitor-keywords/start")
def competitor_keywords_start(body: CompetitorKeywordsIn):
    if not body.domain.strip():
        raise HTTPException(400, "Enter a domain.")
    if not _ckw_lock.acquire(blocking=False):
        raise HTTPException(409, "A keyword check is already running.")
    n = max(1, min(body.max_keywords, 30))
    run_id = uuid.uuid4().hex
    _ckw_jobs[run_id] = {
        "status": "running", "domain": body.domain, "results": None,
        "error": None, "markdown": None, "md_file": None,
    }
    threading.Thread(
        target=_run_competitor_keywords, args=(run_id, body.domain.strip(), n), daemon=True
    ).start()
    return {"run_id": run_id}


@app.get("/api/competitor-keywords/status")
def competitor_keywords_status(run_id: str):
    job = _ckw_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown run.")
    return {"status": job["status"], "error": job["error"],
            "count": len(job["results"] or [])}


@app.get("/api/competitor-keywords/results")
def competitor_keywords_results(run_id: str):
    job = _ckw_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown run.")
    return {
        "status": job["status"], "error": job["error"],
        "results": job["results"] or [],
        "markdown": job["markdown"], "md_file": job["md_file"],
    }


# --- site crawl (domain -> per-page keyword density, no LLM) --------------

_crawl_jobs: dict[str, dict] = {}
_crawl_lock = threading.Lock()


def _run_crawl_keywords(
    run_id: str, domain: str, max_pages: int, n: int, all_pages: bool
) -> None:
    job = _crawl_jobs[run_id]
    cfg = _read_settings()
    try:
        fetcher = Fetcher(
            delay=0.5, respect_robots=bool(cfg["respect_robots"]), use_playwright=True
        )
        try:
            rows = ckw.crawl_keyword_index(
                fetcher, domain,
                blog_patterns=list(cfg.get("blog_path_patterns") or SETTINGS.blog_path_patterns),
                max_pages=max_pages, all_pages=all_pages,
            )
        finally:
            fetcher.close()

        md = ckw.index_to_markdown(domain, rows)
        md_file = None
        try:
            EXPORTS.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:40] or "domain"
            md_file = f"crawl-keywords-{slug}-{run_id[:8]}.md"
            (EXPORTS / md_file).write_text(md, encoding="utf-8")
        except Exception:  # noqa: BLE001 - file write is best-effort
            md_file = None

        job.update(status="success", results=rows, markdown=md, md_file=md_file)
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
    finally:
        _crawl_lock.release()


class CrawlKeywordsIn(BaseModel):
    domain: str
    max_pages: int = 15
    max_keywords: int = 10
    all_pages: bool = False


@app.post("/api/crawl-keywords/start")
def crawl_keywords_start(body: CrawlKeywordsIn):
    if not body.domain.strip():
        raise HTTPException(400, "Enter a domain.")
    if not _crawl_lock.acquire(blocking=False):
        raise HTTPException(409, "A crawl is already running.")
    # Whole-site crawls can be large, so allow a much higher cap with --all.
    cap = 2000 if body.all_pages else 40
    pages_n = max(1, min(body.max_pages, cap))
    n = max(1, min(body.max_keywords, 20))
    run_id = uuid.uuid4().hex
    _crawl_jobs[run_id] = {
        "status": "running", "domain": body.domain, "results": None,
        "error": None, "markdown": None, "md_file": None,
    }
    threading.Thread(
        target=_run_crawl_keywords,
        args=(run_id, body.domain.strip(), pages_n, n, body.all_pages), daemon=True
    ).start()
    return {"run_id": run_id}


@app.get("/api/crawl-keywords/status")
def crawl_keywords_status(run_id: str):
    job = _crawl_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown run.")
    return {"status": job["status"], "error": job["error"],
            "count": len(job["results"] or [])}


@app.get("/api/crawl-keywords/results")
def crawl_keywords_results(run_id: str):
    job = _crawl_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown run.")
    return {
        "status": job["status"], "error": job["error"],
        "results": job["results"] or [],
        "markdown": job["markdown"], "md_file": job["md_file"],
    }


@app.get("/api/crawl-keywords/export")
def crawl_keywords_export(run_id: str, fmt: str):
    job = _crawl_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown run.")
    if job["status"] != "success":
        raise HTTPException(409, "This run has no completed result yet.")
    fmt = fmt.lower()
    gen = exporters.GENERATORS.get(fmt)
    if gen is None:
        raise HTTPException(400, "Unsupported format. Use csv, xlsx, docx or pdf.")
    domain = job["domain"]
    data = gen(domain, job["results"] or [])
    slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:40] or "domain"
    fname = f"keyword-index-{slug}.{fmt}"
    return Response(
        content=data, media_type=exporters.MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# --- free-form ask (Claude web search) + the exports store --------------

_ask_jobs: dict[str, dict] = {}
_ask_lock = threading.Lock()


def _save_export(title: str, body: str) -> str:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "query"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{slug}-{ts}.md"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    (EXPORTS / name).write_text(f"# {title}\n\n_{now}_\n\n{body}\n", encoding="utf-8")
    return name


def _run_ask(run_id: str, query: str) -> None:
    job = _ask_jobs[run_id]
    try:
        llm = ClaudeCLISearch()
        text = llm.raw(query)
        name = _save_export(query, text)
        job.update(status="success", md_file=name, markdown=text)
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
    finally:
        _ask_lock.release()


class AskIn(BaseModel):
    query: str


@app.post("/api/ask/start")
def ask_start(body: AskIn):
    if not _ask_lock.acquire(blocking=False):
        raise HTTPException(409, "A query is already running.")
    run_id = uuid.uuid4().hex
    _ask_jobs[run_id] = {"status": "running", "md_file": None,
                         "markdown": None, "error": None}
    threading.Thread(target=_run_ask, args=(run_id, body.query), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/ask/status")
def ask_status(run_id: str):
    job = _ask_jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Unknown run.")
    return {"status": job["status"], "error": job["error"], "md_file": job["md_file"]}


@app.get("/api/exports")
def list_exports():
    EXPORTS.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(EXPORTS.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        rows.append({
            "name": p.name,
            "size": st.st_size,
            "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return rows


@app.get("/api/exports/{name}")
def get_export(name: str):
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".md"):
        raise HTTPException(400, "Invalid file name.")
    p = EXPORTS / name
    if not p.exists():
        raise HTTPException(404, "Not found.")
    return {"name": name, "content": p.read_text(encoding="utf-8")}


@app.delete("/api/exports/{name}")
def delete_export(name: str):
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".md"):
        raise HTTPException(400, "Invalid file name.")
    p = EXPORTS / name
    if p.exists():
        p.unlink()
    return {"deleted": name}


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
