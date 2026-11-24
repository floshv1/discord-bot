"""LoLdle rules that need no Discord and no database, so they can be tested by value."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from bot.cogs.loldle.catalog import Champion

PARIS = ZoneInfo("Europe/Paris")
NO_REPEAT_DAYS = 60
HINT_UNLOCK = 3
HINTS: dict[str, int] = {"spell": 25, "splash": 50, "letter": 100}  # in purchase order
FULL_DAY_MINUTES = 1200  # below this the bot was down long enough to excuse the day
BOARD_DAYS = 30
BOARD_MIN_SOLVED = 5
PING_FROM_HOUR, PING_UNTIL_HOUR = 9, 21  # a late catch-up must never ping at night
MAX_PING_USERS = 75  # a mention is ~23 characters and a message holds 2000

EMOJI = {"green": "🟩", "yellow": "🟨", "red": "🟥", "up": "⬆️", "down": "⬇️", "none": "⬛"}
COLUMN_COUNT = 9


@dataclass(frozen=True)
class DayInfo:
    day: dt.date
    up_minutes: int


def paris_day(now: dt.datetime) -> dt.date:
    return now.astimezone(PARIS).date()


def in_ping_window(now: dt.datetime) -> bool:
    return PING_FROM_HOUR <= now.astimezone(PARIS).hour < PING_UNTIL_HOUR


def _sets(a: tuple[str, ...], b: tuple[str, ...]) -> str:
    x, y = set(a), set(b)
    return "green" if x == y else "yellow" if x & y else "red"


def _damage(guess: str, answer: str) -> str:
    if guess == answer:
        return "green"
    return "yellow" if "Hybride" in (guess, answer) else "red"


def compare(guess: Champion, answer: Champion) -> list[str]:
    """Nine cells: position, class, range, resource, region, gender, damage, species, year.

    The year's arrow points at the answer. `none` is a clue the stored answer does not have (a day
    started before the column existed and whose champion left the catalog): never a false red.
    """
    if guess.id == answer.id:
        return ["green"] * COLUMN_COUNT
    year = "green" if guess.year == answer.year else "up" if answer.year > guess.year else "down"
    return [
        _sets(guess.positions, answer.positions),
        _sets(guess.classes, answer.classes),
        "green" if guess.range == answer.range else "red",
        "green" if guess.resource == answer.resource else "red",
        _sets(tuple(guess.region.split("/")), tuple(answer.region.split("/"))),  # some have several
        ("green" if guess.gender == answer.gender else "red") if answer.gender else "none",
        _damage(guess.damage, answer.damage) if answer.damage else "none",
        _sets(guess.species, answer.species) if answer.species else "none",
        year,
    ]


def clues(champion: Champion) -> list[str]:
    """The nine values as displayed, stored with each guess so a data refresh can't rewrite it."""
    return [
        "/".join(champion.positions),
        "/".join(champion.classes),
        champion.range,
        champion.resource,
        champion.region,
        champion.gender or "?",
        champion.damage or "?",
        "/".join(champion.species) or "?",
        str(champion.year),
    ]


def grid_row(cells: list[str]) -> str:
    return "".join(EMOJI[c] for c in cells)


def next_hint(owned: set[str]) -> str | None:
    return next((kind for kind in HINTS if kind not in owned), None)


def can_buy(kind: str, owned: set[str], guesses: int) -> bool:
    return guesses >= HINT_UNLOCK and kind == next_hint(owned)


def score(tries: int, hints: int) -> int:
    return tries + hints


def streak(days: list[DayInfo], solved: set[dt.date], today: dt.date) -> int:
    """Consecutive wins, walking back from today.

    Today can only add (it isn't over). On a past day a win counts; no win breaks the streak,
    unless the bot was down: no row, or too few up-minutes, excuses the day.
    """
    known = {d.day: d.up_minutes for d in days}
    seen = set(known) | solved
    if not seen:
        return 0
    earliest = min(seen)
    count = 1 if today in solved else 0
    day = today - dt.timedelta(days=1)
    while day >= earliest:
        if day in solved:
            count += 1
        elif known.get(day, 0) >= FULL_DAY_MINUTES:
            break
        day -= dt.timedelta(days=1)
    return count


def group_streak(days: list[DayInfo], solved: set[dt.date], yesterday: dt.date) -> int:
    """Consecutive days, ending yesterday, on which at least one member found the champion.

    `solved` holds the days with any winner. A day the bot was down never breaks it, exactly like
    `streak`; but a full day yesterday with no winner means the group's run is over.
    """
    known = {d.day: d.up_minutes for d in days}
    if yesterday not in solved and known.get(yesterday, 0) >= FULL_DAY_MINUTES:
        return 0
    return streak(days, solved, yesterday)


def _hash(guild_id: int, day: dt.date, salt: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{guild_id}:{day}:{salt}".encode()).digest()[:8], "big")


def spell_index(guild_id: int, day: dt.date, n: int) -> int:
    """Which ability the day's hint shows: the same for everyone, not guessable from the date alone."""
    return _hash(guild_id, day, "spell") % n


def crop_box(guild_id: int, day: dt.date, width: int, height: int) -> tuple[int, int, int, int]:
    """A square in the middle of the splash, where the champion is rather than the background."""
    side = max(1, int(min(width, height) * 0.30))
    x_min, x_max = int(width * 0.25), max(int(width * 0.25), int(width * 0.75) - side)
    y_min, y_max = int(height * 0.15), max(int(height * 0.15), int(height * 0.75) - side)
    left = x_min + _hash(guild_id, day, "x") % (x_max - x_min + 1)
    top = y_min + _hash(guild_id, day, "y") % (y_max - y_min + 1)
    return left, top, left + side, top + side
