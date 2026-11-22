"""The customs rules that need no Discord and no database, so they can be tested by value."""

from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

from bot.cogs.customs.lolamis import Slot, Teams

PARIS = ZoneInfo("Europe/Paris")
PLAYERS = 10
MIN_SLOTS, MAX_SLOTS = 2, 5
REMIND_BEFORE = dt.timedelta(minutes=15)
EXPIRE_AFTER_START = dt.timedelta(hours=6)
EXPIRE_AFTER_CREATION = dt.timedelta(hours=24)

_TIME = re.compile(r"^(\d{1,2})(?:[h:](\d{2})?)?$", re.IGNORECASE)


def parse_time(text: str, now: dt.datetime) -> dt.datetime:
    """`21h`, `21h30`, `21:30` or `21` → that time in Paris: today, or tomorrow if already past."""
    m = _TIME.match(text.strip())
    if not m:
        raise ValueError(f"heure invalide : {text!r}")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        raise ValueError(f"heure invalide : {text!r}")
    local_now = now.astimezone(PARIS)
    t = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if t <= local_now:
        t += dt.timedelta(days=1)
    return t


def parse_slots(text: str, now: dt.datetime) -> list[dt.datetime]:
    slots = sorted({parse_time(part, now) for part in re.split(r"[\s,;]+", text.strip()) if part})
    if not MIN_SLOTS <= len(slots) <= MAX_SLOTS:
        raise ValueError(f"il faut entre {MIN_SLOTS} et {MAX_SLOTS} créneaux")
    return slots


def split_roster(signups: list) -> tuple[list, list]:
    """Signups arrive in signup order; the first ten play, the rest wait in that same order."""
    return list(signups[:PLAYERS]), list(signups[PLAYERS:])


def vote_winner(counts: dict[dt.datetime, int]) -> dt.datetime | None:
    full = [slot for slot, n in counts.items() if n >= PLAYERS]
    return min(full) if full else None


def expires_at(start_at: dt.datetime | None, created_at: dt.datetime) -> dt.datetime:
    return start_at + EXPIRE_AFTER_START if start_at else created_at + EXPIRE_AFTER_CREATION


def evening_score(games: list) -> tuple[int, int]:
    winners = [g["winner"] for g in games]
    return winners.count("blue"), winners.count("red")


def swap(teams: Teams, a: int, b: int) -> Teams:
    """Swap two players' places: lane within a team, side *and* lane across teams."""
    slots = {s.person_id: ("blue", s.lane) for s in teams.blue} | {s.person_id: ("red", s.lane) for s in teams.red}
    if a not in slots or b not in slots or a == b:
        raise ValueError("joueurs invalides")
    slots[a], slots[b] = slots[b], slots[a]

    def side(name: str) -> tuple[Slot, ...]:
        return tuple(sorted((Slot(p, lane) for p, (s, lane) in slots.items() if s == name), key=lambda x: x.lane))

    return Teams(side("blue"), side("red"), teams.p_blue)
