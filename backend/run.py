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
    uvicorn.run("backend.app:app", host="0.0.0.0", port=port, reload=False)
