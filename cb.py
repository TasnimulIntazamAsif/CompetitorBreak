#!/usr/bin/env python
"""Entry point so you can run `python cb.py ...` from the project root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from competitorbreak.cli import cli  # noqa: E402

if __name__ == "__main__":
    cli()
