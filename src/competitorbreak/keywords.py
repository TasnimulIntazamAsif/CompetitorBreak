"""Keyword extraction from scraped page text — no external NLP dependencies.

Produces candidate target keywords for a blog post from its title, headings and
body, using stopword-filtered 1-3 word n-grams with position weighting. Also
normalises explicit signals (tags, meta keywords, URL slug).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

# Compact English stopword list (enough to clean n-grams without a dependency).
STOPWORDS: frozenset[str] = frozenset(
    """
    a about above after again against all am an and any are aren't as at be
    because been before being below between both but by can can't cannot could
    couldn't did didn't do does doesn't doing don't down during each few for from
    further had hadn't has hasn't have haven't having he he'd he'll he's her here
    here's hers herself him himself his how how's i i'd i'll i'm i've if in into is
    isn't it it's its itself let's me more most mustn't my myself no nor not of off
    on once only or other ought our ours ourselves out over own same shan't she
    she'd she'll she's should shouldn't so some such than that that's the their
    theirs them themselves then there there's these they they'd they'll they're
    they've this those through to too under until up very was wasn't we we'd we'll
    we're we've were weren't what what's when when's where where's which while who
    who's whom why why's will with won't would wouldn't you you'd you'll you're
    you've your yours yourself yourselves
    get got also may might make made use used using one two best top how what why
    guide tips vs
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


@dataclass
class ExtractedKeywords:
    primary: str | None
    candidates: list[tuple[str, int]]  # (term, score), highest first
    explicit: list[str]                # tags / meta keywords / slug terms


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall((text or "").lower())]


def _ngrams(tokens: list[str], n: int) -> list[str]:
    out: list[str] = []
    for i in range(len(tokens) - n + 1):
        gram = tokens[i : i + n]
        if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
            continue
        if any(len(t) < 2 for t in gram):
            continue
        out.append(" ".join(gram))
    return out


def count_occurrences(text: str, term: str) -> int:
    """Count non-overlapping, case-insensitive occurrences of a word/phrase."""
    if not text or not term:
        return 0
    pattern = r"\b" + r"\s+".join(re.escape(w) for w in term.split()) + r"\b"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def density(count: int, word_count: int | None) -> float:
    """Keyword density as a percentage of the post's word count."""
    if not word_count:
        return 0.0
    return round(count / word_count * 100, 2)


def slug_terms(url: str) -> list[str]:
    """Human-meaningful words from a URL's last path segment."""
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug)
    words = [w for w in re.split(r"[-_]+", slug) if w and w.lower() not in STOPWORDS]
    return words


def extract(
    *,
    title: str | None,
    headings: list[str],
    body: str,
    url: str,
    tags: list[str] | None = None,
    meta_keywords: list[str] | None = None,
    max_candidates: int = 25,
) -> ExtractedKeywords:
    """Score n-grams across weighted fields and pick a primary keyword."""
    weighted: Counter[str] = Counter()

    def add(text: str, weight: int) -> None:
        tokens = tokenize(text)
        for n in (1, 2, 3):
            for gram in _ngrams(tokens, n):
                # Longer phrases get a small boost; they're more specific.
                weighted[gram] += weight * n

    add(title or "", weight=6)
    for h in headings:
        add(h, weight=3)
    add(body, weight=1)

    # Explicit, high-confidence signals.
    explicit: list[str] = []
    for t in (tags or []) + (meta_keywords or []):
        t = t.strip().lower()
        if t and t not in explicit:
            explicit.append(t)
            weighted[t] += 8
    for w in slug_terms(url):
        weighted[w.lower()] += 4

    candidates = weighted.most_common(max_candidates)

    # Primary = highest-scoring candidate that also appears in the title/slug,
    # else the single highest-scoring candidate.
    title_slug = " ".join(tokenize(title or "") + [s.lower() for s in slug_terms(url)])
    primary = None
    for term, _score in candidates:
        if all(part in title_slug for part in term.split()):
            primary = term
            break
    if primary is None and candidates:
        primary = candidates[0][0]

    return ExtractedKeywords(primary=primary, candidates=candidates, explicit=explicit)
