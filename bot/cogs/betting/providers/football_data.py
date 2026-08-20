from __future__ import annotations

import datetime
from collections.abc import Sequence

import aiohttp
from loguru import logger

from bot.cogs.betting.providers import FixtureDTO, ResultDTO, chunks

BASE_URL = "https://api.football-data.org/v4"
# football-data.org competition codes, all three confirmed in the free plan's 12. Each is
# fetched independently anyway: the plan could change, and a 403 on one must not cost us the
# others (see _fetch_matches).
#
# No domestic league is followed — Ligue 1 (FL1) was dropped for the same reason PL, PD, SA and
# BL1 were never added, despite all being available: football shares a single channel by design,
# and a league matchday is 9 more cards that bury the Champions League. The API cost isn't the
# constraint since results are batched; the channel is.
#
# Dropping a code only stops *new* markets. Results are fetched by match id, not by competition
# (see get_results), so any Ligue 1 market still open or locked settles normally on its own —
# which is why it is deliberately not in service.RETIRED_LEAGUES: nobody's live bet is refunded.
COMPETITIONS = ["WC", "EC", "CL"]  # World Cup, Euro, UEFA Champions League

_WINNER_MAP = {"HOME_TEAM": "home", "AWAY_TEAM": "away", "DRAW": "draw"}
_FINISHED_STATUSES = {"FINISHED", "AWARDED"}
_VOID_STATUSES = {"POSTPONED", "CANCELLED", "SUSPENDED"}
# Still on the clock. A football match runs ~1h50-2h05, so at the 2h settle-reminder mark it is
# routinely IN_PLAY or PAUSED — reported as "the result never arrived" until this said otherwise.
_PENDING_STATUSES = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"}

# 50 is comfortably above any realistic matchday (the Champions League's biggest is 18), so
# results stay one request — which is the whole point on a 10-requests-a-minute tier.
_MAX_IDS_PER_REQUEST = 50


class FootballDataProvider:
    name = "football_data"
    sport = "football"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"X-Auth-Token": self._api_key}

    async def list_upcoming(self, days: int) -> list[FixtureDTO]:
        cutoff = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days)
        fixtures: list[FixtureDTO] = []
        async with aiohttp.ClientSession() as session:
            for code in COMPETITIONS:
                for match in await self._fetch_matches(session, code):
                    fixture = self._to_fixture(match, code, cutoff)
                    if fixture is not None:
                        fixtures.append(fixture)
        return fixtures

    async def _fetch_matches(self, session: aiohttp.ClientSession, code: str) -> list[dict]:
        """One competition's scheduled matches, or [] if it couldn't be read.

        Isolated per competition on purpose: the free tier covers some and not others, so a 403
        on the Champions League must still leave the Euro and the World Cup on the board.
        """
        try:
            async with session.get(
                f"{BASE_URL}/competitions/{code}/matches",
                headers=self._headers(),
                params={"status": "SCHEDULED"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    # 403 here usually means the free tier doesn't cover this competition.
                    body = (await resp.text())[:200]
                    logger.warning(f"football-data.org fixtures request failed for {code}: HTTP {resp.status} — {body}")
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"football-data.org fixtures request failed for {code}: {e}")
            return []
        return data.get("matches", [])

    def _to_fixture(self, match: dict, code: str, cutoff: datetime.datetime) -> FixtureDTO | None:
        start_time = datetime.datetime.fromisoformat(match["utcDate"].replace("Z", "+00:00"))
        if start_time > cutoff:
            return None
        # Knockout slots are published before their teams are known, with null team names.
        # There's nothing to bet on yet — they'll be picked up on a later poll once seeded.
        home_name = (match.get("homeTeam") or {}).get("name")
        away_name = (match.get("awayTeam") or {}).get("name")
        if not home_name or not away_name:
            return None
        return FixtureDTO(
            external_id=str(match["id"]),
            sport=self.sport,
            # The API's display name is what betting_markets.competition holds and what the
            # cards show; the code is only a last resort if the payload omits it.
            competition=(match.get("competition") or {}).get("name") or code,
            home_name=home_name,
            away_name=away_name,
            start_time=start_time,
            has_draw=True,
        )

    async def get_results(self, external_ids: Sequence[str]) -> dict[str, ResultDTO]:
        """Every requested match's result in as few requests as possible. See Provider.

        The free tier allows 10 requests a minute; a Champions League matchday locks up to 18
        games at once. One request per match burnt through that every 5 minutes — /matches?ids=
        collapses the lot into one.
        """
        if not external_ids:
            return {}

        results: dict[str, ResultDTO] = {}
        async with aiohttp.ClientSession() as session:
            for chunk in chunks(list(external_ids), _MAX_IDS_PER_REQUEST):
                for match in await self._fetch_results(session, chunk):
                    result = self._to_result(match)
                    if result is not None:
                        results[str(match["id"])] = result
        return results

    async def _fetch_results(self, session: aiohttp.ClientSession, ids: Sequence[str]) -> list[dict]:
        try:
            async with session.get(
                f"{BASE_URL}/matches",
                headers=self._headers(),
                params={"ids": ",".join(ids)},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    # 429 lands here too. Returning nothing means "we don't know", which is the
                    # truth — the caller must not mistake a throttle for a missing result.
                    body = (await resp.text())[:200]
                    logger.warning(f"football-data.org results request failed: HTTP {resp.status} — {body}")
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"football-data.org results request failed: {e}")
            return []
        return data.get("matches", [])

    def _to_result(self, match: dict) -> ResultDTO | None:
        status = match.get("status")
        if status in _FINISHED_STATUSES:
            winner_raw = (match.get("score") or {}).get("winner")
            winner = _WINNER_MAP.get(winner_raw)
            return ResultDTO(status="finished", winner=winner)
        if status in _VOID_STATUSES:
            return ResultDTO(status="postponed" if status == "POSTPONED" else "cancelled", winner=None)
        if status in _PENDING_STATUSES:
            return ResultDTO(status="pending", winner=None)
        # An unknown status is not "still running" — we genuinely don't know, same as an
        # unreachable API. Say nothing rather than vouch for a match we can't read.
        return None
