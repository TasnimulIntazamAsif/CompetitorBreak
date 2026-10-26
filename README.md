# CompetitorBreak

Find out **which keywords your competitors target in their blogs**, store it in
a local **SQLite** file, and surface **content gaps** against your own domain —
using pure **Python + BeautifulSoup** scraping. No paid SEO API, no DB server.

## What it can and can't do

- ✅ **Which keywords competitors target** — scrapes each blog post's title,
  headings, tags, meta keywords, URL slug and body, then extracts the target
  keyword(s) via stopword-filtered n-gram scoring.
- ✅ **Topic frequency / content gaps / overlap** vs your own domain.
- ❌ **Actual SERP ranking positions** ("which keyword they rank #N for") —
  this is **not obtainable by scraping blogs**. Real ranking data comes from
  Google SERPs, which can't be scraped reliably (ToS + CAPTCHA/blocking) and
  needs a rank-tracking API. The target-keyword analysis above is the practical
  substitute; a SERP/keyword API can be plugged in later if you get one.

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
```

(After `pip install -e .` the same commands are available as `cb ...`.)

## Project layout

| File | Purpose |
|------|---------|
| `src/competitorbreak/config.py` | load `.env` + `competitors.yaml` |
| `src/competitorbreak/db.py` | SQLite access layer |
| `src/competitorbreak/schema.sql` | SQLite schema |
| `src/competitorbreak/scraper.py` | sitemap/blog discovery + post parsing |
| `src/competitorbreak/keywords.py` | n-gram keyword extraction (no NLP deps) |
| `src/competitorbreak/collector.py` | scrape + persist orchestration |
| `src/competitorbreak/reports.py` | topics / gaps / overlap queries |
| `src/competitorbreak/cli.py` | Click CLI |

## Notes

- Scraping respects `robots.txt` by default (`RESPECT_ROBOTS=false` to disable)
  and waits `CRAWL_DELAY` seconds between requests.
- The database is a single SQLite file (`DB_PATH`), git-ignored. Back it up by
  copying the file; inspect it with any SQLite browser.
