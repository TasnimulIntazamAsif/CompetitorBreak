"""Command-line interface for CompetitorBreak (scrape pipeline)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from . import db, reports
from .collector import collect_competitor, collect_serp
from .config import Settings, load_settings
from .scraper import Fetcher
from .serp import SerpScraper
from .dataforseo import DataForSEOSerp
from .llm_search import LLMSearch, ClaudeCLISearch
from .analyze import analyze_query, to_markdown
from . import competitor_keywords as ckw

console = Console()


def _settings(ctx: click.Context) -> Settings:
    return ctx.obj["settings"]


@click.group()
@click.option("--config", "config_path", default=None, help="Path to competitors.yaml")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """Competitor blog keyword intelligence (scrape-based)."""
    ctx.ensure_object(dict)
    ctx.obj["settings"] = load_settings(config_path)


# --- setup / health ------------------------------------------------------

@cli.command("init-db")
@click.pass_context
def init_db(ctx: click.Context) -> None:
    """Create the database schema."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        db.init_schema(conn)
    console.print("[green]Schema created / verified.[/green]")


@cli.command("check")
@click.pass_context
def check(ctx: click.Context) -> None:
    """Verify the configured database is reachable."""
    s = _settings(ctx)
    target = s.require_db()
    kind = "Postgres" if s.is_postgres else "SQLite"
    try:
        with db.connect(target) as conn:
            conn.execute("SELECT 1")
        shown = target.split("@")[-1] if s.is_postgres else target
        console.print(f"[green]{kind}: OK[/green] - {shown}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{kind}: FAILED[/red] - {exc}")


@cli.command("migrate-to-pg")
@click.option("--from", "src_path", default=None,
              help="Source SQLite file (default: DB_PATH / competitorbreak.db)")
@click.pass_context
def migrate_to_pg(ctx: click.Context, src_path: str | None) -> None:
    """Copy all data from the SQLite file into the configured PostgreSQL database.

    Target Postgres must be configured in .env (PGHOST/… or DATABASE_URL). The
    target tables are cleared first, rows are copied preserving ids, then the id
    sequences are advanced so new inserts don't collide."""
    import os
    import sqlite3
    from pathlib import Path
    from .config import ROOT

    s = _settings(ctx)
    if not s.is_postgres:
        raise click.ClickException(
            "No Postgres target configured. Set PGHOST/PGUSER/… or DATABASE_URL in .env."
        )

    src = src_path or os.getenv("DB_PATH") or "competitorbreak.db"
    src_file = Path(src)
    if not src_file.is_absolute():
        src_file = ROOT / src_file
    if not src_file.exists():
        raise click.ClickException(f"SQLite file not found: {src_file}")

    # Parent-before-child so foreign keys resolve.
    order = ["competitors", "keywords", "pages", "page_keywords",
             "runs", "serp_runs", "serp_results", "settings"]
    id_tables = [t for t in order if t != "settings"]

    srccon = sqlite3.connect(str(src_file))
    srccon.row_factory = sqlite3.Row
    try:
        with db.connect(s.require_db()) as pg:
            pg.execute(
                "TRUNCATE " + ", ".join(order) + " RESTART IDENTITY CASCADE"
            )
            total = 0
            for table in order:
                cur = srccon.execute(f"SELECT * FROM {table}")
                cols = [d[0] for d in cur.description]
                collist = ", ".join(cols)
                placeholders = ", ".join("?" for _ in cols)
                rows = cur.fetchall()
                for row in rows:
                    pg.execute(
                        f"INSERT INTO {table} ({collist}) VALUES ({placeholders})",
                        [row[c] for c in cols],
                    )
                console.print(f"  {table}: {len(rows)} rows", style="dim")
                total += len(rows)
            # Advance each id sequence past the copied maximum id.
            for table in id_tables:
                pg.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
                )
        console.print(f"[green]Migrated {total} rows into Postgres.[/green]")
    finally:
        srccon.close()


@cli.command("sync-config")
@click.pass_context
def sync_config(ctx: click.Context) -> None:
    """Seed competitors (and self domain) from competitors.yaml into the DB."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        count = 0
        if s.self_domain:
            db.upsert_competitor(conn, s.self_domain, s.self_name, is_self=True)
            count += 1
        for comp in s.competitors:
            db.upsert_competitor(
                conn, comp.domain, comp.name, is_self=False, blog_url=comp.blog_url
            )
            count += 1
    console.print(f"[green]Synced {count} domains from config.[/green]")


@cli.command("add-competitor")
@click.argument("domain")
@click.option("--name", default=None)
@click.option("--blog-url", default=None, help="Explicit blog index or sitemap URL")
@click.option("--self", "is_self", is_flag=True, help="Mark as your own domain")
@click.pass_context
def add_competitor(
    ctx: click.Context, domain: str, name: str | None, blog_url: str | None, is_self: bool
) -> None:
    """Add or update a single competitor domain."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        db.upsert_competitor(conn, domain, name, is_self=is_self, blog_url=blog_url)
    console.print(f"[green]Saved[/green] {domain}")


