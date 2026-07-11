from __future__ import annotations

import datetime

import aiohttp
from loguru import logger

from bot.cogs.betting.providers import FixtureDTO, ResultDTO

BASE_URL = "https://api.pandascore.co"

# PandaScore's `filter[...]` is strict equality, so these must be the league's exact name.
# "Worlds" and "MSI" are the colloquial names and match nothing.
LEAGUES = ["LEC", "World Championship", "Mid-Season Invitational"]

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
                        # 401/403 here usually means the API key is wrong or the plan doesn't
                        # include LoL — worth distinguishing from "no matches scheduled".
                        body = (await resp.text())[:200]
                        logger.warning(f"PandaScore fixtures request failed: HTTP {resp.status} — {body}")
                        return []
                    matches = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"PandaScore fixtures request failed: {e}")
            return []

        if not matches:
            logger.info(f"PandaScore returned no upcoming matches for leagues: {', '.join(LEAGUES)}")

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
            # Bracket slots are listed before their teams are known — nothing to bet on yet.
            home_name = ((opponents[0] or {}).get("opponent") or {}).get("name")
            away_name = ((opponents[1] or {}).get("opponent") or {}).get("name")
            if not home_name or not away_name:
                continue
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
