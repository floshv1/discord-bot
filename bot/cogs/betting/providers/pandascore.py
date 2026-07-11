from __future__ import annotations

import datetime

import aiohttp
from loguru import logger

from bot.cogs.betting.providers import FixtureDTO, ResultDTO

BASE_URL = "https://api.pandascore.co"
LEAGUES = ["LEC", "Worlds", "MSI"]

_VOID_STATUSES = {"canceled", "postponed"}


class PandaScoreProvider:
    name = "pandascore"
    sport = "lol"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def list_upcoming(self, days: int) -> list[FixtureDTO]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/lol/matches/upcoming",
                    headers=self._headers(),
                    params={"filter[league_name]": ",".join(LEAGUES), "sort": "begin_at", "per_page": 50},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"PandaScore fixtures request failed: {resp.status}")
                        return []
                    matches = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"PandaScore fixtures request failed: {e}")
            return []

        cutoff = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days)
        fixtures = []
        for match in matches:
            begin_at = match.get("begin_at") or match.get("scheduled_at")
            if not begin_at:
                continue
            start_time = datetime.datetime.fromisoformat(begin_at.replace("Z", "+00:00"))
            if start_time > cutoff:
                continue
            opponents = match.get("opponents") or []
            if len(opponents) != 2:
                continue
            home_name = opponents[0]["opponent"]["name"]
            away_name = opponents[1]["opponent"]["name"]
            fixtures.append(
                FixtureDTO(
                    external_id=str(match["id"]),
                    sport=self.sport,
                    competition=match.get("league", {}).get("name", "LoL"),
                    home_name=home_name,
                    away_name=away_name,
                    start_time=start_time,
                    has_draw=False,
                )
            )
        return fixtures

    async def get_result(self, external_id: str) -> ResultDTO | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/lol/matches/{external_id}",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"PandaScore result request failed: {resp.status}")
                        return None
                    match = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"PandaScore result request failed: {e}")
            return None

        status = match.get("status")
        if status == "finished":
            winner_id = match.get("winner_id")
            opponents = match.get("opponents") or []
            winner = None
            if winner_id is not None and len(opponents) == 2:
                if opponents[0]["opponent"]["id"] == winner_id:
                    winner = "home"
                elif opponents[1]["opponent"]["id"] == winner_id:
                    winner = "away"
            return ResultDTO(status="finished", winner=winner)
        if status in _VOID_STATUSES:
            return ResultDTO(status="postponed" if status == "postponed" else "cancelled", winner=None)
        return None