@cli.command("list-competitors")
@click.pass_context
def list_competitors(ctx: click.Context) -> None:
    """List tracked domains."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = db.list_competitors(conn)
    table = Table(title="Tracked domains")
    for col in ("id", "domain", "name", "self", "blog_url"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]), r["domain"], r.get("name") or "",
            "yes" if r["is_self"] else "", r.get("blog_url") or "",
        )
    console.print(table)


# --- collection ----------------------------------------------------------

@cli.command("collect")
@click.option("--competitor", "domain", default=None, help="One domain (default: all)")
@click.option("--max-pages", type=int, default=None, help="Max posts per competitor")
@click.pass_context
def collect(ctx: click.Context, domain: str | None, max_pages: int | None) -> None:
    """Discover + scrape competitor blogs and store their target keywords."""
    s = _settings(ctx)
    max_pages = max_pages or s.max_pages
    fetcher = Fetcher(delay=s.crawl_delay, respect_robots=s.respect_robots)
    with db.connect(s.require_db()) as conn:
        if domain:
            comp = db.get_competitor(conn, domain)
            if not comp:
                raise click.ClickException(
                    f"{domain} not tracked. Add it with `add-competitor` or `sync-config`."
                )
            targets = [comp]
        else:
            targets = db.list_competitors(conn)
        if not targets:
            raise click.ClickException("No competitors tracked. Run `sync-config` first.")

        try:
            for comp in targets:
                console.print(f"[bold]{comp['domain']}[/bold]")
                res = collect_competitor(
                    conn, fetcher, comp,
                    blog_patterns=s.blog_path_patterns,
                    max_pages=max_pages,
                    log=lambda m: console.print(m, style="dim"),
                )
                console.print(
                    f"  [green]done[/green]: {res.posts_found} found, "
                    f"{res.posts_scraped} scraped"
                )
                conn.commit()
        finally:
            fetcher.close()


@cli.command("serp")
@click.option("--keyword", "-k", "keywords", multiple=True,
              help="Keyword to check (repeatable). Default: top target terms.")
@click.option("--limit", type=int, default=30, help="Max auto-selected terms")
@click.option("--country", default="us")
@click.option("--delay", type=float, default=4.0, help="Seconds between queries")
@click.option("--show-browser", is_flag=True, help="Run Chromium non-headless")
@click.option("--source", type=click.Choice(["playwright", "dataforseo"]),
              default="playwright", help="Ranking source")
@click.option("--sandbox", is_flag=True, help="DataForSEO sandbox (free dummy data)")
@click.pass_context
def serp(ctx, keywords, limit, country, delay, show_browser, source, sandbox):
    """Get Google rankings for keywords (Playwright scrape, or DataForSEO API)."""
    s = _settings(ctx)
    if source == "dataforseo":
        auth = s.dataforseo_auth()
        if not auth:
            raise click.ClickException(
                "DataForSEO credentials missing in .env "
                "(DATAFORSEO_LOGIN/PASSWORD or DATAFORSEO_Base64_format)."
            )
        scraper = DataForSEOSerp(auth=auth, country=country, sandbox=sandbox)
    else:
        scraper = SerpScraper(country=country, delay=delay, headless=not show_browser)
    try:
        with db.connect(s.require_db()) as conn:
            if keywords:
                terms = [
                    {"keyword_id": db.upsert_keyword(conn, k.strip().lower()),
                     "term": k.strip().lower()}
                    for k in keywords if k.strip()
                ]
            else:
                terms = db.target_terms(conn, limit)
            if not terms:
                raise click.ClickException(
                    "No keywords. Pass -k <keyword> or run `collect` first."
                )
            console.print(f"Checking {len(terms)} keyword(s) on Google ({country}) ...")
            res = collect_serp(
                conn, scraper, terms, country=country,
                log=lambda m: console.print(m, style="dim"),
            )
            if res.blocked:
                console.print("[red]Blocked by Google (CAPTCHA).[/red] Try fewer "
                              "keywords, a longer --delay, or --show-browser.")
            console.print(
                f"[green]done[/green]: {res.keywords_checked} keywords, "
                f"{res.results_stored} results"
            )
    finally:
        scraper.close()


# --- reports -------------------------------------------------------------

@cli.command("analyze")
@click.argument("query")
@click.option("--max-results", type=int, default=5)
@click.option("--source", type=click.Choice(["claude-cli", "anthropic"]),
              default="claude-cli", help="LLM source")
@click.option("--model", default="claude-haiku-5-5")
@click.option("--out", "out_path", default=None, help="Write a Markdown report to this path")
@click.pass_context
def analyze_cmd(ctx, query, max_results, source, model, out_path):
    """LLM web-search ranking for a query + each ranked page's on-page keywords."""
    s = _settings(ctx)
    # Our own site, for the rank comparison — the competitor marked is_self.
    self_domain = None
    try:
        with db.connect(s.require_db()) as conn:
            row = conn.execute(
                "SELECT domain FROM competitors WHERE is_self = 1 LIMIT 1"
            ).fetchone()
            if row:
                self_domain = row["domain"]
    except Exception:  # noqa: BLE001 - comparison is best-effort
        self_domain = None
    if source == "claude-cli":
        llm = ClaudeCLISearch(model=model, max_results=max_results, self_domain=self_domain)
    else:
        if not s.anthropic_api_key:
            raise click.ClickException("ANTHROPIC_API_KEY is not set in .env.")
        llm = LLMSearch(
            api_key=s.anthropic_api_key, model=model, max_results=max_results,
            self_domain=self_domain,
        )
    fetcher = Fetcher(delay=0.5, respect_robots=s.respect_robots, use_playwright=False)
    try:
        rows = analyze_query(
            llm, fetcher, query, max_results=max_results, self_domain=self_domain,
            log=lambda m: console.print(m, style="dim"),
        )
        import datetime as _dt
        import pathlib as _pl
        import re as _re
        from .config import ROOT
        if out_path:
            path = _pl.Path(out_path)
        else:
            slug = _re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:50] or "query"
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            exports = ROOT / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            path = exports / f"analyze-{slug}-{ts}.md"
        path.write_text(to_markdown(query, rows, source=source), encoding="utf-8")
        console.print(f"[green]Saved:[/green] {path}")
        for r in rows:
            tag = " [cyan](us)[/cyan]" if r.get("is_self") else ""
            console.print(
                f"[bold]Position {r['position']}/100[/bold] {r['domain']}{tag} "
                f"[dim]— {r.get('primary_keyword') or ''}[/dim]"
            )
            console.print(f"    {r['url']}", style="dim")
            kws = ", ".join(
                f"{k['term']} ({k['count']})" for k in r["keywords"][:6]
            )
            if kws:
                console.print(f"    keywords: {kws}", style="dim")
    finally:
        llm.close()
        fetcher.close()


