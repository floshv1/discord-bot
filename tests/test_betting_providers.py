import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

from bot.cogs.betting.providers.football_data import FootballDataProvider
from bot.cogs.betting.providers.pandascore import PandaScoreProvider


def _resp(payload, status=200):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=payload)
    resp.text = AsyncMock(return_value="")
    return AsyncMock(__aenter__=AsyncMock(return_value=resp), __aexit__=AsyncMock(return_value=False))


def _mock_session(*payloads):
    """Mock aiohttp.ClientSession, returning each payload in turn for successive GETs.

    A payload may be a `_resp(...)` to control the HTTP status; anything else is a 200 body.
    """
    session = MagicMock()
    session.get = MagicMock(side_effect=[p if isinstance(p, AsyncMock) else _resp(p) for p in payloads])
    return MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
    )


def _one_competition():
    """Pin the football provider to a single competition.

    Tests about parsing one fixture shouldn't have to feed a payload per competition, and
    shouldn't break the day COMPETITIONS grows. The loop itself is covered separately.
    """
    return patch("bot.cogs.betting.providers.football_data.COMPETITIONS", ["WC"])


# PandaScore matches carry a league_id but no league_name, so the provider resolves ids first.
_LEAGUES_PAYLOAD = [{"id": 4197, "name": "LEC"}]  # 4197 is the LEC's real id; 4198 is the LCS


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
    with _one_competition(), patch("aiohttp.ClientSession", _mock_session(payload)):
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
    with _one_competition(), patch("aiohttp.ClientSession", _mock_session(payload)):
        fixtures = await FootballDataProvider("key").list_upcoming(7)

    assert fixtures == []


@pytest.mark.asyncio
async def test_football_polls_every_competition():
    def _match(match_id, competition):
        return {
            "id": match_id,
            "utcDate": _soon(),
            "competition": {"name": competition},
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
        }

    session_factory = _mock_session(
        {"matches": [_match(1, "FIFA World Cup")]},
        {"matches": [_match(2, "UEFA Champions League")]},
        {"matches": [_match(3, "Ligue 1")]},
    )
    with patch("bot.cogs.betting.providers.football_data.COMPETITIONS", ["WC", "CL", "FL1"]):
        with patch("aiohttp.ClientSession", session_factory):
            fixtures = await FootballDataProvider("key").list_upcoming(7)

    session = session_factory.return_value.__aenter__.return_value
    assert [c[0][0].split("/competitions/")[1] for c in session.get.call_args_list] == [
        "WC/matches",
        "CL/matches",
        "FL1/matches",
    ]
    assert [f.competition for f in fixtures] == ["FIFA World Cup", "UEFA Champions League", "Ligue 1"]


@pytest.mark.asyncio
async def test_football_keeps_the_other_competitions_when_one_is_forbidden():
    # The free tier covers some competitions and not others: a 403 on the Champions League must
    # still leave Ligue 1 on the board, or one unavailable league empties the whole sport.
    ligue1 = {
        "matches": [
            {
                "id": 3,
                "utcDate": _soon(),
                "competition": {"name": "Ligue 1"},
                "homeTeam": {"name": "PSG"},
                "awayTeam": {"name": "OM"},
            }
        ]
    }
    session_factory = _mock_session(_resp({}, status=403), ligue1)
    with patch("bot.cogs.betting.providers.football_data.COMPETITIONS", ["CL", "FL1"]):
        with patch("aiohttp.ClientSession", session_factory):
            fixtures = await FootballDataProvider("key").list_upcoming(7)

    assert [f.external_id for f in fixtures] == ["3"]
    assert fixtures[0].competition == "Ligue 1"


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
    assert params["filter[league_id]"] == "4197"
    assert "filter[league_name]" not in params


@pytest.mark.asyncio
async def test_pandascore_returns_nothing_when_no_leagues_match():
    with patch("aiohttp.ClientSession", _mock_session([])):
        fixtures = await PandaScoreProvider("key").list_upcoming(7)

    assert fixtures == []


