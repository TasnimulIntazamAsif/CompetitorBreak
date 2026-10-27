"""Configuration loading: environment variables (.env) + competitors.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Project root = two levels up from this file (src/competitorbreak/config.py).
ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class CompetitorEntry:
    domain: str
    name: str | None = None
    blog_url: str | None = None  # optional explicit blog index/sitemap override


@dataclass
class Settings:
    db_path: str
    max_pages: int = 300
    crawl_delay: float = 1.0
    respect_robots: bool = True
    self_domain: str | None = None
    self_name: str | None = None
    competitors: list[CompetitorEntry] = field(default_factory=list)
    blog_path_patterns: list[str] = field(default_factory=list)
    # DataForSEO credentials (from .env; secrets stay out of the DB settings)
    dataforseo_login: str | None = None
    dataforseo_password: str | None = None
    dataforseo_base64: str | None = None
    dataforseo_sandbox: bool = False

    def dataforseo_auth(self) -> str | None:
        """Basic-auth base64 string, or None if no credentials are configured."""
        from .dataforseo import make_auth

        return make_auth(
            self.dataforseo_login, self.dataforseo_password, self.dataforseo_base64
        )

    def require_db(self) -> str:
        """Return the SQLite file path, ensuring its parent directory exists."""
        path = Path(self.db_path)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)


def load_settings(yaml_path: Path | str | None = None) -> Settings:
    """Load settings from the environment and competitors.yaml."""
    yaml_file = Path(yaml_path) if yaml_path else ROOT / "competitors.yaml"

    data: dict = {}
    if yaml_file.exists():
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}

    self_block = data.get("self") or {}
    competitors = [
        CompetitorEntry(
            domain=c["domain"], name=c.get("name"), blog_url=c.get("blog_url")
        )
        for c in (data.get("competitors") or [])
        if c.get("domain")
    ]

    return Settings(
        db_path=os.getenv("DB_PATH", "competitorbreak.db"),
        max_pages=_env_int("MAX_PAGES", 300),
        crawl_delay=_env_float("CRAWL_DELAY", 1.0),
        respect_robots=_env_bool("RESPECT_ROBOTS", True),
        self_domain=self_block.get("domain"),
        self_name=self_block.get("name"),
        competitors=competitors,
        blog_path_patterns=data.get("blog_path_patterns") or ["/blog/"],
        dataforseo_login=os.getenv("DATAFORSEO_LOGIN") or None,
        dataforseo_password=os.getenv("DATAFORSEO_PASSWORD") or None,
        dataforseo_base64=os.getenv("DATAFORSEO_Base64_format")
        or os.getenv("DATAFORSEO_BASE64")
        or None,
        dataforseo_sandbox=_env_bool("DATAFORSEO_SANDBOX", False),
    )
