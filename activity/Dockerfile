# LoLdle Activity: the aiohttp server plus the built React front, in one image.
# Build context is the REPO ROOT (the server imports from `bot/`):
#   docker build -f activity/Dockerfile -t loldle-activity .
# No build argument: the Discord client id is served at runtime by /api/config.

# --- Stage 1: the front. Tests run here so a broken front never becomes an image.
FROM node:22-alpine AS web
WORKDIR /web
# .npmrc first: `legacy-peer-deps=true` is what lets `npm ci` resolve the lockfile.
COPY activity/web/package.json activity/web/package-lock.json activity/web/.npmrc ./
RUN npm ci
COPY activity/web/ ./
RUN npm test -- --run && npm run build

# --- Stage 2: the server. Only what it imports: no tests, no .env, no node_modules.
FROM python:3.12-slim

WORKDIR /app

RUN pip install uv --no-cache-dir

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY bot/ bot/
COPY activity/__init__.py activity/__init__.py
COPY activity/server/ activity/server/
COPY --from=web /web/dist activity/web/dist

ENV ACTIVITY_STATIC_DIR=/app/activity/web/dist \
    PYTHONUNBUFFERED=1

RUN useradd --system --create-home --uid 10001 activity
USER activity

EXPOSE 8080

# python:slim has no curl. /health is 503 until configured, so an unconfigured container is "unhealthy" on purpose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"]

# --no-sync: without it `uv run` reinstalls the dev dependencies at start-up.
CMD ["uv", "run", "--no-sync", "python", "-m", "activity.server"]
