#!/bin/sh
# Initialize the SQLite DB + seed competitors on first boot, then run the server.
set -e

mkdir -p "$(dirname "$DB_PATH")" 2>/dev/null || true

python cb.py init-db || true
python cb.py sync-config || true

exec python backend/run.py
