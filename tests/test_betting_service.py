from bot.cogs.betting.service import (
    HOUSE_SEED_PER_OUTCOME,
    house_stakes,
    implied_odds,
    outcomes_for_market,
    player_bets,
    pool_shares,
    pool_totals,
    settle_parimutuel,
)
from bot.cogs.currency.service import HOUSE_USER_ID


def _bet(id, outcome, amount):
    return {"id": id, "outcome": outcome, "amount": amount}


def _seed(*outcomes, amount=HOUSE_SEED_PER_OUTCOME):
    """The house's opening line, as seed_market writes it: one row per outcome."""
    return [{"id": -i, "user_id": HOUSE_USER_ID, "outcome": key, "amount": amount} for i, key in enumerate(outcomes, 1)]


def test_settle_parimutuel_splits_losing_pool_proportionally():
    bets = [
        _bet(1, "home", 100),
        _bet(2, "home", 300),
        _bet(3, "away", 400),
    ]
    payouts = settle_parimutuel(bets, "home")

    # winning_pool=400, losing_pool=400 -> each winner doubles their stake
    assert payouts == {1: 200, 2: 600}


def test_settle_parimutuel_uneven_split():
    bets = [
        _bet(1, "home", 100),
        _bet(2, "away", 100),
        _bet(3, "away", 200),
    ]
    payouts = settle_parimutuel(bets, "home")

    # winning_pool=100, losing_pool=300 -> winner gets stake back plus entire losing pool
    assert payouts == {1: 400}


def test_settle_parimutuel_no_winning_bets_returns_empty():
    bets = [_bet(1, "away", 100), _bet(2, "away", 200)]
    payouts = settle_parimutuel(bets, "home")
    assert payouts == {}


def test_settle_parimutuel_empty_bets():
    assert settle_parimutuel([], "home") == {}


def test_settle_parimutuel_only_pays_winning_outcome():
    bets = [_bet(1, "home", 100), _bet(2, "draw", 100), _bet(3, "away", 100)]
    payouts = settle_parimutuel(bets, "draw")
    assert set(payouts.keys()) == {2}


def test_pool_totals_aggregates_by_outcome():
    bets = [_bet(1, "home", 100), _bet(2, "home", 50), _bet(3, "away", 75)]
    totals = pool_totals(bets)
    assert totals["home"]["total"] == 150
    assert totals["home"]["count"] == 2
    assert totals["away"]["total"] == 75


def test_pool_totals_counts_distinct_backers_not_bets():
    # One person betting twice on France is one backer, not two — Twitch counts predictors.
    bets = [
        {"id": 1, "outcome": "home", "amount": 100, "user_id": 7},
        {"id": 2, "outcome": "home", "amount": 50, "user_id": 7},
        {"id": 3, "outcome": "home", "amount": 25, "user_id": 8},
    ]
    totals = pool_totals(bets)
    assert totals["home"]["count"] == 3
    assert totals["home"]["backers"] == 2


def test_pool_shares_split_the_pool():
    bets = [_bet(1, "home", 750), _bet(2, "away", 250)]
    shares = pool_shares(bets)
    assert shares["home"] == 0.75
    assert shares["away"] == 0.25


def test_pool_shares_empty():
    assert pool_shares([]) == {}


def test_pool_totals_empty():
    assert pool_totals([]) == {}


def test_implied_odds_are_total_over_outcome_pool():
    bets = [_bet(1, "home", 100), _bet(2, "away", 300)]
    # Pool is 400: home pays 400/100 = 4.0x, away pays 400/300 = 1.33x.
    odds = implied_odds(bets)
    assert odds["home"] == 4.0
    assert round(odds["away"], 2) == 1.33


def test_implied_odds_omit_outcomes_with_no_backers():
    odds = implied_odds([_bet(1, "home", 100)])
    assert set(odds) == {"home"}
    assert odds["home"] == 1.0  # only backer: you just get your own stake back


def test_implied_odds_empty():
    assert implied_odds([]) == {}


def test_displayed_odds_match_what_settlement_actually_pays():
    # The cote on the card must be the multiplier settle_parimutuel really applies,
    # otherwise the bot advertises one payout and pays another.
    bets = [_bet(1, "home", 100), _bet(2, "away", 300)]
    odds = implied_odds(bets)
    payouts = settle_parimutuel(bets, "home")
    assert payouts[1] == int(100 * odds["home"])


