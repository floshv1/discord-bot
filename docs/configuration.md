# Configuration

The bot is configured entirely through environment variables. It will refuse to start with a clear error message if any required variable is missing or invalid.

---

## Required Variables

| Variable | Type | Description |
|---|---|---|
| `DISCORD_TOKEN` | string | Bot token from the Discord Developer Portal |
| `DATABASE_URL` | string | PostgreSQL connection string |
| `GUILD_ID` | integer | Discord server (guild) ID — used to sync slash commands |
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

## Privileged Intents

The following privileged intents must be enabled in the Discord Developer Portal under **Bot → Privileged Gateway Intents**:

| Intent | Required for |
|---|---|
| Server Members Intent | `on_member_join`, `on_member_remove`, `on_member_update` |
| Message Content Intent | Reading message content in `on_message`, `on_message_edit`, `on_message_delete` |

---

## Local Development

Create a `.env` file at the project root (already in `.gitignore`):

```dotenv
DISCORD_TOKEN=your_token
POSTGRES_PASSWORD=botpass
DATABASE_URL=postgresql://botuser:botpass@localhost:5432/discord_bot
GUILD_ID=123456789012345678
LOG_CHANNEL_ID=987654321098765432
```

Then run with Docker Compose (starts Postgres automatically):

```bash
docker compose up --build
```

Or export vars manually and run directly:

```powershell
# PowerShell
$env:DISCORD_TOKEN="..."
$env:DATABASE_URL="postgresql://botuser:botpass@localhost:5432/discord_bot"
$env:GUILD_ID="..."
$env:LOG_CHANNEL_ID="..."
uv run python main.py
```
