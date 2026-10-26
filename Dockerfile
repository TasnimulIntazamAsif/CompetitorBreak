# syntax=docker/dockerfile:1

# --- Stage 1: build the Vite/React frontend -------------------------------
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime with Playwright Chromium ---------------------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/competitorbreak.db \
    PORT=8000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + its OS dependencies, for the SERP/rankings feature.
# Retried to tolerate transient CDN drops while fetching the browser build.
RUN for i in 1 2 3; do playwright install --with-deps chromium && break || sleep 5; done

# Application source
COPY src/ ./src/
COPY backend/ ./backend/
COPY cb.py competitors.yaml pyproject.toml ./
COPY docker-entrypoint.sh ./

# Built frontend from stage 1 (backend serves this at /)
COPY --from=frontend /frontend/dist ./frontend/dist

RUN chmod +x docker-entrypoint.sh && mkdir -p /data

EXPOSE 8000
VOLUME ["/data"]
ENTRYPOINT ["./docker-entrypoint.sh"]
