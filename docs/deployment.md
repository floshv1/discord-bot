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

> `compose.yml` is the production stack and pulls prebuilt images from GHCR. To run **your local
> code** instead, use the development environment below.

---

## Development environment

`compose.dev.yml` builds the bot from local source and runs it against its **own** Postgres +
Lavalink, fully isolated from production (separate Docker project name, network, and database
volume). It reads its variables from `.env.dev` so the dev/test bot uses a **different token** —
Discord allows only one gateway connection per token, so this avoids clashing with the production bot.

### One-time setup

1. Create a **separate** Discord application + bot token at
   [discord.com/developers/applications](https://discord.com/developers/applications) and invite it to a
   **test server**. (Reusing the production token would make the two bots disconnect each other.)
2. Copy the dev env template and fill it in:
   ```bash
   cp .env.dev.example .env.dev
   ```
   Set `DISCORD_TOKEN` (test bot), `GUILD_ID` (test server), `LOG_CHANNEL_ID` (a channel in the test
   server) and `POSTGRES_PASSWORD`. `.env.dev` is gitignored.

### Run

```bash
docker compose -f compose.dev.yml --env-file .env.dev up --build   # start (builds local code)
docker compose -f compose.dev.yml --env-file .env.dev down          # stop
docker compose -f compose.dev.yml --env-file .env.dev down -v       # stop + wipe the dev DB
```

The dev Postgres is exposed on `127.0.0.1:5433` (not 5432) so it never clashes with a local
production database. Migrations apply automatically on startup, just like production.

### Run tests / lint locally (no Docker)

```bash
uv run ruff check .   # lint
uv run pytest         # tests
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

### Development stack (branch `dev`)

A **second, separate** Komodo stack can run the `dev` branch for testing, fully isolated from
production. Unlike the production stack (which pulls prebuilt `:latest` images), the dev stack
**builds the image from source** via `compose.dev.yml` (`build: .`) — so it always runs the exact
code on `dev`, and **nothing in the production pipeline (`ci.yml`, `compose.yml`) is touched**.
Pushing `dev` does not trigger CI (CI runs on `main` only), so no image is ever published.

**One-time setup in Komodo UI:**

1. **Create a new Stack** (separate from the production one).
2. Point it at this repo, **branch `dev`**, **compose file `compose.dev.yml`**.
3. **Set environment variables** in the stack (the Komodo equivalent of `.env.dev`):

   | Variable | Value |
   |---|---|
   | `DISCORD_TOKEN` | Token of a **dedicated TEST bot** (never the production token) |
   | `POSTGRES_PASSWORD` | Any value (the dev DB is isolated) |
   | `GUILD_ID` | Your **test** server ID |
   | `LOG_CHANNEL_ID` | A channel ID in the **test** server |

   Optional: `LOG_IGNORED_CHANNEL_IDS`, `VOICE_LEADERBOARD_CHANNEL_ID`, `BIRTHDAY_CHANNEL_ID`,
   `BIRTHDAY_ANNOUNCE_CHANNEL_ID`, `LAVALINK_PASSWORD`. `DATABASE_URL` is built inside the compose file.

4. **Deploy.** Because `compose.dev.yml` declares `build: .`, Komodo builds the bot image from the
   `dev` branch on deploy (no GHCR pull). **Do not enable GHCR auto-redeploy** for this stack — it has
   no published image to poll. To test new code, **redeploy the stack** (Komodo re-clones `dev` and
   rebuilds), or enable a git poll/webhook on `dev` to automate it.

**Isolation guarantees:**

- Separate Compose project (`discord-bot-dev`) and database volume (`pgdata_dev`) — the dev DB can
  never touch production data. The dev Postgres is exposed on `127.0.0.1:5433` (not 5432).
- **Use a separate TEST bot token.** Discord allows only one gateway connection per token, so reusing
  the production token would make the two bots disconnect each other.
- The dev stack runs its own Postgres + Lavalink alongside production. Stop it when not testing to
  free resources.

---

## Database

PostgreSQL runs in the `db` service defined in `compose.yml`. Data is stored in a named Docker volume (`pg_data`) and survives container restarts.

All tables are created automatically by the migrations in `bot/db/migrations/` when the bot starts for the first time. Migration files are applied in alphabetical order as a single transaction.

### Schema

All tables are created by migration files in `bot/db/migrations/`, applied alphabetically in a single transaction on startup.

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

-- 003_game_queue.sql
CREATE TABLE IF NOT EXISTS game_presets (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    name         TEXT NOT NULL,
    player_count INT NOT NULL,
    UNIQUE (guild_id, name)
);

CREATE TABLE IF NOT EXISTS game_queues (
    id              BIGSERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    channel_id      BIGINT NOT NULL,
    preset_id       BIGINT NOT NULL REFERENCES game_presets(id),
    status          TEXT NOT NULL DEFAULT 'open',  -- open | filled | done | cancelled
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at       TIMESTAMPTZ,
    start_time      TIMESTAMPTZ,
    reminder_sent   BOOLEAN NOT NULL DEFAULT FALSE,
    message_id      BIGINT,
    creator_user_id BIGINT
);

CREATE TABLE IF NOT EXISTS queue_members (
    id           BIGSERIAL PRIMARY KEY,
    queue_id     BIGINT NOT NULL REFERENCES game_queues(id),
    user_id      BIGINT NOT NULL,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    in_lane      BOOLEAN NOT NULL DEFAULT FALSE,
    cant_attend  BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (queue_id, user_id)
);

-- 005_suggestions.sql
CREATE TABLE IF NOT EXISTS suggestion_config (
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT
);

CREATE TABLE IF NOT EXISTS suggestions (
    id         BIGSERIAL PRIMARY KEY,
    number     INT NOT NULL,
    guild_id   BIGINT NOT NULL,
    author_id  BIGINT NOT NULL,
    type       TEXT NOT NULL,       -- feature | improvement
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',  -- open | accepted | rejected | implemented
    message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (guild_id, number)
);

CREATE TABLE IF NOT EXISTS suggestion_votes (
    suggestion_id BIGINT NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL,
    vote          INT NOT NULL,     -- 1 = upvote, -1 = downvote
    PRIMARY KEY (suggestion_id, user_id)
);

-- 008_audit_logs.sql
CREATE TABLE IF NOT EXISTS audit_logs (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id   BIGINT,
    target_id  BIGINT,
    channel_id BIGINT,
    details    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 009_discord_users.sql
CREATE TABLE IF NOT EXISTS discord_users (
    user_id      BIGINT PRIMARY KEY,
    username     TEXT NOT NULL,
    display_name TEXT,
    avatar       TEXT,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 010_voice_sessions.sql
CREATE TABLE IF NOT EXISTS voice_sessions (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS voice_leaderboard (
    guild_id          BIGINT PRIMARY KEY,
    channel_id        BIGINT NOT NULL,
    weekly_message_id BIGINT,
    alltime_message_id BIGINT
);

-- 011_birthdays.sql
CREATE TABLE IF NOT EXISTS birthdays (
    user_id    BIGINT PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    username   TEXT NOT NULL,
    day        INT NOT NULL CHECK (day BETWEEN 1 AND 31),
    month      INT NOT NULL CHECK (month BETWEEN 1 AND 12),
    year       INT NOT NULL CHECK (year BETWEEN 1900 AND 2100),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS birthday_config (
    guild_id             BIGINT PRIMARY KEY,
    channel_id           BIGINT NOT NULL,
    upcoming_message_id  BIGINT,
    month_message_id     BIGINT
);

-- 013_queue_rework.sql
CREATE TABLE IF NOT EXISTS queue_config (
    guild_id         BIGINT PRIMARY KEY,
    channel_id       BIGINT NOT NULL,
    panel_message_id BIGINT
);

CREATE TABLE IF NOT EXISTS game_subscriptions (
    id        BIGSERIAL PRIMARY KEY,
    guild_id  BIGINT NOT NULL,
    user_id   BIGINT NOT NULL,
    preset_id BIGINT NOT NULL REFERENCES game_presets(id) ON DELETE CASCADE,
    UNIQUE (guild_id, user_id, preset_id)
);

-- game_queues gains a per-queue size override and an idle-expiry timestamp:
ALTER TABLE game_queues ADD COLUMN IF NOT EXISTS player_count INT;            -- NULL = preset default
ALTER TABLE game_queues ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
```

### Game queue setup

After inviting the bot, an admin runs `/setup queue #channel` once to post the control panel in a
dedicated channel. Members create queues by tapping a game button (choosing Duo / Flex / Custom size),
and opt into per-game ping notifications via the panel's **Subscriptions** button.

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
