"""Command-line interface for CompetitorBreak (scrape pipeline)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from . import db
from .config import Settings, load_settings
from .scraper import Fetcher
from .llm_search import ClaudeCLISearch
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
