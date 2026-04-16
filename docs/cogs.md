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

Tracks time spent in voice channels per user and maintains an auto-updating leaderboard.

### Behavior

- On **cog load**: closes any open sessions left over from a bot restart (sets `left_at = NOW()`)
- On **voice state change**: inserts a new session row when a user joins, closes it when they leave
- **Daily at UTC 00:00**: edits the pinned leaderboard message in the configured channel (or posts a new one if the message was deleted)

### Commands

| Command | Permission | Description |
|---|---|---|
| `/voice stats [user]` | — | Shows weekly + all-time voice time and 5 most recent sessions for a user (defaults to yourself) |
| `/voice leaderboard [period]` | — | Top 10 users by voice time; `period` is `weekly` (default) or `alltime` |
| `/voice setchannel <channel>` | Manage Channels | Sets the channel for the persistent daily leaderboard message and posts it immediately |

### Notes

- The leaderboard message ID is stored in `voice_leaderboard_config` so the bot always edits the same message rather than posting a new one each day
- Set `VOICE_LEADERBOARD_CHANNEL_ID` in env to pre-configure the leaderboard channel at startup without needing `/voice setchannel`

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
