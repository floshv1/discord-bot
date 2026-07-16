import datetime

from bot.cogs.betting import service
from bot.cogs.betting.embeds import BOARD_FIXTURE_COUNT, build_board_embed


def _market(home, away, in_hours=1):
    return {
        "home_name": home,
        "away_name": away,
        "start_time": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=in_hours),
    }


def _text(embed) -> str:
    return " ".join([embed.title or "", embed.description or ""] + [f.value for f in embed.fields])


def test_the_board_names_what_the_channel_follows():
    # A channel has to say what it is for before any fixture lands in it.
    text = _text(build_board_embed(service.CATEGORY_INTER, []))

    assert "World Championship" in text
    assert "Mid-Season Invitational" in text
    assert "LEC" not in text  # that's another channel's board


def test_the_foot_board_names_every_football_competition():
    text = _text(build_board_embed(service.CATEGORY_FOOT, []))

    assert "Coupe du Monde" in text
    assert "Ligue des Champions" in text
    assert "Ligue 1" in text


def test_an_empty_board_says_so_rather_than_looking_broken():
    text = _text(build_board_embed(service.CATEGORY_LCK, []))

    assert "Rien de prévu" in text


def test_the_board_lists_upcoming_matches_with_a_reader_local_timestamp():
    # A formatted date would be wrong for everyone outside the poster's timezone.
    embed = build_board_embed(service.CATEGORY_LEC, [_market("G2", "Fnatic")])
    text = _text(embed)

    assert "G2" in text and "Fnatic" in text
    assert "<t:" in text and ":R>" in text


def test_the_board_does_not_grow_without_bound():
    # A 7-day football lookahead is a lot of fixtures, and an embed field caps at 1024 chars.
    markets = [_market(f"H{i}", f"A{i}", in_hours=i + 1) for i in range(30)]
    text = _text(build_board_embed(service.CATEGORY_FOOT, markets))

    assert text.count("•") == BOARD_FIXTURE_COUNT


def test_the_custom_board_explains_itself_and_lists_no_competitions():
    # Community bets have no fixtures to announce and no leagues to follow — `competition`
    # holds the question text, which would be nonsense as a "followed competition" list.
    text = _text(build_board_embed(service.CATEGORY_CUSTOM, []))

    assert "/bet create" in text
    assert "Compétitions suivies" not in text
