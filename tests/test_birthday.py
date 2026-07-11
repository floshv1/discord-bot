from datetime import date

from bot.cogs.birthday.cog import _next_occurrence


def test_next_occurrence_later_this_year():
    assert _next_occurrence(18, 6, date(2026, 1, 15)) == date(2026, 6, 18)


def test_next_occurrence_today():
    assert _next_occurrence(15, 1, date(2026, 1, 15)) == date(2026, 1, 15)


def test_next_occurrence_already_passed_rolls_to_next_year():
    assert _next_occurrence(1, 1, date(2026, 6, 18)) == date(2027, 1, 1)


def test_leap_day_falls_back_to_feb_28_in_a_non_leap_year():
    # 2026 is not a leap year — the birthday is celebrated on the 28th, not skipped.
    assert _next_occurrence(29, 2, date(2026, 1, 15)) == date(2026, 2, 28)


def test_leap_day_stays_on_feb_29_in_a_leap_year():
    assert _next_occurrence(29, 2, date(2028, 1, 15)) == date(2028, 2, 29)


def test_leap_day_after_february_rolls_into_next_year():
    # Next year (2027) is also not a leap year — must not raise, must land on the 28th.
    assert _next_occurrence(29, 2, date(2026, 6, 18)) == date(2027, 2, 28)


def test_leap_day_rolls_into_a_leap_year():
    assert _next_occurrence(29, 2, date(2027, 6, 18)) == date(2028, 2, 29)
