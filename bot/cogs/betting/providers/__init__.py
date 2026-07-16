from __future__ import annotations

import datetime
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


def chunks(items: list[str], size: int) -> Iterator[list[str]]:
    """Split a batch of ids across requests. No provider documents a cap on how many ids a
    filter accepts, and an unbounded URL is a 414 waiting to happen."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


Outcome = Literal["home", "away", "draw"]
# "pending" is not a result — it is the provider saying the match is still on the clock, and it
# exists to be told apart from `get_result` returning None (the API was unreachable, or reported
# a status we don't know). A result that isn't due yet is not a result that is late: the settle
# reminder must chase the second and stay quiet about the first.
ResultStatus = Literal["finished", "postponed", "cancelled", "pending"]


@dataclass(frozen=True)
class FixtureDTO:
    external_id: str
    sport: str
    competition: str
    home_name: str
    away_name: str
    start_time: datetime.datetime
    has_draw: bool


@dataclass(frozen=True)
class ResultDTO:
    status: ResultStatus
    winner: Outcome | None


class Provider(Protocol):
    name: str
    sport: str

    async def list_upcoming(self, days: int) -> list[FixtureDTO]: ...

    async def get_results(self, external_ids: Sequence[str]) -> dict[str, ResultDTO]:
        """Results for many matches at once, keyed by external_id.

        Batched, not one call per match, because the resolution ticker runs every 5 minutes over
        every locked market and football-data.org's free tier allows **10 requests a minute**.
        A Champions League matchday kicks off up to 18 games at once and they stay locked for
        ~2h, so per-match polling meant an 18-request burst every 5 minutes for 24 ticks —
        straight through the limit. And a throttled request is indistinguishable from a silent
        provider, which would fire the "le résultat n'est jamais arrivé" reminder about matches
        that were merely rate-limited.

        An id missing from the returned dict means "we don't know" — unreachable, unreadable, or
        simply not in the response. Callers must treat absence as unknown, never as void.
        """
        ...
