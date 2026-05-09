# Cogs Reference

---

## Logs Cog (`bot/cogs/logs/cog.py`)

Listens to Discord gateway events and posts a compact color-coded embed to `LOG_CHANNEL_ID` for each one.

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

## Queue Cog (`bot/cogs/queue/cog.py`)

Game lobby queue system. Members join a queue; the embed auto-updates as players fill in. Start times are interpreted in **Europe/Paris** timezone.

### Default presets

Seeded automatically on startup (once per guild):

| Game | Players |
|---|---|
| `lol` | 5 |
| `overwatch` | 5 |

### Commands

| Command | Permission | Description |
|---|---|---|
| `/queue join <game> [start_time]` | — | Joins the open queue for a game (creates it if none exists). Optional `start_time` in `HH:MM` (Paris time) |
| `/queue list` | — | Lists all open queues with player counts and start times |
| `/queue cancel <game>` | — | Cancels the active queue for a game and updates the embed |
| `/queue add <game> <player_count>` | Kick Members | Adds a custom game preset (2–100 players) |
| `/queue remove <game>` | Kick Members | Removes a game preset |

### Embed states

| Status | Title | Color |
|---|---|---|
| `open` | 🎮 GAME | Blurple |
| `filled` | ✅ GAME — Lobby ready! | Green |
| `cancelled` | ❌ GAME — Cancelled | Dark Grey |

### Automatic behavior

- **Auto-expire**: open queues older than 1 hour are cancelled every minute; the embed updates to show the cancelled state
- **Start-time reminder**: ~10 minutes before the configured start time, the bot sends a message in the queue channel mentioning all current members
- **Persistent buttons**: Join / Leave buttons survive bot restarts (views re-registered on `cog_load`)

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