@pytest.mark.asyncio
async def test_pandascore_warns_about_a_league_name_that_matched_nothing():
    """A wrong league name is dropped by the strict-equality filter without a word.

    This is not hypothetical. `LEAGUES` held "World Championship" for months; PandaScore calls
    it `Worlds` (id 297). Every other league resolved, so the only symptom was that Worlds
    never got a single market — through an entire tournament. This warning is what finally
    said so, and it is the only thing standing between a typo and a silent hole.

    Loguru doesn't feed caplog, so capture it with a sink of our own.
    """
    warnings: list[str] = []
    sink_id = logger.add(lambda m: warnings.append(m.record["message"]), level="WARNING")
    try:
        with patch("aiohttp.ClientSession", _mock_session(_LEAGUES_PAYLOAD, [])):
            await PandaScoreProvider("key").list_upcoming(7)
    finally:
        logger.remove(sink_id)

    missing = next(w for w in warnings if "no league named" in w)
    assert "Worlds" in missing
    assert "Mid-Season Invitational" in missing
    assert "Esports World Cup" in missing
    assert "LEC" not in missing  # the one league that did resolve isn't reported as missing


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
    # /lol/matches?filter[id]= returns a list, and get_results keys it by external_id.
    with patch("aiohttp.ClientSession", _mock_session([payload])):
        results = await PandaScoreProvider("key").get_results(["1"])
    return results.get("1")


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
async def test_pandascore_says_a_running_match_is_pending_not_unknown():
    # "pending" and None both used to be None, and that conflation is what made the bot announce
    # "le résultat n'est jamais arrivé" about matches that were still being played. A result
    # that isn't due yet is not a result that is late.
    assert (await _result({"id": 1, "status": "running"})).status == "pending"
    assert (await _result({"id": 1, "status": "not_started"})).status == "pending"


@pytest.mark.asyncio
async def test_pandascore_does_not_vouch_for_a_status_it_cannot_read():
    # An unknown status is not "still running" — we genuinely don't know, exactly like an
    # unreachable API. Claiming it's pending would mute the reminder on a market nobody settles.
    assert await _result({"id": 1, "status": "who_knows"}) is None


async def _football_result(payload):
    with patch("aiohttp.ClientSession", _mock_session({"matches": [{"id": 1, **payload}]})):
        results = await FootballDataProvider("key").get_results(["1"])
    return results.get("1")


@pytest.mark.asyncio
async def test_football_fetches_every_result_in_one_request():
    """The rate-limit fix: the free tier allows 10 requests a minute.

    A Champions League matchday kicks off up to 18 games at once and they stay locked for ~2h,
    so one request per match meant an 18-request burst every 5 minutes, for 24 ticks. Worse, a
    throttled request is indistinguishable from a silent provider — it would have fired the
    "le résultat n'est jamais arrivé" reminder about matches that were merely rate-limited.
    """
    ids = [str(i) for i in range(18)]
    payload = {"matches": [{"id": i, "status": "IN_PLAY"} for i in range(18)]}
    session_factory = _mock_session(payload)
    with patch("aiohttp.ClientSession", session_factory):
        results = await FootballDataProvider("key").get_results(ids)

    session = session_factory.return_value.__aenter__.return_value
    assert session.get.call_count == 1  # not 18
    assert session.get.call_args[1]["params"]["ids"] == ",".join(ids)
    assert len(results) == 18


@pytest.mark.asyncio
async def test_a_throttled_batch_reports_nothing_rather_than_guessing():
    # HTTP 429 lands in the same branch as any non-200: absence means "we don't know", which
    # lets the settle reminder still ring for a provider that has genuinely gone quiet.
    with patch("aiohttp.ClientSession", _mock_session(_resp({}, status=429))):
        assert await FootballDataProvider("key").get_results(["1", "2"]) == {}


@pytest.mark.asyncio
async def test_asking_for_no_results_costs_no_request():
    session_factory = _mock_session()
    with patch("aiohttp.ClientSession", session_factory):
        assert await FootballDataProvider("key").get_results([]) == {}
        assert await PandaScoreProvider("key").get_results([]) == {}


@pytest.mark.asyncio
async def test_football_says_a_match_on_the_clock_is_pending():
    # The bug that started this: a football match runs ~1h50-2h05 and the settle reminder is due
    # 2h after kickoff, so IN_PLAY/PAUSED at reminder time is the normal case, not the odd one.
    for status in ("SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"):
        assert (await _football_result({"status": status})).status == "pending", status


@pytest.mark.asyncio
async def test_football_still_reports_a_finished_match_and_a_void_one():
    finished = await _football_result({"status": "FINISHED", "score": {"winner": "AWAY_TEAM"}})
    assert (finished.status, finished.winner) == ("finished", "away")
    assert (await _football_result({"status": "POSTPONED"})).status == "postponed"
    assert (await _football_result({"status": "CANCELLED"})).status == "cancelled"


@pytest.mark.asyncio
async def test_football_does_not_vouch_for_a_status_it_cannot_read():
    assert await _football_result({"status": "WHO_KNOWS"}) is None
