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

from competitorbreak import config, db  # noqa: E402
from competitorbreak.scraper import Fetcher  # noqa: E402
from competitorbreak.llm_search import ClaudeCLISearch  # noqa: E402
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

EXPORTS = ROOT / "exports"

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
            # Store the result as structured JSON so the Store tab can render it
            # as a table (keyword -> URLs -> frequency), not just raw text.
            md_file = f"crawl-keywords-{slug}-{run_id[:8]}.json"
            payload = {
                "kind": "crawl-keywords",
                "domain": domain,
                "created": datetime.datetime.now().isoformat(timespec="seconds"),
                "rows": rows,
            }
            (EXPORTS / md_file).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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


def _valid_export_name(name: str) -> bool:
    return not ("/" in name or "\\" in name or ".." in name) and (
        name.endswith(".md") or name.endswith(".json")
    )


@app.get("/api/exports")
def list_exports():
    EXPORTS.mkdir(parents=True, exist_ok=True)
    files = list(EXPORTS.glob("*.md")) + list(EXPORTS.glob("*.json"))
    rows = []
    for p in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        rows.append({
            "name": p.name,
            "size": st.st_size,
            "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return rows


@app.get("/api/exports/{name}")
def get_export(name: str):
    if not _valid_export_name(name):
        raise HTTPException(400, "Invalid file name.")
    p = EXPORTS / name
    if not p.exists():
        raise HTTPException(404, "Not found.")
    return {"name": name, "content": p.read_text(encoding="utf-8")}


@app.delete("/api/exports/{name}")
def delete_export(name: str):
    if not _valid_export_name(name):
        raise HTTPException(400, "Invalid file name.")
    p = EXPORTS / name
    if p.exists():
        p.unlink()
    return {"deleted": name}


@app.get("/api/exports/{name}/download")
def export_file_as(name: str, fmt: str):
    """Convert a saved crawl result (.json) to md/csv/xlsx/docx/pdf for download."""
    if not _valid_export_name(name) or not name.endswith(".json"):
        raise HTTPException(400, "Only saved crawl (.json) results can be converted.")
    p = EXPORTS / name
    if not p.exists():
        raise HTTPException(404, "Not found.")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data.get("rows") or []
        domain = data.get("domain") or "domain"
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "File is not a valid crawl result.")

    fmt = fmt.lower()
    base = name[:-5]  # strip ".json"
    if fmt == "md":
        content = ckw.index_to_markdown(domain, rows).encode("utf-8")
        media = "text/markdown"
    elif fmt in exporters.GENERATORS:
        content = exporters.GENERATORS[fmt](domain, rows)
        media = exporters.MEDIA_TYPES[fmt]
    else:
        raise HTTPException(400, "Unsupported format. Use md, csv, xlsx, docx or pdf.")
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{base}.{fmt}"'},
    )

# --- serve the built frontend (mounted last so /api/* wins) --------------
# The frontend is a Vite + React + TypeScript app; run `npm run build` in
# frontend/ to produce frontend/dist. During development use `npm run dev`
# (Vite on :5173 proxies /api here).

_DIST = ROOT / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
