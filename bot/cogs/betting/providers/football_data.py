from __future__ import annotations

import datetime

import aiohttp
from loguru import logger

from bot.cogs.betting.providers import FixtureDTO, ResultDTO

BASE_URL = "https://api.football-data.org/v4"
COMPETITION_CODE = "WC"  # World Cup

_WINNER_MAP = {"HOME_TEAM": "home", "AWAY_TEAM": "away", "DRAW": "draw"}
_FINISHED_STATUSES = {"FINISHED", "AWARDED"}
_VOID_STATUSES = {"POSTPONED", "CANCELLED", "SUSPENDED"}


class FootballDataProvider:
    name = "football_data"
    sport = "football"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"X-Auth-Token": self._api_key}

    async def list_upcoming(self, days: int) -> list[FixtureDTO]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/competitions/{COMPETITION_CODE}/matches",
                    headers=self._headers(),
                    params={"status": "SCHEDULED"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        # 403 here usually means the free tier doesn't cover this competition.
                        body = (await resp.text())[:200]
                        logger.warning(f"football-data.org fixtures request failed: HTTP {resp.status} — {body}")
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"football-data.org fixtures request failed: {e}")
            return []

        cutoff = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days)
        fixtures = []
        for match in data.get("matches", []):
            start_time = datetime.datetime.fromisoformat(match["utcDate"].replace("Z", "+00:00"))
            if start_time > cutoff:
                continue
            # Knockout slots are published before their teams are known, with null team names.
            # There's nothing to bet on yet — they'll be picked up on a later poll once seeded.
            home_name = (match.get("homeTeam") or {}).get("name")
            away_name = (match.get("awayTeam") or {}).get("name")
            if not home_name or not away_name:
                continue
            fixtures.append(
                FixtureDTO(
                    external_id=str(match["id"]),
                    sport=self.sport,
                    competition=match.get("competition", {}).get("name", "World Cup"),
                    home_name=home_name,
                    away_name=away_name,
                    start_time=start_time,
                    has_draw=True,
                )
            )
        return fixtures

    async def get_result(self, external_id: str) -> ResultDTO | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/matches/{external_id}",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"football-data.org result request failed: {resp.status}")
                        return None
                    match = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"football-data.org result request failed: {e}")
            return None

        status = match.get("status")
        if status in _FINISHED_STATUSES:
            winner_raw = match.get("score", {}).get("winner")
            winner = _WINNER_MAP.get(winner_raw)
            return ResultDTO(status="finished", winner=winner)
        if status in _VOID_STATUSES:
            return ResultDTO(status="postponed" if status == "POSTPONED" else "cancelled", winner=None)
        return None
