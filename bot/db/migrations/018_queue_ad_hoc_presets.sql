-- Members can spin up a queue for any game via the panel's "Other" button, which used to
-- persist that game as a permanent button on the shared panel — an end-run around the
-- mod-gated /queue add, and a way to fill the panel's 23-button budget with junk.
--
-- Ad-hoc presets now exist but stay off the panel. Existing presets were all created
-- deliberately (or are the seeded defaults), so they keep their button.
ALTER TABLE game_presets
    ADD COLUMN IF NOT EXISTS on_panel BOOLEAN NOT NULL DEFAULT TRUE;
