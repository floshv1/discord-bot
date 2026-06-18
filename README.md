# Discord Bot

A private Discord bot for a small community: music, game-lobby queues, audit logging, moderation,
suggestions, a voice leaderboard, and birthdays. Built with Python 3.12, discord.py, Wavelink,
asyncpg, and PostgreSQL. Deployed via Docker and managed by Komodo.

---

## Features

- **Music** — Lavalink-backed playback: play, autoplay, skip/previous, queue, lyrics
- **Game queues** — a control panel where members open game lobbies (Duo / Full team / Custom), join with a button, get auto-pinged via per-game subscriptions, with start-time reminders and auto-cleanup
- **Audit logs** — every server event (messages, members, voice, roles, channels, threads, invites) posted as a color-coded embed to a dedicated log channel
- **Moderation** — `/kick`, `/ban`, `/unban`, `/timeout`, `/warn`, `/history`, `/clear` with DB-backed history
- **Suggestions** — issue-style suggestions with 👍/👎 voting and status tracking
- **Voice leaderboard** — auto-updating 7-day and all-time voice-time rankings
- **Birthdays** — registered birthdays with pinned upcoming/this-month embeds and midnight wishes
- **Single-guild by design** — only acts on `GUILD_ID`; ignores events from any other server it shares
- **Auto-migrations** — schema applied on startup, no manual SQL required
- **Clean config** — fails fast with a clear error if any required env var is missing

---

## Quick Start

### 1. Set environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

See [docs/configuration.md](docs/configuration.md) for details on each variable.

### 2. Run with Docker Compose

```bash
docker compose up --build
```

This starts both the bot and a PostgreSQL container. Migrations run automatically.

### 3. Verify

Expected startup output:
```
INFO | Connecting to PostgreSQL...
INFO | PostgreSQL connection pool created.
INFO | Migrations applied.
INFO | Loaded cog: bot.cogs.logs.cog
INFO | Loaded cog: bot.cogs.moderation.cog
INFO | Loaded cog: bot.cogs.queue.cog
INFO | Loaded cog: bot.cogs.suggestions.cog
INFO | Loaded cog: bot.cogs.voice.cog
INFO | Loaded cog: bot.cogs.birthday.cog
INFO | Loaded cog: bot.cogs.music.cog
INFO | Loaded cog: bot.cogs.setup.cog
INFO | Slash commands synced to guild <GUILD_ID>.
INFO | Bot ready — logged in as YourBot#1234 (...)
```

> **Local development:** to run your local code against a separate **test bot** (so tokens don't
> clash with production), use the dev environment — see
> [docs/deployment.md](docs/deployment.md#development-environment):
> ```bash
> cp .env.dev.example .env.dev   # fill in the TEST bot token + test server
> docker compose -f compose.dev.yml --env-file .env.dev up --build
> ```

---

## Project Structure

```
discord-bot/
├── main.py                        # Entry point
├── compose.yml                    # Production stack (prebuilt GHCR images)
├── compose.dev.yml                # Development stack (builds local code, isolated)
├── .env.example / .env.dev.example
├── Dockerfile
├── pyproject.toml                 # uv-managed dependencies
├── uv.lock
├── docs/
│   ├── configuration.md           # Environment variables reference
│   ├── deployment.md              # Docker, dev environment & Komodo guide
│   └── cogs.md                    # Cog & command reference
├── bot/
│   ├── core/
│   │   ├── bot.py                 # Bot subclass — pool, cogs, slash sync
│   │   └── config.py              # Env var validation
│   ├── db/
│   │   ├── client.py              # asyncpg pool singleton
│   │   ├── models.py              # Migration loader
│   │   └── migrations/            # Numbered .sql, applied alphabetically on startup
│   └── cogs/
│       ├── logs/                  # Server event listeners (single-guild filtered)
│       ├── moderation/            # Mod slash commands
│       ├── music/                 # Lavalink playback
│       ├── queue/                 # Game-lobby panel (cog/views/service/embeds)
│       ├── suggestions/           # Suggestion board
│       ├── voice/                 # Voice leaderboard
│       ├── birthday/              # Birthday tracking
│       └── setup/                 # /setup commands for the above
├── dashboard/                     # FastAPI + Next.js admin dashboard
└── tests/                         # pytest (config, embeds, music, queue, suggestions)
```

---

## Documentation

- [Configuration](docs/configuration.md) — required env vars and how to get them
- [Deployment](docs/deployment.md) — Docker Compose and Komodo setup
- [Cogs Reference](docs/cogs.md) — all events logged and commands available

---

## Tech Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Framework | discord.py ≥ 2.4 |
| Audio | Wavelink ≥ 3.4 + Lavalink |
| Package manager | uv |
| Database | PostgreSQL via asyncpg |
| Logging | loguru → stdout |
| Deployment | Docker + Komodo |

---

## Development

**Activate the pre-push hook** (once per clone):

```bash
git config core.hooksPath .githooks
```

Before every `git push`, this will automatically:
- Fix and format code with Ruff
- Run the test suite

**Run checks manually:**

```bash
uv run ruff check --fix .   # lint + auto-fix
uv run ruff format .         # format
uv run pytest                # tests
uv run pip-audit             # security audit
```
