# Cogs Reference

---

## Logs Cog (`bot/cogs/logs/cog.py`)

Listens to Discord gateway events and posts a compact color-coded embed to `LOG_CHANNEL_ID` for each one.

> **Single-guild filtering:** the bot is designed for one server (`GUILD_ID`). Every listener ignores
> events from any other guild it happens to share (e.g. a test server), so they never leak into the log
> channel. The voice cog applies the same `GUILD_ID` filter to session tracking.

### Embed format

```
┃ [color] Action Title — context details
                         timestamp (footer)
```

One embed per event. All details on a single description line. Timestamp is Discord's native embed timestamp.

### Color legend

| Color | Category |
|---|---|
| Light Grey | Message sent |
| Yellow | Message edited |
| Orange | Message deleted / bulk delete |
| Blue | Voice joined |
| Dark Blue | Voice left / moved / muted / deafened |
| Green | Member joined |
| Red | Member left |
| Dark Red | Member banned |
| Teal | Member unbanned / thread events |
| Purple | Role added/removed, nickname changed |
| Blurple | Channel created/deleted/renamed |
| Light Grey | Invite created/deleted |

### Events handled

**Messages**

| Event | Embed |
|---|---|
| `on_message` | Message Sent — channel, author, content preview |
| `on_message_edit` | Message Edited — channel, author, before → after |
| `on_message_delete` | Message Deleted — channel, author, content preview |
| `on_bulk_message_delete` | Bulk Delete — channel, count |

**Voice**

| Event | Embed |
|---|---|
| `on_voice_state_update` | Voice Joined / Left / Moved / State — member + channel(s) |

**Members**

| Event | Embed |
|---|---|
| `on_member_join` | Member Joined — mention, ID, account age in days |
| `on_member_remove` | Member Left — mention, ID |
| `on_member_ban` | Member Banned — mention, ID |
| `on_member_unban` | Member Unbanned — mention, ID |
| `on_member_update` | Nickname Changed / Role Added / Role Removed |

**Server Structure**

| Event | Embed |
|---|---|
| `on_guild_channel_create` | Channel Created |
| `on_guild_channel_delete` | Channel Deleted |
| `on_guild_channel_update` | Channel Renamed (name change only) |
| `on_guild_role_create` | Role Created |
| `on_guild_role_delete` | Role Deleted |
| `on_guild_role_update` | Role Renamed (name change only) |
| `on_invite_create` | Invite Created — URL, inviter, max uses |
| `on_invite_delete` | Invite Deleted — URL |
| `on_thread_create` | Thread Created — thread + parent channel |
| `on_thread_delete` | Thread Deleted — name + parent channel |
| `on_thread_update` | Thread Archived / Unarchived |

---

## Moderation Cog (`bot/cogs/moderation/cog.py`)

All commands are slash commands. Every action writes a row to the `mod_actions` table and posts an embed to `LOG_CHANNEL_ID`.

### Commands

| Command | Permission | Description |
|---|---|---|
| `/kick <user> [reason]` | Kick Members | Kicks the user from the server |
| `/ban <user> [reason] [delete_days]` | Ban Members | Bans the user; optionally deletes recent messages (0–7 days) |
| `/unban <user_id> [reason]` | Ban Members | Unbans a user by their Discord ID |
| `/timeout <user> <duration> <reason>` | Kick Members | Applies a Discord timeout for `duration` minutes |
| `/warn <user> <reason>` | Kick Members | Records a warning in the DB and DMs the user |
| `/history <user>` | Kick Members | Shows the last 10 mod actions for the user |
| `/clear <amount> [user]` | Manage Messages | Deletes up to `amount` messages (1–100); optional `user` filter deletes only that member's messages |

### Log embed colors

| Action | Color |
|---|---|
| kick | Red |
| ban | Dark Red |
| unban | Teal |
| timeout | Orange |
| warn | Yellow |

---

## Voice Cog (`bot/cogs/voice/cog.py`)

Tracks time spent in voice channels per user and maintains two auto-updating pinned leaderboards: one for the rolling 7-day window and one for all-time totals.

### Behavior

