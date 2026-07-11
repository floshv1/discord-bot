-- Birthday wishes fired only inside the exact minute of 00:00 Paris. If the bot was down,
-- restarting, or the tick simply drifted past that minute, the day was skipped silently and
-- nobody got wished. Conversely a restart landing inside minute 0 could post them twice.
--
-- Recording the last day we announced makes the send idempotent (claim-once-per-day) and
-- lets a bot that comes up late still catch up on today's birthdays.
CREATE TABLE IF NOT EXISTS birthday_announcements (
    guild_id       BIGINT PRIMARY KEY,
    last_wishes_on DATE NOT NULL
);
