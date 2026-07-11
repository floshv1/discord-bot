import datetime

import discord

from bot.cogs.betting.embeds import build_market_embed


def _market(status="open", sport="football", winner=None):
    return {
        "sport": sport,
        "competition": "World Cup",
        "home_name": "France",
        "away_name": "Argentina",
        "start_time": datetime.datetime(2026, 7, 15, 18, 0, tzinfo=datetime.UTC),
        "status": status,
        "winner": winner,
    }


def _bet(outcome, amount):
    return {"outcome": outcome, "amount": amount}


def test_open_market_shows_pool_and_blurple_color():
    embed = build_market_embed(_market(status="open"), [_bet("home", 100), _bet("away", 50)])
    assert embed.color == discord.Color.blurple()
    assert "France" in embed.title or "France" in embed.description
    assert "odds shift" in embed.footer.text.lower()
    assert "One option each" in embed.footer.text


def test_open_market_shows_live_cotes():
    # Pool is 150; France holds 100 -> 1.50x, Argentina holds 50 -> 3.00x.
    embed = build_market_embed(_market(status="open"), [_bet("home", 100), _bet("away", 50)])
    field = embed.fields[0].value
    assert "1.50x" in field
    assert "3.00x" in field


def test_outcome_with_no_money_on_it_has_no_cote():
    embed = build_market_embed(_market(status="open"), [_bet("home", 100)])
    field = embed.fields[0].value
    # Draw and Argentina have no backers — their odds are undefined, not infinite.
    assert field.count("—") == 2


def test_market_with_no_bets_shows_no_cotes():
    embed = build_market_embed(_market(status="open"), [])
    field = embed.fields[0].value
    assert "x" not in field.replace("bet(s)", "")
    assert field.count("—") == 3


def test_resolved_market_ticks_the_winning_outcome():
    embed = build_market_embed(
        _market(status="resolved", winner="home"),
        [_bet("home", 100), _bet("away", 100)],
    )
    home_line = embed.fields[0].value.splitlines()[0]
    assert "✅" in home_line
    assert "2.00x" in home_line  # 200 total / 100 on France


def test_locked_market_is_greyed_out():
    embed = build_market_embed(_market(status="locked"), [_bet("home", 100)])
    assert embed.color == discord.Color.greyple()
    assert "🔒" in embed.title


def test_resolved_market_is_green_with_multiplier():
    embed = build_market_embed(
        _market(status="resolved", winner="home"),
        [_bet("home", 100), _bet("away", 100)],
    )
    assert embed.color == discord.Color.green()
    assert "France" in embed.title
    assert "x stake" in embed.footer.text


def test_resolved_market_with_no_winning_bets():
    embed = build_market_embed(
        _market(status="resolved", winner="home"),
        [_bet("away", 100)],
    )
    assert "no payouts" in embed.footer.text


def test_void_market_is_red():
    embed = build_market_embed(_market(status="void"), [_bet("home", 100)])
    assert embed.color == discord.Color.red()
    assert "refunded" in embed.footer.text


def test_lol_market_omits_draw_outcome():
    market = _market(sport="lol")
    market["home_name"] = "G2"
    market["away_name"] = "Fnatic"
    embed = build_market_embed(market, [_bet("home", 100)])
    pool_field = embed.fields[0].value
    assert "Draw" not in pool_field


def _custom_market(status="open", winner=None):
    return {
        "sport": "custom",
        "competition": "Who wins tonight's scrim?",
        "home_name": "Team Blue",
        "away_name": "Team Red",
        "start_time": datetime.datetime(2026, 7, 15, 18, 0, tzinfo=datetime.UTC),
        "status": status,
        "winner": winner,
        "creator_user_id": 4242,
    }


def test_custom_market_shows_question_and_user_options():
    embed = build_market_embed(_custom_market(), [_bet("home", 100)])
    assert "Who wins tonight's scrim?" in embed.description
    pool_field = embed.fields[0].value
    assert "Team Blue" in pool_field
    assert "Team Red" in pool_field
    assert "Draw" not in pool_field


def test_custom_market_credits_its_creator():
    embed = build_market_embed(_custom_market(), [])
    assert any("4242" in f.value for f in embed.fields)


def test_custom_market_resolved_names_winning_option():
    embed = build_market_embed(
        _custom_market(status="resolved", winner="away"),
        [_bet("home", 100), _bet("away", 100)],
    )
    assert "Team Red" in embed.title
    assert embed.color == discord.Color.green()