- On **cog load** (after bot ready): closes any orphaned open sessions left from before a restart, then opens fresh sessions for all members currently in voice channels
- On **voice state change**: opens a session row on join, closes it on leave; moves (channel switch) close the old session and open a new one; mute/deafen events are ignored
- **Daily at midnight Paris time**: edits both pinned leaderboard messages in `VOICE_LEADERBOARD_CHANNEL_ID`

### Commands

| Command | Permission | Description |
|---|---|---|
| `/voice setup` | Manage Guild | Posts the two pinned leaderboard messages to `VOICE_LEADERBOARD_CHANNEL_ID` and records their IDs in the DB. Run once after setting the env var. Re-running updates the stored message IDs. |

### Leaderboard format

Top 10 members by total seconds. Duration shown as `Xh YYm` (or `Zm` if under one hour).

```
#1 username — 12h 34m
#2 username — 8h 02m
…
```

- **Weekly** (`🎙️ Top Vocal — 7 derniers jours`): sums all session time where `started_at > NOW() - 7 days`, including any currently open sessions
- **All-time** (`🏆 Top Vocal — Tout temps`): sums all completed sessions (`ended_at IS NOT NULL`)

### Notes

- Message IDs are stored in the `voice_leaderboard` table; the bot always edits the same messages rather than posting new ones
- If `VOICE_LEADERBOARD_CHANNEL_ID` is not set the cog still tracks sessions — only the embed updates are skipped

---

## Queue Cog (`bot/cogs/queue/`)

Game-lobby queue system built around a **persistent control panel** in a dedicated channel.
A member taps a game, picks a size (Duo / Full team / Custom), and a queue card is posted that
others join with a button. The first joiner is the host's duo partner. Start times are interpreted
in **Europe/Paris** timezone. Split into focused modules: `cog.py` (commands + ticker), `views.py`
(panel, size picker, modals, subscriptions, card), `service.py` (DB helpers), `embeds.py` (embed builders).

### Setup

Run `/setup queue <channel>` once (see the Setup Cog). It posts the control panel in the chosen
channel and stores it in `queue_config`. All queue cards and pings then appear in that channel.

### Default presets

Seeded automatically on startup (once per guild): `lol` (5), `overwatch` (5).

### Control panel

| Button | Action |
|---|---|
| 🎮 *\<game>* (one per preset) | Opens an ephemeral size picker: **Duo (2)** / **Full team (preset size)** / **Custom…** |
| ➕ Other game | Modal to start a queue for any game (name + size + optional time); creates the preset if new |
| 🔔 Subscriptions | Ephemeral multi-select to opt in/out of per-game ping notifications |

### Queue card buttons

| Button | Who | Action |
|---|---|---|
| ✅ Join | anyone | Join the queue (overflow goes to a waiting list once full) |
| 🚫 Can't attend | members | Leave; if you held a main slot, the first waiting player is promoted (FIFO) |
| ⏰ Set time | host | Set/update the start time (modal) |
| 🏁 Close | anyone in the queue / Manage Messages | Closes the queue; the card is removed shortly after |

### Slash commands

| Command | Permission | Description |
|---|---|---|
| `/queue add <game> <player_count>` | Kick Members | Adds a game preset (2–100); refreshes the panel |
| `/queue remove <game>` | Kick Members | Removes a preset (blocked while it has active queues); refreshes the panel |

### Embed states

| Status | Title | Color |
|---|---|---|
| `open` | 🎮 GAME | Blurple |
| `filled` | ✅ GAME — Lobby ready! | Green |
| `done` | 🏁 GAME — Game over! | Gold |
| `cancelled` | ❌ GAME — Cancelled | Dark Grey |

### Subscriptions

Stored in `game_subscriptions` (per guild/user/preset). When a queue is created, every subscriber
of that game (except the host) is pinged once in the channel. Toggle anytime via the panel's 🔔 button.

### Automatic behavior (ticker, every minute)

