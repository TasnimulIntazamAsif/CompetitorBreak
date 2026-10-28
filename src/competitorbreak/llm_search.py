"""LLM web-search ranking provider (Anthropic Claude + the web_search tool).

Given a query like "best HR software in Bangladesh", Claude runs web searches and
returns the top-ranking website URLs in order. Note: this is the model's cited /
retrieved ordering, which approximates relevance — it is NOT guaranteed to match
Google's exact organic positions.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile

import requests

from .serp import SerpError, SerpResult, registrable_domain

# Run the nested `claude` in a neutral dir (no project CLAUDE.md / memory / MCP)
# and with local tools denied, so it only web-searches — it must not discover or
# run the project's own DataForSEO CLI, which would block in headless mode.
_DENY_TOOLS = ["Bash", "Edit", "Write", "NotebookEdit", "Read", "Glob", "Grep", "Task"]
_NEUTRAL_CWD = tempfile.gettempdir()


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def parse_ranked(text: str, max_results: int = 1000) -> list["SerpResult"]:
    """Extract ranked results from an LLM response: a JSON array first, then
    markdown links, then bare URLs — in order, de-duplicated."""
    if not text:
        return []

    # 1) JSON array (optionally inside ```json fences or surrounded by prose)
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    arr = None
    try:
        arr = json.loads(t)
    except ValueError:
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
            except ValueError:
                arr = None
    if isinstance(arr, list):
        out: list[SerpResult] = []
        for i, it in enumerate(arr):
            if not isinstance(it, dict):
                continue
            url = it.get("url")
            if not url or not str(url).startswith("http"):
                continue
            dom = registrable_domain(url) or (it.get("domain") or "")
            pos = it.get("position") if isinstance(it.get("position"), int) else i + 1
            out.append(SerpResult(pos, url, dom, it.get("title")))
        if out:
            return out[:max_results]

    # 2) Markdown links [title](url), in order
    out = []
    seen: set[str] = set()
    for m in _MD_LINK_RE.finditer(text):
        title, url = m.group(1).strip(), m.group(2).rstrip(".,);")
        if url in seen:
            continue
        seen.add(url)
        out.append(SerpResult(len(out) + 1, url, registrable_domain(url), title))
    if out:
        return out[:max_results]

    # 3) Bare URLs, in order
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,);")
        if url in seen:
            continue
        seen.add(url)
        out.append(SerpResult(len(out) + 1, url, registrable_domain(url), None))
    return out[:max_results]


def _ranking_prompt(
    query: str, max_results: int, self_domain: "str | list[str] | None" = None
) -> str:
    if isinstance(self_domain, str):
        doms = [self_domain]
    elif self_domain:
        doms = list(self_domain)
    else:
        doms = []
    extra = ""
    if doms:
        names = ", ".join(f'"{d}"' for d in doms)
        extra = (
            " ALSO specifically find where each of these sites ranks for this query "
            "and include it in the array with its position, even if it is outside the "
            f"top {max_results}: {names}. If one of them does not rank at all, omit it."
        )
    return (
        "You are a SERP data extractor. You MUST run an actual live web search for "
        "this query using your web search tool before answering — do NOT answer from "
        "prior knowledge or memory. Every URL and position must come from fresh search "
        f'results. Find how websites currently rank on Google for this EXACT query: "{query}". '
        "Search for the full phrase as given — do not shorten, simplify, reinterpret, "
        "or critique it. "
        f"Report the top {max_results} organic results, and for each give its ranking "
        "position — where 1 is the very top result." + extra + " Respond with ONLY a "
        "JSON array and nothing else — no prose, no commentary, no markdown fences, no "
        "table:\n"
        '[{"position": 1, "url": "https://...", "title": "...", "domain": "example.com"}]\n'
        "Use only real URLs taken from your search results."
    )

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"


class LLMSearchError(SerpError):
    pass


class LLMSearch:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_results: int = 10,
        max_searches: int = 4,
        timeout: int = 120,
        self_domain: "str | list[str] | None" = None,
    ):
        if not api_key:
            raise LLMSearchError("ANTHROPIC_API_KEY is not set.")
        self.api_key = api_key
        self.model = model
        self.max_results = max_results
        self.max_searches = max_searches
        self.timeout = timeout
        self.self_domain = self_domain
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        )

    def search(self, query: str) -> list[SerpResult]:
        """Return ranked website results for a query via Claude web search."""
        prompt = _ranking_prompt(query, self.max_results, self.self_domain)
        body = {
            "model": self.model,
            "max_tokens": 2000,
            "tools": [
                {"type": "web_search_20250305", "name": "web_search",
                 "max_uses": self.max_searches}
            ],
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = self.session.post(API_URL, json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            raise LLMSearchError(f"Request failed: {exc}") from exc

        if resp.status_code == 401:
            raise LLMSearchError("Anthropic auth failed (401). Check ANTHROPIC_API_KEY.")
        if resp.status_code >= 400:
            raise LLMSearchError(f"HTTP {resp.status_code}: {resp.text[:400]}")

        data = resp.json()
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()

        rows = self._parse_json(text)
        if not rows:
            rows = self._from_tool_results(data)
        return rows[: self.max_results]

    @staticmethod
    def _parse_json(text: str) -> list[SerpResult]:
        return parse_ranked(text)

    def _from_tool_results(self, data: dict) -> list[SerpResult]:
        """Fallback: take URLs straight from the web_search_tool_result blocks."""
        out: list[SerpResult] = []
        seen: set[str] = set()
        for block in data.get("content", []):
            if block.get("type") != "web_search_tool_result":
                continue
            for r in block.get("content") or []:
                if not isinstance(r, dict) or r.get("type") != "web_search_result":
                    continue
                url = r.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                out.append(SerpResult(len(out) + 1, url, registrable_domain(url), r.get("title")))
        return out

    def close(self) -> None:
        self.session.close()


class ClaudeCLISearch:
    """Runs the local `claude` CLI headless (`claude -p … --allowedTools WebSearch`).

    Uses your existing Claude Code authentication — no separate ANTHROPIC_API_KEY
    needed. Only works where the `claude` CLI is installed and logged in (your
    local machine), not on a remote server.
    """

    def __init__(
        self,
        model: str = "claude-haiku-5-5",
        max_results: int = 10,
        timeout: int = 300,
        self_domain: "str | list[str] | None" = None,
    ):
        self.model = model
        self.max_results = max_results
        self.timeout = timeout
        self.self_domain = self_domain
        self.binary = shutil.which("claude") or "claude"

    def search(self, query: str) -> list[SerpResult]:
        prompt = _ranking_prompt(query, self.max_results, self.self_domain)
        # NOTE: forcing --model made headless WebSearch get "permission denied";
        # the default model works. --strict-mcp-config skips the project's MCP
        # servers so the call stays fast. --allowedTools grants WebSearch.
        # The prompt goes via STDIN, not argv: on Windows `claude.CMD` mangles the
        # trailing flags when the prompt argument contains quotes/brackets (JSON
        # example), which silently dropped --allowedTools/--output-format and made
        # the model refuse ("WebSearch permission not granted"). stdin avoids that.
        cmd = [
            self.binary, "-p",
            "--output-format", "json",
            "--allowedTools", "WebSearch",
            "--disallowedTools", *_DENY_TOOLS,
            "--strict-mcp-config",
        ]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=self.timeout, cwd=_NEUTRAL_CWD,
            )
        except FileNotFoundError as exc:
            raise LLMSearchError(
                "`claude` CLI not found. Install Claude Code, or use the Anthropic "
                "API source."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMSearchError(f"claude CLI timed out after {self.timeout}s.") from exc

        if proc.returncode != 0:
            raise LLMSearchError(f"claude CLI error: {(proc.stderr or proc.stdout or '')[:300]}")

        out = (proc.stdout or "").strip()
        text = out
        try:
            outer = json.loads(out)
            if isinstance(outer, dict):
                if outer.get("is_error"):
                    raise LLMSearchError(
                        f"claude CLI: {outer.get('result') or outer.get('subtype')}"
                    )
                text = outer.get("result") or ""
        except ValueError:
            pass  # stdout wasn't the JSON wrapper; treat it as the text itself

        return LLMSearch._parse_json(text)[: self.max_results]

    def raw(self, prompt: str) -> str:
        """Run `claude -p <prompt>` with web search and return the raw text answer
        (no JSON wrapper) — for free-form queries saved as-is to Markdown."""
        # Prompt via STDIN (see search() note on Windows claude.CMD arg mangling).
        cmd = [self.binary, "-p", "--allowedTools", "WebSearch",
               "--disallowedTools", *_DENY_TOOLS, "--strict-mcp-config"]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=self.timeout, cwd=_NEUTRAL_CWD,
            )
        except FileNotFoundError as exc:
            raise LLMSearchError("`claude` CLI not found. Install Claude Code.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMSearchError(f"claude CLI timed out after {self.timeout}s.") from exc
        if proc.returncode != 0:
            raise LLMSearchError(f"claude CLI error: {(proc.stderr or proc.stdout or '')[:300]}")
        return (proc.stdout or "").strip()

    def close(self) -> None:
        pass
