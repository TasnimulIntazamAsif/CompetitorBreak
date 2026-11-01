# CompetitorANK

Crawl a competitor's website and build a **keyword index**: every keyword its
pages target, the URLs it appears on, and how often. Results are saved to a
**Store** you can browse later and export as **CSV / XLSX / DOCX / PDF**.

A second tool, **Ask**, runs a free-form Claude web-search query and saves the
answer as a Markdown report in the same Store.

Pure **Python + BeautifulSoup** scraping (with a Playwright/Chromium fallback for
JavaScript-rendered pages) — no paid SEO API needed for the keyword crawl.

## How it works

```
domain ──► discover pages ──► scrape each page ──► extract keywords ──► index ──► Store / export
           (robots.txt,        (requests, then      (title, headings,    keyword → URLs
            sitemaps, blog      Playwright if JS)    meta, slug, body;    → frequency
            index crawl)                             n-gram scoring)
```

1. **Discover** URLs from `robots.txt` sitemaps, common sitemap paths, or a
   blog-index crawl. By default only content pages matching
   `blog_path_patterns` are kept; **all pages** mode takes the whole sitemap.
2. **Scrape** each page politely (respects `robots.txt` by default).
3. **Extract** the keywords each page is densest in, via stopword-filtered
   n-gram scoring (no NLP dependencies).
4. **Index** them as `keyword → [{url, count}]`, sorted by total frequency.

## Setup

Requires Python 3.11+ and Node 18+.

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
```bash
.venv/Scripts/python -m playwright install chromium
```
```bash
cd frontend && npm install && npm run build && cd ..
```

Copy `.env.example` to `.env` and configure the database:

- **PostgreSQL** — set `DATABASE_URL`, or `PGHOST` / `PGPORT` / `PGUSER` /
  `PGPASSWORD` / `PGDATABASE` (the database must already exist). If any
  Postgres host is set, Postgres is used.
- **SQLite** (fallback) — `DB_PATH`, default `competitorbreak.db` in the
  project root. Created automatically.

Scraper defaults: `MAX_PAGES`, `CRAWL_DELAY`, `RESPECT_ROBOTS`.

The **Ask** feature uses the `claude` CLI (your existing Claude Code login) for
web search, so it needs `claude` on `PATH`.

`competitors.yaml` holds your own domain (`self`), seed competitors, and the
default `blog_path_patterns`.

## Web app

```bash
.venv/Scripts/python backend/run.py
```

Open **http://localhost:8000**. The FastAPI backend serves the built UI
(`frontend/dist`) and the API at `/api/*`. Set `PORT` to change the port and
`RELOAD=1` for auto-reload while developing.

Tabs:

| Tab | Purpose |
|---|---|
| **Overview** | What the tool does, at a glance |
| **Keywords** | Enter a domain, crawl it, view the keyword index, export it |
| **Store** | Saved crawls (structured keyword → URL → frequency view) and Ask reports; download or delete |
| **Settings** | Blog path patterns, max pages, crawl delay, robots.txt, LLM options |

**Frontend dev loop:** `cd frontend && npm run dev` runs Vite on
http://localhost:5173 and proxies `/api` to the backend on :8000 (run both). To
point the UI at a different backend, run
`localStorage.setItem('cb_api', 'http://host:8000')` in the browser console.

## CLI

```bash
python cb.py init-db                       # create tables
python cb.py check                         # verify the database connection
python cb.py sync-config                   # load self + competitors from competitors.yaml
python cb.py list-competitors
python cb.py add-competitor rival.com

python cb.py crawl-keywords rival.com                       # content pages, default 15
python cb.py crawl-keywords rival.com --all --max-pages 500 # whole sitemap
python cb.py crawl-keywords rival.com --out rival.md

python cb.py ask "best HR software in Bangladesh"           # Claude web search → exports/*.md

python cb.py migrate-to-pg                 # copy an existing SQLite DB into Postgres
```

(After `pip install -e .` the same commands are available as `cb ...`.)

Reports are written to `exports/` (git-ignored) unless `--out` is given.

## API

| Method & path | Purpose |
|---|---|
| `GET/PUT /api/settings` | scraper and LLM settings (stored in the DB) |
| `POST /api/crawl-keywords/start` `{domain, max_pages, max_keywords, all_pages}` | start a crawl → `run_id` |
| `GET /api/crawl-keywords/status?run_id=` | progress |
| `GET /api/crawl-keywords/results?run_id=` | the keyword index |
| `GET /api/crawl-keywords/export?run_id=&fmt=csv\|xlsx\|docx\|pdf` | download an export |
| `POST /api/ask/start` `{query}`, `GET /api/ask/status?run_id=` | Claude web-search report |
| `GET /api/exports`, `GET/DELETE /api/exports/{name}`, `GET /api/exports/{name}/download` | the Store |

Limits: crawls are capped at 40 pages (2000 with `all_pages`) and 20 keywords
per page. One crawl and one Ask run at a time. Job progress is held in memory,
so a restart drops in-flight jobs; finished results are kept as files in
`exports/`.

## Docker

```bash
docker compose up -d --build
```

Starts PostgreSQL 16 (data in the `pgdata` volume, exposed on host port 5433)
and the web app on **http://localhost:8000**. The image builds the frontend,
installs Chromium for Playwright, and runs `init-db` + `sync-config` on boot.

## Project layout

| Path | Purpose |
|------|---------|
| `src/competitorbreak/config.py` | load `.env` + `competitors.yaml`, pick Postgres or SQLite |
| `src/competitorbreak/db.py` | database access layer (Postgres + SQLite) |
| `src/competitorbreak/schema_pg.sql`, `schema.sql` | Postgres / SQLite schemas |
| `src/competitorbreak/scraper.py` | page discovery + parsing (Playwright fallback) |
| `src/competitorbreak/keywords.py` | n-gram keyword extraction |
| `src/competitorbreak/competitor_keywords.py` | crawl → keyword index, Markdown rendering |
| `src/competitorbreak/exporters.py` | CSV / XLSX / DOCX / PDF export |
| `src/competitorbreak/llm_search.py` | Claude web search (`claude` CLI or Anthropic API) |
| `src/competitorbreak/serp.py` | shared SERP types and domain helpers |
| `src/competitorbreak/cli.py` | Click CLI |
| `backend/app.py` | FastAPI API + serves the built frontend |
| `backend/run.py` | uvicorn launcher |
| `frontend/` | Vite + React + TypeScript UI (plain CSS) |

## Notes

- Scraping respects `robots.txt` by default (`RESPECT_ROBOTS=false` to disable)
  and waits between requests.
- The API has no authentication and allows all CORS origins; put it behind an
  authenticating proxy if it's exposed publicly.
