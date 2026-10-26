# CompetitorBreak

Find out **which keywords your competitors target in their blogs**, store it in
a local **SQLite** file, and surface **content gaps** against your own domain —
using pure **Python + BeautifulSoup** scraping. No paid SEO API, no DB server.

## What it can and can't do

- ✅ **Which keywords competitors target** — scrapes each blog post's title,
  headings, tags, meta keywords, URL slug and body (hybrid `requests` + a
  Playwright/Chromium fallback for JavaScript-rendered pages), then extracts the
  target keyword(s) via stopword-filtered n-gram scoring.
- ✅ **Topic frequency / content gaps / overlap** vs your own domain.
- ⚠ **SERP ranking positions** — optional, via **Playwright Google scraping**
  (`serp` command / Rankings tab). This finds where competitors rank for the
  target keywords. **Best-effort only:** scraping Google is against its Terms of
  Service and hits CAPTCHAs, rate limits and location variance. Keep volume low;
  for reliable data a paid SERP/SEO API is the proper source.

### Pipeline

```
Playwright + Sitemap  →  Keyword Extraction  →  SERP collection
(blog URLs + content)    (title/headings/NLP)    (ranking keyword, URL, position)
```

## How it works

```
competitors.yaml ──► collect (sitemap/crawl + scrape + extract) ──► SQLite ──► report
```

1. **Discover** blog URLs from `robots.txt` sitemaps, common sitemap paths, or a
   blog-index crawl — kept only if they match `blog_path_patterns`.
2. **Scrape** each post (polite delay, respects `robots.txt`).
3. **Extract** target keywords (primary + tags + content n-grams) → `page_keywords`.
4. **Report** top topics, per-post targets, gaps and overlap.

## Setup

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
```bash
.venv/Scripts/python -m playwright install chromium
```
```bash
cd frontend && npm install && npm run build && cd ..
```
Configure `.env` (copy from `.env.example`):
- `DB_PATH` — SQLite file path (default `competitorbreak.db` in the project root).
  Created automatically; no server needed.
- `MAX_PAGES`, `CRAWL_DELAY`, `RESPECT_ROBOTS` — scraper behaviour.

Edit `competitors.yaml` — set your own domain under `self` and list competitors.

## Usage

```bash
python cb.py init-db                 # create tables
python cb.py sync-config             # load self + competitors from YAML
python cb.py check                   # verify Postgres

python cb.py collect                             # scrape all competitors
python cb.py collect --competitor rival.com --max-pages 100

python cb.py report topics rival.com             # their most-targeted keywords
python cb.py report posts rival.com              # per-post primary keyword
python cb.py report gaps                         # they target, we don't
python cb.py report overlap rival.com            # shared targets

# rankings (Playwright Google scrape — best-effort)
python cb.py serp --limit 20                     # check top target terms
python cb.py serp -k "hr software" -k "payroll"  # specific keywords
python cb.py report rankings rival.com           # where they rank
python cb.py report serp "hr software"           # full SERP for a keyword
```

(After `pip install -e .` the same commands are available as `cb ...`.)

## Web app (backend + frontend)

Prefer a UI? The repo ships a separate **backend** (FastAPI JSON API) and
**frontend** (Vite + React + TypeScript, styled with plain CSS). Manage
competitors, edit scraper settings, trigger collection/SERP and view reports
from the browser.

Build the frontend once (needs Node 18+), then run the backend:

```bash
cd frontend && npm install && npm run build && cd ..
```
```bash
.venv/Scripts/python backend/run.py
```

Then open **http://localhost:8000** — the backend serves the built UI
(`frontend/dist`) and the API at `/api/*`. Settings and competitors live in the
same SQLite DB, so the UI and the `cb` CLI share one dataset.

**Frontend dev loop:** `cd frontend && npm run dev` runs Vite on
http://localhost:5173 and proxies `/api` to the backend on :8000 (run both). To
point the UI at a different backend origin, set
`localStorage.setItem('cb_api', 'http://host:8000')` in the browser console.

### Key API endpoints

| Method & path | Purpose |
|---|---|
| `GET/POST /api/competitors`, `PUT/DELETE /api/competitors/{id}` | manage domains |
| `GET/PUT /api/settings` | blog patterns, max pages, delay, robots, SERP options |
| `POST /api/collect` `{domain?}`, `GET /api/collect/status`, `GET /api/runs` | scrape + progress |
| `POST /api/serp/collect`, `GET /api/serp/status`, `GET /api/serp/runs` | rankings scrape |
| `GET /api/keywords` | distinct target terms |
| `GET /api/reports/{topics,posts,gaps,overlap,rankings,serp}` | reports |

## Project layout

| File | Purpose |
|------|---------|
| `src/competitorbreak/config.py` | load `.env` + `competitors.yaml` |
| `src/competitorbreak/db.py` | SQLite access layer |
| `src/competitorbreak/schema.sql` | SQLite schema |
| `src/competitorbreak/scraper.py` | sitemap/blog discovery + post parsing (Playwright fallback) |
| `src/competitorbreak/keywords.py` | n-gram keyword extraction (no NLP deps) |
| `src/competitorbreak/serp.py` | Playwright Google SERP scraper (rankings) |
| `src/competitorbreak/collector.py` | scrape + persist orchestration |
| `src/competitorbreak/reports.py` | topics / gaps / overlap queries |
| `src/competitorbreak/cli.py` | Click CLI |
| `backend/app.py` | FastAPI JSON API + serves the built frontend |
| `backend/run.py` | `python backend/run.py` launcher (uvicorn) |
| `frontend/` | Vite + React + TypeScript UI (`src/*.tsx`, plain CSS) |

## Notes

- Scraping respects `robots.txt` by default (`RESPECT_ROBOTS=false` to disable)
  and waits `CRAWL_DELAY` seconds between requests.
- The database is a single SQLite file (`DB_PATH`), git-ignored. Back it up by
  copying the file; inspect it with any SQLite browser.