- **Auto-close past start**: queues whose `start_time` passed by more than 30 min are closed; the card is removed
- **Idle expiry** (safety net for queues with no start time): open after ~45 min idle, filled after ~2 h idle are cancelled and removed (`last_activity_at` is bumped on every join / can't-attend)
- **Start-time reminder**: ~10 min before the start time, the bot pings the current main members
- **Persistent views**: the panel and all active queue cards survive restarts (re-registered on `cog_load`)

---

## Suggestions Cog (`bot/cogs/suggestions/cog.py`)

GitHub-issue-style suggestion system. A fixed channel message with two buttons lets users submit ideas; each becomes a numbered embed with 👍/👎 voting. Admins can update suggestion status in place.

### Setup

Run `/suggest setup <channel>` to post the entry-point message. This is idempotent — running it again moves the setup to a new channel.

### Commands

| Command | Permission | Description |
|---|---|---|
| `/suggest setup <channel>` | Manage Channels | Posts the fixed entry-point message with New Feature and Improvement buttons |
| `/suggest status <number> <status>` | Kick Members | Updates a suggestion's status and edits the embed in place |

### Embed states

| Status | Color |
|---|---|
| open | Blurple |
| accepted | Green |
| rejected | Red |
| implemented | Purple |

### Notes

- Suggestion numbers are guild-scoped and sequential (`#1`, `#2`, …)
- A user can vote 👍 or 👎 once per suggestion; clicking the same button again toggles it off; switching direction replaces the previous vote
- Vote views and setup buttons survive bot restarts (re-registered on `cog_load`)

---

## Birthday Cog (`bot/cogs/birthday/cog.py`)

Members register their birthday once. Two pinned embeds update daily and the bot sends birthday wishes at midnight Paris time.

### Setup

1. Set `BIRTHDAY_CHANNEL_ID` (where the two pinned embeds live) and `BIRTHDAY_ANNOUNCE_CHANNEL_ID` (where birthday wishes are posted) in your env
2. Run `/birthday setup` in any channel — the bot posts both embeds to `BIRTHDAY_CHANNEL_ID` and records their IDs in the DB

### Commands

| Command | Permission | Description |
|---|---|---|
| `/birthday set <day> <month> <year>` | — | Registers or updates your birthday. Immediately refreshes both pinned embeds. |
| `/birthday delete` | — | Removes your birthday from the DB and refreshes both embeds |
| `/birthday list` | — | Ephemeral embed showing all upcoming birthdays sorted by next occurrence |
| `/birthday month` | — | Ephemeral embed showing birthdays in the current calendar month |
| `/birthday setup` | Manage Guild | Posts the two pinned embeds to `BIRTHDAY_CHANNEL_ID` and stores their message IDs. Safe to re-run — replaces existing stored IDs. |

### Pinned embeds

| Embed | Title | Color | Content |
|---|---|---|---|
| Upcoming | 🎉 Anniversaires à venir | Blue | All registered birthdays sorted by days until next occurrence |
| This month | 📅 Anniversaires de \<mois\> | Purple | Only birthdays in the current calendar month, sorted by day |

Each field shows: `DD/MM (N ans) • dans X jours` or `aujourd'hui 🎂`.

### Automatic behavior

- **On cog load**: both embeds refresh immediately (so they're correct after a bot restart)
- **Daily at midnight Paris time**: both embeds refresh, then the bot checks for today's birthdays and posts a wish to `BIRTHDAY_ANNOUNCE_CHANNEL_ID`

### Notes

- Birthdays are stored per `user_id` — a user has at most one entry across all guilds
- If `BIRTHDAY_CHANNEL_ID` is not set, embed updates are skipped silently; commands still work
- If `BIRTHDAY_ANNOUNCE_CHANNEL_ID` is not set, midnight wishes are skipped silently

---

## Setup Cog (`bot/cogs/setup/cog.py`)

Admin-only `/setup` group that initializes the message-based features (posts their entry-point /
panel messages and records the message IDs in the DB so they persist across restarts).

| Command | Permission | Description |
|---|---|---|
| `/setup voice` | Manage Guild | Posts the two voice-leaderboard messages to `VOICE_LEADERBOARD_CHANNEL_ID` |
| `/setup birthday` | Manage Guild | Posts the two birthday messages to `BIRTHDAY_CHANNEL_ID` |
| `/setup suggestions <channel>` | Manage Channels | Posts the suggestion entry-point message in the channel |
| `/setup queue <channel>` | Manage Channels | Posts the game-queue control panel in the channel |

Each command is safe to re-run — it reposts the message and updates the stored ID.