@cli.command("competitor-keywords")
@click.argument("domain")
@click.option("--max-keywords", type=int, default=15,
              help="How many ranking keywords to return")
@click.option("--model", default="claude-haiku-5-5")
@click.option("--out", "out_path", default=None, help="Write a Markdown report to this path")
@click.pass_context
def competitor_keywords_cmd(ctx, domain, max_keywords, model, out_path):
    """Ask claude (web search) which keywords DOMAIN ranks for and which page
    ranks for each. Output: keyword -> approx position -> ranking page + URL."""
    s = _settings(ctx)
    llm = ClaudeCLISearch(model=model)
    # Playwright (headless browser) renders JS and gets past many bot-blocks that
    # return nothing to plain requests (e.g. ibos.io), so density can be computed.
    fetcher = Fetcher(delay=0.5, respect_robots=s.respect_robots, use_playwright=True)
    try:
        rows = ckw.reverse_keywords(
            llm, domain, max_keywords=max_keywords, fetcher=fetcher,
            log=lambda m: console.print(m, style="dim"),
        )
        import datetime as _dt
        import pathlib as _pl
        import re as _re
        from .config import ROOT
        if out_path:
            path = _pl.Path(out_path)
        else:
            slug = _re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:50] or "domain"
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            exports = ROOT / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            path = exports / f"competitor-keywords-{slug}-{ts}.md"
        path.write_text(ckw.to_markdown(domain, rows, source="claude-cli"), encoding="utf-8")
        console.print(f"[green]Saved:[/green] {path} ({len(rows)} keywords)")
        for r in rows:
            pos = f"{r['position']}/100" if r["position"] is not None else "?"
            dens = (
                f" [magenta]{r['kw_count']}× · {r['kw_density']}%[/magenta]"
                if r.get("kw_count") is not None else ""
            )
            console.print(
                f"[bold]Position {pos}[/bold] {r['keyword']} "
                f"[cyan]({r['page_type'] or '—'})[/cyan]{dens}"
            )
            if r["url"]:
                console.print(f"    {r['url']}", style="dim")
    finally:
        llm.close()
        fetcher.close()


