from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal, Protocol

Outcome = Literal["home", "away", "draw"]
ResultStatus = Literal["finished", "postponed", "cancelled"]


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

    async def get_result(self, external_id: str) -> ResultDTO | None: ...
