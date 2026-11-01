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
import time
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
_crawl_guard = threading.Lock()  # guards _crawl_current only
_crawl_current: str | None = None

# A crawl that makes no progress for this long is treated as hung (e.g. a wedged
# headless browser) and failed, so it can't block every later crawl forever.
CRAWL_STALL_SECS = 180


def _check_stall(job: dict) -> None:
    """Fail a running job whose heartbeat is older than CRAWL_STALL_SECS."""
    if job["status"] == "running" and time.monotonic() - job["heartbeat"] > CRAWL_STALL_SECS:
        job.update(status="error", error=(
            f"Crawl stalled (no progress for {CRAWL_STALL_SECS // 60} minutes) and was "
            "stopped. Try again, or crawl fewer pages."
        ))


# In-progress crawl state, saved as it goes so a server stop/restart doesn't
# lose finished pages: starting the same crawl again resumes from here.
# (A subfolder, so the Store's *.json listing doesn't pick these up.)
CHECKPOINTS = EXPORTS / "checkpoints"
CHECKPOINT_EVERY_PAGES = 10
CHECKPOINT_EVERY_SECS = 20.0


def _checkpoint_path(domain: str, all_pages: bool, max_pages: int) -> pathlib.Path:
    slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:60] or "domain"
    return CHECKPOINTS / f"{slug}-{'all' if all_pages else 'blog'}-{max_pages}.json"


def _load_checkpoint(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("urls") else None


def _write_checkpoint(path: pathlib.Path, state: dict) -> None:
    try:
        CHECKPOINTS.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic, so a kill mid-write can't corrupt it
    except OSError:
        pass  # best-effort


def _run_crawl_keywords(
    run_id: str, domain: str, max_pages: int, n: int, all_pages: bool
) -> None:
    global _crawl_current
    job = _crawl_jobs[run_id]
    cfg = _read_settings()

    def beat(done: int, total: int) -> None:
        job.update(done=done, total=total, heartbeat=time.monotonic())

    ckpt_path = _checkpoint_path(domain, all_pages, max_pages)
    resume = _load_checkpoint(ckpt_path)
    state = resume or {"domain": domain, "all_pages": all_pages,
                       "max_pages": max_pages, "urls": [], "pages": {}}
    job["resumed"] = len(state["pages"])
    unsaved = 0
    last_save = time.monotonic()

    def on_urls(urls: list[str]) -> None:
        state["urls"] = urls
        _write_checkpoint(ckpt_path, state)

    def on_page(url: str, terms: dict[str, int]) -> None:
        nonlocal unsaved, last_save
        if job["status"] != "running":
            return  # failed as stalled; a retry may own the checkpoint now
        state["pages"][url] = terms
        unsaved += 1
        if unsaved >= CHECKPOINT_EVERY_PAGES or time.monotonic() - last_save > CHECKPOINT_EVERY_SECS:
            _write_checkpoint(ckpt_path, state)
            unsaved, last_save = 0, time.monotonic()

    try:
        fetcher = Fetcher(
            delay=0.5, respect_robots=bool(cfg["respect_robots"]), use_playwright=True
        )
        try:
            rows = ckw.crawl_keyword_index(
                fetcher, domain,
                blog_patterns=list(cfg.get("blog_path_patterns") or SETTINGS.blog_path_patterns),
                max_pages=max_pages, all_pages=all_pages,
                log=lambda _m: job.update(heartbeat=time.monotonic()), progress=beat,
                resume=resume, on_urls=on_urls, on_page=on_page,
            )
        except BaseException:
            _write_checkpoint(ckpt_path, state)  # keep what's done for the retry
            raise
        finally:
            fetcher.close()
        ckpt_path.unlink(missing_ok=True)  # finished: nothing left to resume

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

        # A job already failed as stalled keeps that result.
        if job["status"] == "running":
            job.update(status="success", results=rows, markdown=md, md_file=md_file)
    except Exception as exc:  # noqa: BLE001
        if job["status"] == "running":
            job.update(status="error", error=str(exc))
    finally:
        with _crawl_guard:
            if _crawl_current == run_id:
                _crawl_current = None


class CrawlKeywordsIn(BaseModel):
    domain: str
    max_pages: int = 15
    max_keywords: int = 10
    all_pages: bool = False


@app.post("/api/crawl-keywords/start")
def crawl_keywords_start(body: CrawlKeywordsIn):
    global _crawl_current
    if not body.domain.strip():
        raise HTTPException(400, "Enter a domain.")
    # Whole-site crawls can be large, so allow a much higher cap with --all.
    cap = 2000 if body.all_pages else 40
    pages_n = max(1, min(body.max_pages, cap))
    n = max(1, min(body.max_keywords, 20))
    run_id = uuid.uuid4().hex
    with _crawl_guard:
        current = _crawl_jobs.get(_crawl_current) if _crawl_current else None
        if current:
            _check_stall(current)
            if current["status"] == "running":
                raise HTTPException(409, "A crawl is already running.")
        _crawl_current = run_id
        _crawl_jobs[run_id] = {
            "status": "running", "domain": body.domain, "results": None,
            "error": None, "markdown": None, "md_file": None,
            "done": 0, "total": None, "heartbeat": time.monotonic(), "resumed": 0,
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
    _check_stall(job)
    return {"status": job["status"], "error": job["error"],
            "count": len(job["results"] or []),
            "done": job["done"], "total": job["total"], "resumed": job["resumed"]}


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
