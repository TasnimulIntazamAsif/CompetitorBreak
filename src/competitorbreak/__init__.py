"""CompetitorBreak: competitor keyword & ranking intelligence.

Pulls, for each competitor domain, which keywords they rank for and which blog
pages target which keywords (via the Ahrefs API v3), stores it in PostgreSQL,
optionally scrapes the blog pages for on-page analysis, and reports content gaps
against your own domain.
"""

__version__ = "0.1.0"