@cli.command("crawl-keywords")
@click.argument("domain")
@click.option("--all", "all_pages", is_flag=True,
              help="Crawl EVERY page in the sitemap, not just blog/content pages")
@click.option("--max-pages", type=int, default=15,
              help="Max pages to crawl (raise this with --all)")
@click.option("--max-keywords", type=int, default=10, help="Top keywords per page")
@click.option("--out", "out_path", default=None, help="Write a Markdown report to this path")
@click.pass_context
def crawl_keywords_cmd(ctx, domain, all_pages, max_pages, max_keywords, out_path):
    """Crawl DOMAIN's own pages and report, per page, the keywords it is densest
    in (what each page targets) + the page URL. No LLM, no rank data — reliable."""
    s = _settings(ctx)
    fetcher = Fetcher(delay=0.5, respect_robots=s.respect_robots, use_playwright=True)
    try:
        rows = ckw.crawl_keyword_index(
            fetcher, domain, blog_patterns=s.blog_path_patterns,
            max_pages=max_pages, all_pages=all_pages,
            log=lambda m: console.print(m, style="dim"),
        )
        import datetime as _dt
        import pathlib as _pl
        import re as _re
        from .config import ROOT
        if out_path:
            path = _pl.Path(out_path)
        else:
            slug = _re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:50] or "domain"
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            exports = ROOT / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            path = exports / f"crawl-keywords-{slug}-{ts}.md"
        path.write_text(ckw.index_to_markdown(domain, rows), encoding="utf-8")
        console.print(f"[green]Saved:[/green] {path} ({len(rows)} keywords)")
        for r in rows[:40]:
            console.print(f"[bold]{r['keyword']}[/bold] [dim]— {r['total']}× on {r['pages']} page(s)[/dim]")
            for u in r["urls"][:5]:
                console.print(f"    {u['count']}×  {u['url']}", style="dim")
    finally:
        fetcher.close()


@cli.command("rank-check")
@click.argument("keywords", nargs=-1, required=True)
@click.option("--competitor", "competitor", default=None,
              help="A competitor domain to track alongside your own site")
@click.option("--model", default="claude-haiku-5-5")
@click.option("--out", "out_path", default=None, help="Write a Markdown report to this path")
@click.pass_context
def rank_check_cmd(ctx, keywords, competitor, model, out_path):
    """Check where YOUR site and a competitor rank for keywords YOU supply.

    Example: cb.py rank-check "hr software bangladesh" "payroll software" --competitor bamboohr.com
    """
    s = _settings(ctx)
    # Your own site = the competitor marked is_self in the DB.
    self_domain = None
    with db.connect(s.require_db()) as conn:
        row = conn.execute(
            "SELECT domain FROM competitors WHERE is_self = 1 LIMIT 1"
        ).fetchone()
        if row:
            self_domain = row["domain"]
    track = [d for d in (self_domain, competitor) if d]
    if not track:
        raise click.ClickException(
            "No domains to track. Mark your site with `add-competitor <domain> --self`, "
            "or pass --competitor <domain>."
        )

    llm = ClaudeCLISearch(model=model, self_domain=track)
    try:
        rows = ckw.rank_keywords(
            llm, list(keywords), track_domains=track,
            log=lambda m: console.print(m, style="dim"),
        )
        import datetime as _dt
        import pathlib as _pl
        import re as _re
        from .config import ROOT
        if out_path:
            path = _pl.Path(out_path)
        else:
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            exports = ROOT / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            path = exports / f"rank-check-{ts}.md"
        path.write_text(ckw.ranks_to_markdown(rows, track), encoding="utf-8")
        console.print(f"[green]Saved:[/green] {path}")
        for row in rows:
            console.print(f"[bold]{row['keyword']}[/bold]")
            for d in track:
                info = row["ranks"].get(d)
                tag = " [cyan](you)[/cyan]" if d == self_domain else ""
                if info:
                    console.print(
                        f"    {d}{tag}: [bold]#{info['position']}/100[/bold] "
                        f"({info['page_type']}) {info['url']}", style="dim"
                    )
                else:
                    console.print(f"    {d}{tag}: not found in top results", style="dim")
    finally:
        llm.close()


