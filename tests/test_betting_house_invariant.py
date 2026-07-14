"""Rule B: opening a custom bet can never make anyone money.

The maximum a player can extract from the house on a custom market is strictly less than the
seed of the losing side. If that seed does not exceed the creation fee, the `create -> stake ->
resolve` loop is structurally unprofitable—solo and in collusion.

This is the invariant that replaces the exploit. It must stay true whatever is changed afterwards.
"""

from itertools import product

import pytest

from bot.cogs.betting.service import (
    CREATE_FEE,
    CUSTOM_SEED_PER_OUTCOME,
    house_pnl,
    settle_parimutuel,
)
from bot.cogs.currency.service import HOUSE_USER_ID

AMOUNTS = [0, 1, 10, 100, 250, 1_000, 10_000, 1_000_000]


def _settled(stake_home: int, stake_away: int, winner: str) -> list[dict]:
    """A seeded binary custom market, settled. Returns the bets with their payouts written."""
    bets = [
        {"id": 1, "user_id": HOUSE_USER_ID, "outcome": "home", "amount": CUSTOM_SEED_PER_OUTCOME},
        {"id": 2, "user_id": HOUSE_USER_ID, "outcome": "away", "amount": CUSTOM_SEED_PER_OUTCOME},
    ]
    if stake_home:
        bets.append({"id": 3, "user_id": 7, "outcome": "home", "amount": stake_home})
    if stake_away:
        bets.append({"id": 4, "user_id": 8, "outcome": "away", "amount": stake_away})

    payouts = settle_parimutuel(bets, winner)
    return [{**b, "payout": payouts.get(b["id"], 0)} for b in bets]


def test_the_custom_seed_never_exceeds_the_create_fee():
    # The whole invariant rests on this one comparison.
    assert CUSTOM_SEED_PER_OUTCOME <= CREATE_FEE


@pytest.mark.parametrize("winner", ["home", "away"])
def test_opening_a_custom_bet_can_never_turn_a_profit(winner):
    """Whatever the stakes, and whoever the arbiter hands the win to, the house loses less on
    the market than the creator paid to open it. So `create -> stake -> resolve` nets < 0."""
    for stake_home, stake_away in product(AMOUNTS, repeat=2):
        loss = -house_pnl(_settled(stake_home, stake_away, winner))
        assert loss <= CREATE_FEE, f"house lost {loss} 🪙 with home={stake_home} away={stake_away}"


def test_a_lone_bettor_backing_the_declared_winner_is_the_worst_case():
    # This is the exploit that was reported: back one side alone, declare it the winner.
    settled = _settled(stake_home=10_000, stake_away=0, winner="home")
    player = next(b for b in settled if b["user_id"] == 7)
    profit = player["payout"] - player["amount"]
    assert profit < CUSTOM_SEED_PER_OUTCOME  # can't even take the whole losing seed
    assert profit - CREATE_FEE < 0  # and the fee already cost more than that
