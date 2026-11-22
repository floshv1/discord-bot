import datetime as dt
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from bot.cogs.loldle import rules
from bot.cogs.loldle.catalog import Champion
from bot.cogs.loldle.rules import DayInfo

ANSWER = Champion(
    id="A",
    name="A",
    positions=("Mid", "Top"),
    classes=("Assassin", "Mage"),
    range="Distance",
    resource="Mana",
    region="Ionia",
    year=2015,
    spells=tuple("pqwer"),
    splash="s",
    gender="Femme",
    damage="AP",
    species=("Humain", "Néant"),
)
D = dt.date(2026, 11, 22)


def ago(n):
    return D - dt.timedelta(days=n)


def full(*offsets):
    return [DayInfo(ago(n), 1440) for n in offsets]


def test_paris_day_changes_at_paris_midnight_not_utc():
    assert rules.paris_day(dt.datetime(2026, 11, 22, 22, 59, tzinfo=dt.UTC)) == D
    assert rules.paris_day(dt.datetime(2026, 11, 22, 23, 1, tzinfo=dt.UTC)) == D + dt.timedelta(days=1)
    assert rules.paris_day(dt.datetime(2026, 11, 22, 0, 30, tzinfo=ZoneInfo("Europe/Paris"))) == D


def test_identical_champion_is_all_green():
    assert rules.compare(ANSWER, ANSWER) == ["green"] * 9


@pytest.mark.parametrize(
    ("positions", "expected"), [(("Mid",), "yellow"), (("Top", "Mid"), "green"), (("ADC",), "red")]
)
def test_position_set_comparison(positions, expected):
    assert rules.compare(replace(ANSWER, id="B", positions=positions), ANSWER)[0] == expected


@pytest.mark.parametrize(
    ("classes", "expected"), [(("Mage", "Tank"), "yellow"), (("Mage", "Assassin"), "green"), (("Tank",), "red")]
)
def test_class_set_comparison(classes, expected):
    assert rules.compare(replace(ANSWER, id="B", classes=classes), ANSWER)[1] == expected


def test_single_value_clues_have_no_partial():
    cells = rules.compare(replace(ANSWER, id="B", range="Mêlée", resource="Autre", region="Noxus"), ANSWER)
    assert cells[2:5] == ["red", "red", "red"]


def test_year_arrow_points_at_the_answer():
    assert rules.compare(replace(ANSWER, id="B", year=2012), ANSWER)[-1] == "up"
    assert rules.compare(replace(ANSWER, id="B", year=2020), ANSWER)[-1] == "down"


@pytest.mark.parametrize(
    ("guess", "answer", "expected"),
    [
        ("Ionia", "Ionia", "green"),
        ("Ionia/Noxus", "Noxus/Ionia", "green"),
        ("Ionia/Noxus", "Ionia", "yellow"),
        ("Zaun", "Ionia", "red"),
    ],
)
def test_region_handles_several_regions(guess, answer, expected):
    cells = rules.compare(replace(ANSWER, id="B", region=guess), replace(ANSWER, region=answer))
    assert cells[4] == expected


def test_gender_is_exact():
    assert rules.compare(replace(ANSWER, id="B"), ANSWER)[5] == "green"
    assert rules.compare(replace(ANSWER, id="B", gender="Homme"), ANSWER)[5] == "red"


@pytest.mark.parametrize(
    ("guess", "answer", "expected"),
    [("AP", "AP", "green"), ("AD", "AP", "red"), ("Hybride", "AP", "yellow"), ("AD", "Hybride", "yellow")],
)
def test_damage_hybrid_is_partial(guess, answer, expected):
    cells = rules.compare(replace(ANSWER, id="B", damage=guess), replace(ANSWER, damage=answer))
    assert cells[6] == expected


@pytest.mark.parametrize(
    ("species", "expected"), [(("Néant", "Humain"), "green"), (("Humain",), "yellow"), (("Yordle",), "red")]
)
def test_species_set_comparison(species, expected):
    assert rules.compare(replace(ANSWER, id="B", species=species), ANSWER)[7] == expected


