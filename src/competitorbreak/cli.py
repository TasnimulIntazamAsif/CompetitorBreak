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
    """Verify the SQLite database is reachable."""
    s = _settings(ctx)
    path = s.require_db()
    try:
        with db.connect(path) as conn:
            conn.execute("SELECT 1")
        console.print(f"[green]SQLite: OK[/green] - {path}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]SQLite: FAILED[/red] - {exc}")


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
@click.pass_context
def serp(ctx, keywords, limit, country, delay, show_browser):
    """Scrape Google rankings for keywords (Playwright, best-effort)."""
    s = _settings(ctx)
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
