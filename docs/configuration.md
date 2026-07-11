# Configuration

The bot is configured entirely through environment variables. It will refuse to start with a clear error message if any required variable is missing or invalid.

---

## Required Variables

| Variable | Type | Description |
|---|---|---|
| `DISCORD_TOKEN` | string | Bot token from the Discord Developer Portal |
| `DATABASE_URL` | string | PostgreSQL connection string |
| `GUILD_ID` | integer | Discord server (guild) ID — used to sync slash commands. The bot is **single-guild**: it only acts on this server and ignores events from any other guild it shares (see [cogs.md](cogs.md)). |
| `LOG_CHANNEL_ID` | integer | Channel ID where all audit log embeds are posted |

---

## How to Get Each Value

### `DISCORD_TOKEN`

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Select your application (or create one)
3. Go to **Bot** in the left sidebar
4. Click **Reset Token** and copy the result

> Keep this secret. Anyone with this token can control your bot.

### `DATABASE_URL`

Format: `postgresql://user:password@host:port/database`

When running locally with the provided `compose.yml`:
```
postgresql://botuser:botpass@localhost:5432/discord_bot
```

When running inside Docker Compose (bot container talking to the `db` service):
```
postgresql://botuser:${POSTGRES_PASSWORD}@db:5432/discord_bot
```
This is already wired up correctly in `compose.yml`.

### `GUILD_ID`

1. In Discord, open **User Settings → Advanced** and enable **Developer Mode**
2. Right-click your server icon in the sidebar
3. Click **Copy Server ID**

### `LOG_CHANNEL_ID`

1. Enable Developer Mode (see above)
2. Right-click the channel you want audit logs posted to
3. Click **Copy Channel ID**

---

## Optional Variables

| Variable | Type | Description |
|---|---|---|
| `LOG_MUTED_EVENTS` | string | Comma-separated event types kept out of the log *channel* (still written to the DB). Unset uses a sane default that mutes the flooding ones (`message_sent`, `slash_command`, voice mute/deafen). Set to empty to mirror every event. |

> **Feature channels are not env vars.** Voice, birthday, currency, betting, queue and suggestions
> all take their channel as a `/setup <feature> <channel>` argument and store it in the DB, so you can
> move them without touching the environment or restarting.
| `LOG_IGNORED_CHANNEL_IDS` | string | Comma-separated list of channel IDs excluded from audit logs (e.g. `123,456,789`) |

---

## Privileged Intents

The following privileged intents must be enabled in the Discord Developer Portal under **Bot → Privileged Gateway Intents**:

| Intent | Required for |
|---|---|
| Server Members Intent | `on_member_join`, `on_member_remove`, `on_member_update` |
| Message Content Intent | Reading message content in `on_message`, `on_message_edit`, `on_message_delete` |
| Voice States Intent | Tracking voice joins/leaves for the voice leaderboard |

---

## Local Development

For local development against your own code and a **test bot**, use the isolated development
environment (`compose.dev.yml` + `.env.dev`). It uses a separate token, database, and Docker project
so it never conflicts with production. See the
[Development environment](deployment.md#development-environment) section of the deployment guide.

```bash
cp .env.dev.example .env.dev   # fill in the TEST bot token, test guild, etc.
docker compose -f compose.dev.yml --env-file .env.dev up --build
```

Or, with only the dev database container running
(`docker compose -f compose.dev.yml --env-file .env.dev up -d db`), run the bot directly on the host
(the dev DB is exposed on port **5433**):

```powershell
# PowerShell
$env:DISCORD_TOKEN="..."
$env:DATABASE_URL="postgresql://botuser:devpass@localhost:5433/discord_bot"
$env:GUILD_ID="..."
$env:LOG_CHANNEL_ID="..."
uv run python main.py
```

> Music needs Lavalink, which is only started by the full compose stack — running the bot directly is
> best for non-music work.
