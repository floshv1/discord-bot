import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from bot.cogs.customs import rules

PARIS = ZoneInfo("Europe/Paris")
NOW = dt.datetime(2026, 11, 22, 18, 0, tzinfo=PARIS)


@pytest.mark.parametrize(
    ("text", "hour", "minute"),
    [("21h", 21, 0), ("21h30", 21, 30), ("21:30", 21, 30), ("21", 21, 0), (" 9H05 ", 9, 5)],
)
def test_parse_time_formats(text, hour, minute):
    t = rules.parse_time(text, NOW)
    assert (t.hour, t.minute) == (hour, minute) and t.tzinfo is not None


def test_parse_time_today_when_still_ahead():
    assert rules.parse_time("21h", NOW).date() == NOW.date()


def test_parse_time_tomorrow_when_already_past():
    assert rules.parse_time("17h", NOW).date() == NOW.date() + dt.timedelta(days=1)


@pytest.mark.parametrize("text", ["25h", "21h75", "ce soir", "", "21h30m"])
def test_parse_time_rejects_garbage(text):
    with pytest.raises(ValueError):
        rules.parse_time(text, NOW)


def test_parse_slots_dedupes_and_sorts():
    slots = rules.parse_slots("22h, 20h 21h 20h", NOW)
    assert [s.hour for s in slots] == [20, 21, 22]


@pytest.mark.parametrize("text", ["21h", "18h30 19h 20h 21h 22h 23h"])
def test_parse_slots_needs_two_to_five(text):
    with pytest.raises(ValueError):
        rules.parse_slots(text, NOW)


def test_split_roster_first_ten_play_rest_wait():
    signups = [{"id": i, "person_id": i} for i in range(12)]
    players, waiting = rules.split_roster(signups)
    assert [s["id"] for s in players] == list(range(10))
    assert [s["id"] for s in waiting] == [10, 11]


def test_vote_winner_needs_ten_and_prefers_earliest_on_tie():
    a, b = NOW, NOW + dt.timedelta(hours=1)
    assert rules.vote_winner({a: 9, b: 9}) is None
    assert rules.vote_winner({b: 10, a: 12}) == a
    assert rules.vote_winner({a: 3, b: 10}) == b


def test_expires_at_six_hours_after_start_else_a_day_after_creation():
    assert rules.expires_at(NOW, NOW - dt.timedelta(days=2)) == NOW + dt.timedelta(hours=6)
    assert rules.expires_at(None, NOW) == NOW + dt.timedelta(hours=24)


def test_evening_score_counts_decided_games_only():
    games = [{"winner": "blue"}, {"winner": "red"}, {"winner": "blue"}, {"winner": None}]
    assert rules.evening_score(games) == (2, 1)


def test_swap_within_a_team_exchanges_lanes():
    from bot.cogs.customs.lolamis import Slot, Teams

    t = Teams(tuple(Slot(i, i) for i in range(5)), tuple(Slot(5 + i, i) for i in range(5)), 0.5)
    s = rules.swap(t, 0, 2)
    assert Slot(0, 2) in s.blue and Slot(2, 0) in s.blue and s.red == t.red


def test_swap_across_teams_exchanges_side_and_lane():
    from bot.cogs.customs.lolamis import Slot, Teams

    t = Teams(tuple(Slot(i, i) for i in range(5)), tuple(Slot(5 + i, i) for i in range(5)), 0.5)
    s = rules.swap(t, 1, 8)  # blue jungler <-> red ADC
    assert Slot(8, 1) in s.blue and Slot(1, 3) in s.red
    assert sorted(x.lane for x in s.blue) == [0, 1, 2, 3, 4] == sorted(x.lane for x in s.red)
