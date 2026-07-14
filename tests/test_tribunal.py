from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.tribunal import service, views
from bot.cogs.tribunal.views import PleaView, VerdictView, build_trial_view

# --- The verdict rule ---------------------------------------------------------------
# A verdict needs a quorum *and* a strict lead. Either half alone is a bug: a lead without
# quorum lets one judge condemn on their own, and a quorum without a lead means a 2–2 tie
# would have to be broken by a coin flip.


def test_a_lone_judge_cannot_condemn():
    assert service.tally(guilty=1, innocent=0) is None


def test_three_ballots_are_not_enough_even_when_unanimous():
    # QUORUM is 4 — an absolute majority of the seven-judge bench.
    assert service.tally(guilty=3, innocent=0) is None


def test_a_tie_at_quorum_decides_nothing():
    # 2–2 has the quorum but no majority. The vote must stay open rather than pick a side.
    assert service.tally(guilty=2, innocent=2) is None


def test_the_ballot_that_breaks_the_tie_rules():
    # The fifth ballot cannot tie, so a deadlocked bench always has a way out.
    assert service.tally(guilty=3, innocent=2) == "guilty"
    assert service.tally(guilty=2, innocent=3) == "acquitted"


def test_a_majority_at_quorum_rules():
    assert service.tally(guilty=3, innocent=1) == "guilty"
    assert service.tally(guilty=1, innocent=3) == "acquitted"


def test_an_empty_bench_rules_nothing():
    assert service.tally(guilty=0, innocent=0) is None


# --- Atomicity ----------------------------------------------------------------------


async def test_the_verdict_can_only_be_stamped_once():
    # Two judges casting the deciding ballot at the same instant would both read "quorum
    # reached". If the claim were a SELECT followed by an UPDATE, both would free the
    # accused and both would announce it. The guard has to live in the UPDATE itself.
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("bot.cogs.tribunal.service.get_pool", return_value=pool):
        won = await service.claim_verdict(trial_id=1, verdict="acquitted")

    sql = pool.fetchrow.call_args[0][0]
    assert sql.strip().upper().startswith("UPDATE")
    assert "VERDICT IS NULL" in sql.upper()
    assert "RETURNING" in sql.upper()
    assert won is False  # no row back = someone else already ruled; stand down


async def test_a_served_sentence_never_rewrites_a_verdict_that_already_fell():
    # A guilty verdict's sentence running its course must not flip the card to "expired".
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)
    with patch("bot.cogs.tribunal.service.get_pool", return_value=pool):
        await service.expire_trial(reprimand_id=7)

    sql = pool.fetchval.call_args[0][0]
    assert "VERDICT IS NULL" in sql.upper()
    assert "RETURNING" in sql.upper()


async def test_a_plea_cannot_be_overwritten_once_the_bench_is_voting():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("bot.cogs.tribunal.service.get_pool", return_value=pool):
        accepted = await service.submit_plea(trial_id=1, plea="pardon")

    sql = pool.fetchrow.call_args[0][0]
    assert "PLEA IS NULL" in sql.upper()
    assert "VERDICT IS NULL" in sql.upper()
    assert accepted is False


async def test_changing_your_mind_does_not_buy_a_second_voice():
    # One judge, one ballot: switching sides is an upsert on the (trial, judge) key.
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=service.INNOCENT)  # they had voted not guilty
    pool.execute = AsyncMock()
    with patch("bot.cogs.tribunal.service.get_pool", return_value=pool):
        removed = await service.cast_vote(trial_id=1, judge_id=42, vote=service.GUILTY)

    sql = pool.execute.call_args[0][0]
    assert "ON CONFLICT (TRIAL_ID, JUDGE_ID) DO UPDATE" in sql.upper()
    assert removed is False


async def test_clicking_the_side_you_already_backed_takes_the_vote_back():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=service.GUILTY)
    pool.execute = AsyncMock()
    with patch("bot.cogs.tribunal.service.get_pool", return_value=pool):
        removed = await service.cast_vote(trial_id=1, judge_id=42, vote=service.GUILTY)

    assert removed is True
    assert pool.execute.call_args[0][0].strip().upper().startswith("DELETE")


# --- Who is allowed to judge --------------------------------------------------------

