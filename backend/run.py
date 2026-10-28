"""Run the backend: `python backend/run.py` (from the project root).

Equivalent to `uvicorn backend.app:app --host 0.0.0.0 --port 8000`.
"""

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    # Auto-reload for local dev: set RELOAD=1 (accepts 1/true/yes/on).
    reload = os.getenv("RELOAD", "").lower() in ("1", "true", "yes", "on")
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        reload_dirs=[str(ROOT / "backend"), str(ROOT / "src")] if reload else None,
    )
