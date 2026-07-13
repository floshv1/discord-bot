from __future__ import annotations

import asyncpg

from bot.db.client import get_pool

# An absolute majority of the seven-judge bench. A verdict therefore always speaks for more
# than half the jury, not just for whoever showed up first.
QUORUM = 4

GUILTY = 1
INNOCENT = -1


def tally(guilty: int, innocent: int) -> str | None:
    """The verdict, or None while the bench is still deliberating.

    Two conditions, not one: QUORUM ballots cast *and* a strict lead. A 2–2 split at quorum
    decides nothing and leaves the vote open — the fifth ballot cannot tie, so the trial
    always has a way to end.
    """
    if guilty + innocent < QUORUM:
        return None
    if guilty > innocent:
        return "guilty"
    if innocent > guilty:
        return "acquitted"
    return None


async def get_judge_role_id(guild_id: int) -> int | None:
    return await get_pool().fetchval("SELECT judge_role_id FROM reprimand_config WHERE guild_id = $1", guild_id)


async def create_trial(guild_id: int, reprimand_id: int, channel_id: int) -> int:
    return await get_pool().fetchval(
        """
        INSERT INTO tribunal_trials (guild_id, reprimand_id, channel_id)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        guild_id,
        reprimand_id,
        channel_id,
    )


async def set_message_id(trial_id: int, message_id: int) -> None:
    await get_pool().execute("UPDATE tribunal_trials SET message_id = $1 WHERE id = $2", message_id, trial_id)


async def get_trial(trial_id: int) -> asyncpg.Record | None:
    """The trial joined to the sentence it hangs off — one read serves every render."""
    return await get_pool().fetchrow(
        """
        SELECT t.id, t.guild_id, t.channel_id, t.message_id, t.plea, t.verdict, t.reprimand_id,
               r.target_id, r.moderator_id, r.reason, r.expires_at, r.original_nick
        FROM tribunal_trials t
        JOIN reprimands r ON r.id = t.reprimand_id
        WHERE t.id = $1
        """,
        trial_id,
    )


async def count_votes(trial_id: int) -> tuple[int, int]:
    row = await get_pool().fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE vote = 1)  AS guilty,
               COUNT(*) FILTER (WHERE vote = -1) AS innocent
        FROM tribunal_votes WHERE trial_id = $1
        """,
        trial_id,
    )
    return int(row["guilty"]), int(row["innocent"])


async def submit_plea(trial_id: int, plea: str) -> bool:
    """Record the accused's plea. False if they had already pleaded, or the trial is over.

    Conditional so a double-submitted modal cannot overwrite a plea the bench has already
    started voting on.
    """
    row = await get_pool().fetchrow(
        """
        UPDATE tribunal_trials SET plea = $2, plea_at = NOW()
        WHERE id = $1 AND plea IS NULL AND verdict IS NULL
        RETURNING id
        """,
        trial_id,
        plea,
    )
    return row is not None


async def cast_vote(trial_id: int, judge_id: int, vote: int) -> bool:
    """Cast, change, or take back a ballot. Returns True if the vote was withdrawn.

    Clicking the side you already voted for takes the vote back, same as the suggestion
    votes. Anything else is an upsert — a judge who changes their mind still has one voice.
    """
    pool = get_pool()
    existing = await pool.fetchval(
        "SELECT vote FROM tribunal_votes WHERE trial_id = $1 AND judge_id = $2", trial_id, judge_id
    )
    if existing == vote:
        await pool.execute("DELETE FROM tribunal_votes WHERE trial_id = $1 AND judge_id = $2", trial_id, judge_id)
        return True
    await pool.execute(
        """
        INSERT INTO tribunal_votes (trial_id, judge_id, vote)
        VALUES ($1, $2, $3)
        ON CONFLICT (trial_id, judge_id) DO UPDATE SET vote = EXCLUDED.vote, voted_at = NOW()
        """,
        trial_id,
        judge_id,
        vote,
    )
    return False


async def claim_verdict(trial_id: int, verdict: str) -> bool:
    """Stamp the verdict, but only if nobody has stamped one yet.

    The claim and the write are one statement on purpose. Two judges casting the deciding
    ballot at the same instant would otherwise both read "quorum reached", both free the
    accused, and both announce it. Zero rows back = someone else already ruled; stand down.
    """
    row = await get_pool().fetchrow(
        """
        UPDATE tribunal_trials SET verdict = $2, verdict_at = NOW()
        WHERE id = $1 AND verdict IS NULL
        RETURNING id
        """,
        trial_id,
        verdict,
    )
    return row is not None


async def expire_trial(reprimand_id: int) -> int | None:
    """Close an unjudged trial whose sentence is over. Returns the trial id, or None.

    ``verdict IS NULL`` is what keeps the expiry ticker from stomping on a trial the bench
    already decided: a sentence served after a guilty verdict is just the sentence running
    its course, and that card must not be rewritten.
    """
    return await get_pool().fetchval(
        """
        UPDATE tribunal_trials SET verdict = 'expired', verdict_at = NOW()
        WHERE reprimand_id = $1 AND verdict IS NULL
        RETURNING id
        """,
        reprimand_id,
    )
