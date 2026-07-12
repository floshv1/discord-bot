import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting.providers.football_data import FootballDataProvider
from bot.cogs.betting.providers.pandascore import PandaScoreProvider


def _mock_session(*payloads):
    """Mock aiohttp.ClientSession, returning each payload in turn for successive GETs."""

    def _resp(payload):
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value=payload)
        resp.text = AsyncMock(return_value="")
        return AsyncMock(__aenter__=AsyncMock(return_value=resp), __aexit__=AsyncMock(return_value=False))

    session = MagicMock()
    session.get = MagicMock(side_effect=[_resp(p) for p in payloads])
    return MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
    )


# PandaScore matches carry a league_id but no league_name, so the provider resolves ids first.
_LEAGUES_PAYLOAD = [{"id": 4198, "name": "LEC"}]


def _soon() -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_football_skips_fixtures_with_undecided_teams():
    # World Cup knockout slots are published before the teams are known: null team names.
    # Inserting those would violate the NOT NULL constraint on betting_markets.
    payload = {
        "matches": [
            {
                "id": 537388,
                "utcDate": _soon(),
                "competition": {"name": "FIFA World Cup"},
                "homeTeam": {"name": None},
                "awayTeam": {"name": None},
            },
            {
                "id": 1,
                "utcDate": _soon(),
                "competition": {"name": "FIFA World Cup"},
                "homeTeam": {"name": "France"},
                "awayTeam": {"name": "Argentina"},
            },
        ]
    }
    with patch("aiohttp.ClientSession", _mock_session(payload)):
        fixtures = await FootballDataProvider("key").list_upcoming(7)

    assert [f.external_id for f in fixtures] == ["1"]
    assert fixtures[0].home_name == "France"


@pytest.mark.asyncio
async def test_football_skips_fixture_with_missing_team_object():
    payload = {
        "matches": [
            {
                "id": 2,
                "utcDate": _soon(),
                "competition": {"name": "FIFA World Cup"},
                "homeTeam": None,
                "awayTeam": None,
            }
        ]
    }
    with patch("aiohttp.ClientSession", _mock_session(payload)):
        fixtures = await FootballDataProvider("key").list_upcoming(7)

    assert fixtures == []


@pytest.mark.asyncio
async def test_pandascore_skips_matches_with_undecided_opponents():
    matches = [
        {
            "id": 10,
            "begin_at": _soon(),
            "league": {"name": "LEC"},
            "opponents": [{"opponent": None}, {"opponent": None}],
        },
        {
            "id": 11,
            "begin_at": _soon(),
            "league": {"name": "LEC"},
            "opponents": [{"opponent": {"name": "G2"}}, {"opponent": {"name": "Fnatic"}}],
        },
    ]
    with patch("aiohttp.ClientSession", _mock_session(_LEAGUES_PAYLOAD, matches)):
        fixtures = await PandaScoreProvider("key").list_upcoming(7)

    assert [f.external_id for f in fixtures] == ["11"]
    assert fixtures[0].away_name == "Fnatic"


@pytest.mark.asyncio
async def test_pandascore_filters_matches_by_resolved_league_id():
    # Filtering matches by league_name is an HTTP 400 — matches only have a league_id.
    matches = [
        {
            "id": 11,
            "begin_at": _soon(),
            "league": {"name": "LEC"},
            "opponents": [{"opponent": {"name": "G2"}}, {"opponent": {"name": "Fnatic"}}],
        }
    ]
    session_factory = _mock_session(_LEAGUES_PAYLOAD, matches)
    with patch("aiohttp.ClientSession", session_factory):
        await PandaScoreProvider("key").list_upcoming(7)

    session = session_factory.return_value.__aenter__.return_value
    leagues_call, matches_call = session.get.call_args_list
    assert leagues_call[0][0].endswith("/lol/leagues")
    assert matches_call[0][0].endswith("/lol/matches/upcoming")
    params = matches_call[1]["params"]
    assert params["filter[league_id]"] == "4198"
    assert "filter[league_name]" not in params


@pytest.mark.asyncio
async def test_pandascore_returns_nothing_when_no_leagues_match():
    with patch("aiohttp.ClientSession", _mock_session([])):
        fixtures = await PandaScoreProvider("key").list_upcoming(7)

    assert fixtures == []


# --- Results -----------------------------------------------------------------
#
# A market only settles when the provider reports a winner it can map onto home/away. Every
# way that mapping can fail leaves real coins frozen in a locked market, so each one matters.

_BLG, _HLE = 3211, 3212


def _finished(winner_id, results=None, **kw):
    return {
        "id": 1,
        "status": "finished",
        "winner_id": winner_id,
        "opponents": [{"opponent": {"id": _BLG}}, {"opponent": {"id": _HLE}}],
        "results": results,
        **kw,
    }


async def _result(payload):
    with patch("aiohttp.ClientSession", _mock_session(payload)):
        return await PandaScoreProvider("key").get_result("1")


@pytest.mark.asyncio
async def test_pandascore_maps_the_reported_winner():
    assert (await _result(_finished(_HLE))).winner == "away"
    assert (await _result(_finished(_BLG))).winner == "home"


@pytest.mark.asyncio
async def test_pandascore_falls_back_to_the_scoreline():
    """The MSI bug: a finished match with no winner_id used to settle nothing, forever.

    The resolution ticker retried it every five minutes, reported "finished with no winner",
    and left a member's stake frozen in a locked market with no announcement. The score was in
    the same payload the whole time.
    """
    result = await _result(_finished(None, results=[{"team_id": _BLG, "score": 1}, {"team_id": _HLE, "score": 3}]))

    assert result.status == "finished"
    assert result.winner == "away"


@pytest.mark.asyncio
async def test_pandascore_never_guesses_a_winner():
    # Settling on a coin-flip would pay the wrong people, which is worse than paying nobody
    # yet — the stuck-market reminder and the 7-day refund are the safety net for these.
    tied = _finished(None, results=[{"team_id": _BLG, "score": 2}, {"team_id": _HLE, "score": 2}])
    unknown = _finished(None, results=[{"team_id": 9999, "score": 3}, {"team_id": 8888, "score": 1}])

    assert (await _result(tied)).winner is None
    assert (await _result(unknown)).winner is None
    assert (await _result(_finished(None, results=None))).winner is None


@pytest.mark.asyncio
async def test_pandascore_voids_a_cancelled_match():
    assert (await _result({"id": 1, "status": "canceled"})).status == "cancelled"
    assert (await _result({"id": 1, "status": "postponed"})).status == "postponed"


@pytest.mark.asyncio
async def test_pandascore_says_nothing_about_a_match_still_running():
    assert await _result({"id": 1, "status": "running"}) is None
