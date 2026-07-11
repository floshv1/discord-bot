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
        self._league_ids: list[int] | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _get(self, session: aiohttp.ClientSession, path: str, params: dict | None = None):
        """GET a PandaScore endpoint, returning parsed JSON or None on any failure."""
        try:
            async with session.get(
                f"{BASE_URL}{path}",
                headers=self._headers(),
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    # 401/403 means a bad key or a plan that doesn't cover LoL; 400 means we
                    # sent a filter this resource doesn't have. Log the body — it says which.
                    body = (await resp.text())[:200]
                    logger.warning(f"PandaScore {path} failed: HTTP {resp.status} — {body}")
                    return None
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"PandaScore {path} failed: {e}")
            return None

    async def _resolve_league_ids(self, session: aiohttp.ClientSession) -> list[int]:
        """Look up the numeric ids of the leagues we care about, once, and cache them.

        A match has a `league_id` but no `league_name`, so filtering matches by name is a
        400. Names only exist on the leagues resource, hence this extra hop.
        """
        if self._league_ids is not None:
            return self._league_ids

        leagues = await self._get(session, "/lol/leagues", {"filter[name]": ",".join(LEAGUES)})
        if not leagues:
            logger.warning(f"PandaScore matched none of the configured leagues: {', '.join(LEAGUES)}")
            self._league_ids = []
            return []

        self._league_ids = [league["id"] for league in leagues]
        found = ", ".join(f"{league['name']} ({league['id']})" for league in leagues)
        logger.info(f"PandaScore leagues resolved: {found}")
        return self._league_ids

    async def list_upcoming(self, days: int) -> list[FixtureDTO]:
        async with aiohttp.ClientSession() as session:
            league_ids = await self._resolve_league_ids(session)
            if not league_ids:
                return []
            matches = await self._get(
                session,
                "/lol/matches/upcoming",
                {
                    "filter[league_id]": ",".join(str(i) for i in league_ids),
                    "sort": "begin_at",
                    "per_page": 100,
                },
            )
        if not matches:
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
        async with aiohttp.ClientSession() as session:
            match = await self._get(session, f"/lol/matches/{external_id}")
        if not match:
            return None

        status = match.get("status")
        if status == "finished":
            winner_id = match.get("winner_id")
            opponents = match.get("opponents") or []
            winner = None
            if winner_id is not None and len(opponents) == 2:
                ids = [((o or {}).get("opponent") or {}).get("id") for o in opponents]
                if ids[0] == winner_id:
                    winner = "home"
                elif ids[1] == winner_id:
                    winner = "away"
            return ResultDTO(status="finished", winner=winner)
        if status in _VOID_STATUSES:
            return ResultDTO(status="postponed" if status == "postponed" else "cancelled", winner=None)
        return None
