# Deployment

---

## Docker Compose (local)

The `compose.yml` at the project root starts both the bot and a PostgreSQL container.

```bash
docker compose up --build
```

The bot waits for Postgres to pass its healthcheck before starting. Migrations are applied automatically on first startup.

**Stop:**
```bash
docker compose down
```

**Stop and wipe the database volume:**
```bash
docker compose down -v
```

---

## Komodo

Komodo manages the production deployment. It reads `compose.yml` directly from the repository.

### Setup

1. **Push your repo** to GitHub or Gitea (without `.env` — it's gitignored).

2. **Create a Stack in Komodo:**
   - Set the repo URL and branch
   - Set the compose file path to `compose.yml`

3. **Set environment variables** in Komodo's stack environment section:

   | Variable | Value |
   |---|---|
   | `DISCORD_TOKEN` | Your bot token |
   | `POSTGRES_PASSWORD` | A strong random password |
   | `GUILD_ID` | Your Discord server ID |
   | `LOG_CHANNEL_ID` | Your log channel ID |

   Komodo injects these at deploy time. `DATABASE_URL` is built automatically inside `compose.yml` — you do not need to set it separately.

4. **Deploy** from Komodo's UI. On each new deploy, Komodo pulls the latest commit, rebuilds the bot image, and restarts the stack.

### Auto-deploy (GitOps)

Komodo polls GHCR directly — no webhook, no Tailscale exposure needed.

```
git push main
    └─► CI: lint → test → security → docker push → ghcr.io/floshv1/discord-bot:latest
                                                              ▲
                                              Komodo polls GHCR periodically
                                                              │
                                                    new image detected
                                                              │
                                                    Stack redeploy
```

**One-time setup in Komodo UI:**

1. Open your Stack → **Webhooks / Auto Redeploy** section
2. Enable **Auto Redeploy** (Komodo will poll GHCR for changes on `latest`)
3. Set the polling interval (e.g. 1 minute)

No GitHub secrets needed. Komodo initiates all outbound connections.

---

## Database

PostgreSQL runs in the `db` service defined in `compose.yml`. Data is stored in a named Docker volume (`pg_data`) and survives container restarts.

All tables are created automatically by the migrations in `bot/db/migrations/` when the bot starts for the first time. Migration files are applied in alphabetical order as a single transaction.

### Schema

```sql
-- 001_initial.sql
CREATE TABLE IF NOT EXISTS mod_actions (
    id           SERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    target_id    BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    action_type  TEXT NOT NULL,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 002_voice_sessions.sql
CREATE TABLE IF NOT EXISTS voice_sessions (
    id               BIGSERIAL PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    user_id          BIGINT NOT NULL,
    channel_id       BIGINT NOT NULL,
    joined_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at          TIMESTAMPTZ,
    duration_seconds INT
);

CREATE TABLE IF NOT EXISTS voice_leaderboard_config (
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT
);

-- 003_game_queue.sql
CREATE TABLE IF NOT EXISTS game_presets (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    name         TEXT NOT NULL,
    player_count INT NOT NULL,
    UNIQUE (guild_id, name)
);

CREATE TABLE IF NOT EXISTS game_queues (
    id            BIGSERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    channel_id    BIGINT NOT NULL,
    preset_id     BIGINT NOT NULL REFERENCES game_presets(id),
    status        TEXT NOT NULL DEFAULT 'open',  -- open | filled | cancelled
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at     TIMESTAMPTZ,
    start_time    TIMESTAMPTZ,
    reminder_sent BOOLEAN NOT NULL DEFAULT FALSE,
    message_id    BIGINT  -- added by 004_queue_message_id.sql
);

CREATE TABLE IF NOT EXISTS queue_members (
    id        BIGSERIAL PRIMARY KEY,
    queue_id  BIGINT NOT NULL REFERENCES game_queues(id),
    user_id   BIGINT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (queue_id, user_id)
);
```

---

## Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install uv --no-cache-dir
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen
COPY bot/ bot/
COPY main.py .
CMD ["uv", "run", "python", "main.py"]
```

Only production dependencies are installed (`--no-dev`). The image is built from the locked `uv.lock` for reproducible builds.
