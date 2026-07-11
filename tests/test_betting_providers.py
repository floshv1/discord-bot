import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting.providers.football_data import FootballDataProvider
from bot.cogs.betting.providers.pandascore import PandaScoreProvider


def _mock_session(payload):
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value=payload)

    session = MagicMock()
    session.get = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=resp), __aexit__=AsyncMock(return_value=False))
    )
    return MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
    )


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
    payload = [
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
    with patch("aiohttp.ClientSession", _mock_session(payload)):
        fixtures = await PandaScoreProvider("key").list_upcoming(7)

    assert [f.external_id for f in fixtures] == ["11"]
    assert fixtures[0].away_name == "Fnatic"
