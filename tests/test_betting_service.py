from bot.cogs.betting.service import outcomes_for_market, pool_totals, settle_parimutuel


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
    assert totals == {
        "home": {"total": 150, "count": 2},
        "away": {"total": 75, "count": 1},
    }


def test_pool_totals_empty():
    assert pool_totals([]) == {}


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
