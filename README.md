# Discord Bot

A private Discord bot with full audit logging and moderation slash commands. Built with Python 3.12, discord.py, asyncpg, and PostgreSQL. Deployed via Docker and managed by Komodo.

---

## Features

- **Audit log cog** — every server event (messages, members, voice, roles, channels, threads, invites) posted as a color-coded embed to a dedicated log channel
- **Moderation cog** — `/kick`, `/ban`, `/unban`, `/timeout`, `/warn`, `/history` slash commands with DB-backed history
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
INFO | Slash commands synced to guild <GUILD_ID>.
INFO | Bot ready — logged in as YourBot#1234 (...)
```

---

## Project Structure

```
discord-bot/
├── main.py                        # Entry point
├── compose.yml                    # Docker Compose (bot + Postgres)
├── Dockerfile
├── pyproject.toml                 # uv-managed dependencies
├── uv.lock
├── docs/
│   ├── configuration.md           # Environment variables reference
│   ├── deployment.md              # Docker & Komodo deployment guide
│   └── cogs.md                    # Cog & command reference
├── bot/
│   ├── core/
│   │   ├── bot.py                 # Bot subclass — pool, cogs, slash sync
│   │   └── config.py              # Env var validation
│   ├── db/
│   │   ├── client.py              # asyncpg pool singleton
│   │   ├── models.py              # Migration loader
│   │   └── migrations/
│   │       └── 001_initial.sql    # mod_actions table
│   └── cogs/
│       ├── logs/cog.py            # All server event listeners
│       └── moderation/cog.py      # Slash commands
└── tests/
    ├── test_config.py
    └── test_embeds.py
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