@cli.command("ask")
@click.argument("query")
@click.option("--out", "out_path", default=None, help="Output .md path")
@click.pass_context
def ask_cmd(ctx, query, out_path):
    """Free-form Claude web-search query; save the answer to exports/<name>.md."""
    import datetime
    import pathlib
    import re as _re
    from .config import ROOT

    llm = ClaudeCLISearch()
    console.print("Asking Claude (web search) ...", style="dim")
    text = llm.raw(query)

    if out_path:
        path = pathlib.Path(out_path)
    else:
        slug = _re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:50] or "query"
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        exports = ROOT / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        path = exports / f"{slug}-{ts}.md"

    now = datetime.datetime.now().isoformat(timespec="seconds")
    path.write_text(f"# {query}\n\n_{now}_\n\n{text}\n", encoding="utf-8")
    console.print(f"[green]Saved:[/green] {path}")
    console.print(text)


@cli.group()
def report() -> None:
    """Read-side reports."""


def _render(title: str, rows: list[dict], columns: list[str]) -> None:
    if not rows:
        console.print(f"[yellow]No data for: {title}[/yellow]")
        return
    table = Table(title=title)
    for col in columns:
        table.add_column(col)
    for r in rows:
        table.add_row(*["" if r.get(c) is None else str(r.get(c)) for c in columns])
    console.print(table)


@report.command("topics")
@click.argument("domain")
@click.option("--limit", type=int, default=50)
@click.option("--min-posts", type=int, default=1)
@click.pass_context
def report_topics(ctx: click.Context, domain: str, limit: int, min_posts: int) -> None:
    """Most-targeted keywords across a competitor's blog."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.topics(conn, domain, limit, min_posts)
    _render(f"{domain} — top target keywords", rows, ["term", "posts", "as_primary"])


@report.command("posts")
@click.argument("domain")
@click.option("--limit", type=int, default=50)
@click.pass_context
def report_posts(ctx: click.Context, domain: str, limit: int) -> None:
    """Each scraped post and its inferred primary keyword."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.posts(conn, domain, limit)
    _render(
        f"{domain} — posts",
        rows,
        ["primary_keyword", "kw_count", "kw_density", "word_count",
         "published_at", "title", "url"],
    )


@report.command("density")
@click.argument("domain")
@click.argument("keyword")
@click.option("--limit", type=int, default=50)
@click.pass_context
def report_density(ctx: click.Context, domain: str, keyword: str, limit: int) -> None:
    """How many times a keyword is used in each of a domain's posts."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.keyword_density(conn, domain, keyword, limit)
    _render(f"{domain} — '{keyword}' density", rows,
            ["count", "density", "word_count", "title", "url"])


@report.command("post-keywords")
@click.argument("url")
@click.option("--limit", type=int, default=50)
@click.pass_context
def report_post_keywords(ctx: click.Context, url: str, limit: int) -> None:
    """All target keywords of a single post with count and density."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.post_keywords(conn, url, limit)
    _render(f"Keywords in {url}", rows, ["term", "source", "count", "density"])


@report.command("gaps")
@click.option("--competitor", "domain", default=None, help="One domain (default: all)")
@click.option("--limit", type=int, default=50)
@click.pass_context
def report_gaps(ctx: click.Context, domain: str | None, limit: int) -> None:
    """Keywords competitors target that your domain does not."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.gaps(conn, domain, limit)
    _render("Content gaps (they target, we don't)", rows, ["term", "posts", "competitors"])


@report.command("overlap")
@click.argument("domain")
@click.option("--limit", type=int, default=50)
@click.pass_context
def report_overlap(ctx: click.Context, domain: str, limit: int) -> None:
    """Keywords both your domain and a competitor target."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.overlap(conn, domain, limit)
    _render(f"Shared targets vs {domain}", rows, ["term", "their_posts"])


@report.command("rankings")
@click.argument("domain")
@click.option("--limit", type=int, default=100)
@click.pass_context
def report_rankings(ctx: click.Context, domain: str, limit: int) -> None:
    """Keywords a domain ranks for on Google (latest position)."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.rankings(conn, domain, limit)
    _render(f"{domain} — Google rankings", rows,
            ["keyword", "position", "url", "captured_at"])


@report.command("serp")
@click.argument("keyword")
@click.option("--limit", type=int, default=20)
@click.pass_context
def report_serp(ctx: click.Context, keyword: str, limit: int) -> None:
    """Full latest SERP for a keyword — who ranks where."""
    s = _settings(ctx)
    with db.connect(s.require_db()) as conn:
        rows = reports.serp_keyword(conn, keyword, limit)
    _render(f"SERP: {keyword}", rows, ["position", "domain", "tracked", "url", "title"])


if __name__ == "__main__":
    cli()
