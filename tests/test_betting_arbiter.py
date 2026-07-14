"""Rule A: you do not referee a bet you have money on.

This is what leaves the creator free to choose without handing them the power to rob the
others: they bet (and the gavel passes to the mods), or they referee (and they stay out).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.betting.service import creator_holds_gavel, may_settle
from bot.cogs.currency.service import HOUSE_USER_ID

CREATOR = 10
MEMBER = 20
MOD = 30


def _custom(**kw):
    return {"provider": "custom", "creator_user_id": CREATOR, **kw}


def _match(**kw):
    return {"provider": "football-data", "creator_user_id": None, **kw}


def _bet(user_id, amount=100, outcome="home"):
    return {"id": user_id, "user_id": user_id, "outcome": outcome, "amount": amount}


HOUSE_SEED = [
    {"id": -1, "user_id": HOUSE_USER_ID, "outcome": "home", "amount": 100},
    {"id": -2, "user_id": HOUSE_USER_ID, "outcome": "away", "amount": 100},
]


def test_a_creator_who_has_not_bet_keeps_the_gavel():
    assert creator_holds_gavel(_custom(), HOUSE_SEED) is True


def test_the_house_seed_does_not_take_the_gavel_off_the_creator():
    # The house is staked on every market ever opened. If its seed counted, no creator would
    # ever hold the gavel and every community bet would need a mod.
    assert creator_holds_gavel(_custom(), [*HOUSE_SEED, _bet(MEMBER)]) is True


def test_a_creator_who_bets_hands_the_gavel_to_the_mods():
    assert creator_holds_gavel(_custom(), [*HOUSE_SEED, _bet(CREATOR)]) is False


def test_a_provider_match_has_no_creator_so_the_mods_hold_the_gavel():
    assert creator_holds_gavel(_match(), HOUSE_SEED) is False


def test_a_neutral_creator_may_settle_their_own_bet():
    assert may_settle(_custom(), HOUSE_SEED, CREATOR, is_mod=False) is True


def test_a_creator_who_bet_may_not_settle_their_own_bet():
    # The exploit in one line: bet on A, declare A the winner, take everyone's stakes.
    bets = [*HOUSE_SEED, _bet(CREATOR)]
    assert may_settle(_custom(), bets, CREATOR, is_mod=False) is False


def test_a_mod_who_bet_may_not_settle_a_custom_bet_either():
    # A custom bet's truth is not public — being a mod does not make your call checkable.
    bets = [*HOUSE_SEED, _bet(MOD)]
    assert may_settle(_custom(), bets, MOD, is_mod=True) is False


def test_a_mod_who_did_not_bet_may_settle_a_custom_bet():
    bets = [*HOUSE_SEED, _bet(CREATOR)]
    assert may_settle(_custom(), bets, MOD, is_mod=True) is True


def test_a_random_member_may_never_settle_a_custom_bet():
    assert may_settle(_custom(), HOUSE_SEED, MEMBER, is_mod=False) is False


def test_a_mod_may_settle_a_provider_match_they_bet_on():
    # The escape hatch for a match the API never reported. The score is public, so a mod who
    # lied about it would be caught in five seconds — unlike a custom bet.
    bets = [*HOUSE_SEED, _bet(MOD)]
    assert may_settle(_match(), bets, MOD, is_mod=True) is True


def test_a_member_may_never_settle_a_provider_match():
    assert may_settle(_match(), HOUSE_SEED, MEMBER, is_mod=False) is False


@pytest.mark.asyncio
async def test_the_settleable_list_excludes_markets_you_bet_on_in_sql():
    """The autocomplete must not offer a bet you can't settle — and it must do the exclusion in
    one query, not one query per market: it re-runs on every keystroke."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.list_settleable_markets(guild_id=1, user_id=MEMBER, is_mod=False)

    sql, *args = pool.fetch.call_args[0]
    assert "NOT EXISTS" in sql, "a market you staked on must be filtered out in SQL"
    assert args == [1, MEMBER, False]
