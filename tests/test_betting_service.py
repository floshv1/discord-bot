from bot.cogs.betting.service import (
    implied_odds,
    outcomes_for_market,
    pool_shares,
    pool_totals,
    settle_parimutuel,
)


def _bet(id, outcome, amount):
    return {"id": id, "outcome": outcome, "amount": amount}


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