JUDGE_ROLE_ID = 77
ACCUSED_ID = 100
COMPLAINANT_ID = 200

OPEN_TRIAL = {
    "id": 1,
    "guild_id": 9,
    "channel_id": 5,
    "message_id": 7,
    "reprimand_id": 3,
    "plea": "je le regrette",
    "verdict": None,
    "target_id": ACCUSED_ID,
    "moderator_id": COMPLAINANT_ID,
    "reason": "spam",
    "expires_at": None,
    "original_nick": None,
}


def _voter(user_id: int, *, is_judge: bool = True):
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.user.roles = [MagicMock(id=JUDGE_ROLE_ID)] if is_judge else []
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _bench(**overrides):
    calls = {
        "get_trial": AsyncMock(return_value=OPEN_TRIAL),
        "get_judge_role_id": AsyncMock(return_value=JUDGE_ROLE_ID),
        "cast_vote": AsyncMock(return_value=False),
        "count_votes": AsyncMock(return_value=(3, 1)),
        "claim_verdict": AsyncMock(return_value=True),
        **overrides,
    }
    return [patch.object(views.service, name, mock) for name, mock in calls.items()]


async def _vote(interaction, **overrides):
    # Everything past the verdict — freeing the accused, logging it, announcing it — talks to
    # Discord and the pool, so it is stubbed out here. What's under test is the decision.
    patches = [
        *_bench(**overrides),
        patch.object(views, "refresh_trial_message", AsyncMock()),
        patch.object(views, "_apply_verdict", AsyncMock()),
        patch.object(views, "_announce_verdict", AsyncMock()),
    ]
    for p in patches:
        p.start()
    try:
        await views.VerdictButton(1, service.GUILTY).callback(interaction)
    finally:
        for p in patches:
            p.stop()


async def test_a_judge_can_actually_vote():
    # The positive control. Without it the three "turned away" tests below would still pass
    # if the mock harness were broken, since they only assert that nothing happened.
    interaction = _voter(42)
    await _vote(interaction)
    interaction.response.edit_message.assert_awaited_once()


async def test_the_accused_does_not_judge_their_own_trial():
    interaction = _voter(ACCUSED_ID)
    await _vote(interaction)
    interaction.response.edit_message.assert_not_called()
    assert "propre procès" in interaction.response.send_message.call_args[0][0]


async def test_the_moderator_who_pressed_charges_does_not_also_get_a_ballot():
    # Carrying the accusation was already a call. Voting on it too would be two voices.
    interaction = _voter(COMPLAINANT_ID)
    await _vote(interaction)
    interaction.response.edit_message.assert_not_called()
    assert "accusation" in interaction.response.send_message.call_args[0][0]


async def test_a_member_without_the_judge_role_is_turned_away():
    interaction = _voter(42, is_judge=False)
    await _vote(interaction)
    interaction.response.edit_message.assert_not_called()


async def test_losing_the_verdict_race_does_not_resurrect_the_buttons():
    # Two judges cast the deciding ballot at once; only one claim wins. The loser must not
    # fall through and redraw the ruled card with live vote buttons on it.
    interaction = _voter(42)
    await _vote(interaction, claim_verdict=AsyncMock(return_value=False))
    interaction.response.edit_message.assert_not_called()
    assert "verdict" in interaction.response.send_message.call_args[0][0].lower()


# --- The card's phase machine -------------------------------------------------------


def _trial(plea=None, verdict=None):
    return {"id": 1, "plea": plea, "verdict": verdict}


def test_before_the_plea_the_bench_has_no_buttons_to_press():
    # The plea is a hard gate: no vote exists to click until the accused has spoken.
    view = build_trial_view(_trial(), 0, 0)
    assert isinstance(view, PleaView)


def test_once_pleaded_the_card_hands_over_to_the_jury():
    view = build_trial_view(_trial(plea="je le regrette"), 0, 0)
    assert isinstance(view, VerdictView)


def test_a_ruled_trial_keeps_no_clickable_buttons():
    # A card that still looks clickable after the verdict throws "This interaction failed".
    assert build_trial_view(_trial(plea="...", verdict="guilty"), 3, 1) is None
    assert build_trial_view(_trial(plea="...", verdict="acquitted"), 1, 3) is None
    assert build_trial_view(_trial(verdict="expired"), 0, 0) is None