MSI_STAKE = 100


def test_the_lone_bettor_actually_wins_something():
    """The bug that started this, in its original numbers.

    One member put 100 🪙 on Hanwha in the MSI market and nobody backed Bilibili. The cote read
    1.00x: winning returned their own stake and not one coin more. Betting alone was pointless.
    The house is now on the other side, so there is something to win.
    """
    bets = [*_seed("home", "away"), {"id": 1, "user_id": 7, "outcome": "away", "amount": MSI_STAKE}]

    payouts = settle_parimutuel(bets, "away")

    # Pool 600, away pool 350 -> 1.71x. Not the 100 🪙 they'd have got back before.
    assert payouts[1] == 171
    assert payouts[1] > MSI_STAKE


def test_a_seeded_market_never_offers_1x():
    bets = [*_seed("home", "away"), {"id": 1, "user_id": 7, "outcome": "away", "amount": MSI_STAKE}]
    odds = implied_odds(bets)

    assert odds["away"] > 1.0
    # And the unbacked side is priced too, rather than showing "—" until someone commits.
    assert odds["home"] > 1.0


def test_a_seeded_market_opens_at_even_odds():
    # Nobody has bet yet: both sides pay 2.00x, which is the line members expect to see.
    odds = implied_odds(_seed("home", "away"))
    assert odds == {"home": 2.0, "away": 2.0}


def test_the_house_pays_the_lone_winner_out_of_its_own_seed():
    # Zero-sum: every coin paid out came from a coin someone staked. Nothing is minted.
    bets = [*_seed("home", "away"), {"id": 1, "user_id": 7, "outcome": "away", "amount": MSI_STAKE}]
    payouts = settle_parimutuel(bets, "away")

    staked = sum(b["amount"] for b in bets)
    paid = sum(payouts.values())
    assert paid <= staked  # the residual (dust) is what the house keeps


def test_the_house_takes_the_pool_when_every_member_is_wrong():
    bets = [*_seed("home", "away"), {"id": 1, "user_id": 7, "outcome": "away", "amount": 400}]
    payouts = settle_parimutuel(bets, "home")

    # Only the house backed home, so it collects: its own 250 back plus the 650 that lost.
    house_bet = next(b for b in bets if b["outcome"] == "home")
    assert payouts == {house_bet["id"]: 900}


def test_the_card_counts_people_not_the_house():
    # A bar half-filled because the bank is on that side would tell the reader nothing.
    bets = [*_seed("home", "away"), {"id": 1, "user_id": 7, "outcome": "away", "amount": MSI_STAKE}]
    totals = pool_totals(player_bets(bets))

    assert "home" not in totals  # nobody actually backed Bilibili
    assert totals["away"]["backers"] == 1
    assert totals["away"]["total"] == MSI_STAKE


def test_house_stakes_report_the_line():
    assert house_stakes([*_seed("home", "away"), _bet(1, "home", 50)]) == {
        "home": HOUSE_SEED_PER_OUTCOME,
        "away": HOUSE_SEED_PER_OUTCOME,
    }


def test_player_bets_keeps_everyone_who_is_not_the_house():
    bets = [*_seed("home", "away"), {"id": 1, "user_id": 7, "outcome": "away", "amount": 10}]
    assert [b["id"] for b in player_bets(bets)] == [1]


def test_outcomes_for_market_includes_draw_for_football():
    market = {"sport": "football", "home_name": "France", "away_name": "Argentina"}
    assert outcomes_for_market(market) == [
        ("home", "France"),
        ("draw", "Draw"),
        ("away", "Argentina"),
    ]


def test_outcomes_for_market_excludes_draw_for_lol():
    market = {"sport": "lol", "home_name": "G2", "away_name": "Fnatic"}
    assert outcomes_for_market(market) == [
        ("home", "G2"),
        ("away", "Fnatic"),
    ]


def test_outcomes_for_custom_market_uses_user_labels():
    market = {"sport": "custom", "home_name": "Team Blue", "away_name": "Team Red"}
    assert outcomes_for_market(market) == [
        ("home", "Team Blue"),
        ("away", "Team Red"),
    ]
