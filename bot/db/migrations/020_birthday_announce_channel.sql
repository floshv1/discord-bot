-- Voice and birthday were the last two features whose channel came from an env var, so
-- changing where they post meant editing the environment and restarting the bot. Every
-- other feature takes the channel as a /setup argument and stores it here. The pinned
-- embeds already had a channel_id column; the birthday *wishes* channel did not.
--
-- NULL means "announce in the same channel as the embeds".
ALTER TABLE birthday_config
    ADD COLUMN IF NOT EXISTS announce_channel_id BIGINT;