def test_clues_follow_the_cell_order():
    assert rules.clues(ANSWER) == [
        "Mid/Top",
        "Assassin/Mage",
        "Distance",
        "Mana",
        "Ionia",
        "Femme",
        "AP",
        "Humain/Néant",
        "2015",
    ]


def test_answer_without_the_new_clues_gets_a_neutral_cell_not_a_false_red():
    old = replace(ANSWER, gender="", damage="", species=())
    cells = rules.compare(replace(ANSWER, id="B"), old)
    assert cells[5:8] == ["none", "none", "none"]
    assert rules.grid_row(cells[5:8]) == "⬛⬛⬛"


def test_grid_row():
    assert rules.grid_row(["green", "yellow", "red", "green", "red", "up"]) == "🟩🟨🟥🟩🟥⬆️"


def test_hint_prices_and_order():
    assert rules.HINTS == {"spell": 25, "splash": 50, "letter": 100}
    assert rules.next_hint(set()) == "spell"
    assert rules.next_hint({"spell"}) == "splash"
    assert rules.next_hint({"spell", "splash", "letter"}) is None


def test_hints_unlock_after_three_guesses_and_in_order():
    assert not rules.can_buy("spell", set(), 2)
    assert rules.can_buy("spell", set(), 3)
    assert not rules.can_buy("splash", set(), 9)
    assert rules.can_buy("splash", {"spell"}, 3)
    assert not rules.can_buy("spell", {"spell"}, 3)


def test_score_adds_one_per_hint():
    assert rules.score(3, 2) == 5


def test_streak_counts_consecutive_wins():
    assert rules.streak(full(0, 1, 2), {ago(0), ago(1), ago(2)}, D) == 3


def test_streak_survives_not_having_played_yet_today():
    assert rules.streak(full(0, 1, 2), {ago(1), ago(2)}, D) == 2


def test_missed_normal_day_breaks_the_streak():
    assert rules.streak(full(0, 1, 2, 3), {ago(0), ago(2), ago(3)}, D) == 1


def test_a_normal_day_nobody_played_still_breaks_it():
    # The row exists because the bot was up, even with zero guesses.
    assert rules.streak(full(0, 1, 2), {ago(0), ago(2)}, D) == 1


def test_day_with_no_row_is_excused():
    assert rules.streak(full(0, 2), {ago(0), ago(2)}, D) == 2


def test_partial_outage_is_excused():
    days = [DayInfo(ago(0), 1440), DayInfo(ago(1), 1199), DayInfo(ago(2), 1440)]
    assert rules.streak(days, {ago(0), ago(2)}, D) == 2


def test_twenty_hours_up_is_a_normal_day():
    days = [DayInfo(ago(0), 1440), DayInfo(ago(1), 1200), DayInfo(ago(2), 1440)]
    assert rules.streak(days, {ago(0), ago(2)}, D) == 1


def test_win_on_an_excused_day_extends_the_streak():
    days = [DayInfo(ago(0), 1440), DayInfo(ago(1), 300), DayInfo(ago(2), 1440)]
    assert rules.streak(days, {ago(0), ago(1), ago(2)}, D) == 3


def test_no_win_ever_is_zero():
    assert rules.streak(full(0, 1), set(), D) == 0
    assert rules.streak([], set(), D) == 0


def test_derived_hints_are_stable_and_in_bounds():
    assert rules.spell_index(1, D, 5) == rules.spell_index(1, D, 5)
    assert 0 <= rules.spell_index(1, D, 5) < 5
    box = rules.crop_box(1, D, 1215, 717)
    assert box == rules.crop_box(1, D, 1215, 717)
    left, top, right, bottom = box
    assert 0 <= left < right <= 1215 and 0 <= top < bottom <= 717
    assert right - left == bottom - top
