from __future__ import annotations

import datetime
from collections.abc import Sequence

import aiohttp
from loguru import logger

from bot.cogs.betting.providers import FixtureDTO, ResultDTO, chunks

BASE_URL = "https://api.pandascore.co"

# PandaScore's `filter[...]` is strict equality, so these must be the league's exact name —
# and "exact" means *whatever PandaScore calls it*, which is not always the formal name.
# `Worlds` (id 297) is the real one; "World Championship" matched nothing, and a comment here
# once asserted the opposite. That cost the server every Worlds market, silently, for as long
# as it stood: the filter drops an unmatched name without a word. Only `_resolve_league_ids`'
# warning surfaced it. Verify a new name against GET /lol/leagues before adding it — guessing
# is what put "World Championship" (and "Esports Nations Cup", which does not exist at all)
# in this list.
LEAGUES = [
    "LEC",
    "LFL",
    "LCK",
    "Worlds",
    "Mid-Season Invitational",  # "MSI" really is colloquial and matches nothing
    "Esports World Cup",  # ...as is "EWC"
]

_VOID_STATUSES = {"canceled", "postponed"}
# Still on the clock — told apart from get_result returning None (unreachable / unknown status)
# so the settle reminder chases a missing result and not a match that is simply still being played.
_PENDING_STATUSES = {"not_started", "running"}

# PandaScore caps per_page at 100; 50 keeps the URL sane and is far above any real matchday.
_MAX_IDS_PER_REQUEST = 50


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

        # A name that matches nothing is silently dropped by the filter: the other leagues
        # still resolve, so the only symptom is a tournament that never gets a market. Say it.
        missing = [name for name in LEAGUES if name not in {league["name"] for league in leagues}]
        if missing:
            logger.warning(f"PandaScore has no league named: {', '.join(missing)} — no market will be created for it")
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

    def _side_of(self, match: dict, team_id: int | None) -> str | None:
        """Map a PandaScore team id onto the market's home/away sides."""
        opponents = match.get("opponents") or []
        if team_id is None or len(opponents) != 2:
            return None
        ids = [((o or {}).get("opponent") or {}).get("id") for o in opponents]
        if ids[0] == team_id:
            return "home"
        if ids[1] == team_id:
            return "away"
        return None

    def _winner_from_scores(self, match: dict) -> str | None:
        """Work out the winner from the scoreline when `winner_id` is missing.

        PandaScore does not always fill `winner_id` on a finished match, and when it doesn't
        we used to report "finished, winner unknown" forever: the resolution ticker would
        retry every 5 minutes, settle nothing, and say nothing, leaving real stakes frozen.
        The scoreline is right there in the same payload and answers the question.

        A draw is not a real LoL result, so equal scores mean the data is incomplete rather
        than tied — return None and let the caller keep waiting.
        """
        results = match.get("results") or []
        if len(results) != 2:
            return None
        best, other = sorted(results, key=lambda r: r.get("score") or 0, reverse=True)
        if (best.get("score") or 0) == (other.get("score") or 0):
            return None
        return self._side_of(match, best.get("team_id"))

    async def get_results(self, external_ids: Sequence[str]) -> dict[str, ResultDTO]:
        """Every requested match's result in as few requests as possible. See Provider.

        PandaScore's quota is far roomier than football-data's, but the same shape of bug is
        available here and one code path beats two.
        """
        if not external_ids:
            return {}

        results: dict[str, ResultDTO] = {}
        async with aiohttp.ClientSession() as session:
            for chunk in chunks(list(external_ids), _MAX_IDS_PER_REQUEST):
                matches = await self._get(
                    session,
                    "/lol/matches",
                    {"filter[id]": ",".join(chunk), "per_page": str(_MAX_IDS_PER_REQUEST)},
                )
                for match in matches or []:
                    result = self._to_result(match)
                    if result is not None:
                        results[str(match["id"])] = result
        return results

    def _to_result(self, match: dict) -> ResultDTO | None:
        external_id = match.get("id")
        status = match.get("status")
        if status == "finished":
            winner = self._side_of(match, match.get("winner_id")) or self._winner_from_scores(match)
            if winner is None:
                # Never settle on a guess — but say enough that the next one is diagnosable,
                # because the failure is otherwise completely silent. The market keeps being
                # retried, gets chased by the settle reminder, and is refunded after a week.
                logger.warning(
                    f"PandaScore match {external_id} finished but the winner is unmappable: "
                    f"winner_id={match.get('winner_id')} results={match.get('results')} "
                    f"opponents={[((o or {}).get('opponent') or {}).get('id') for o in match.get('opponents') or []]}"
                )
            return ResultDTO(status="finished", winner=winner)
        if status in _VOID_STATUSES:
            return ResultDTO(status="postponed" if status == "postponed" else "cancelled", winner=None)
        if status in _PENDING_STATUSES:
            return ResultDTO(status="pending", winner=None)
        # An unknown status is not "still running" — we genuinely don't know, same as an
        # unreachable API. Say nothing rather than vouch for a match we can't read.
        return None
