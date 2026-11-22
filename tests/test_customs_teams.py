"""The evening: generate, regenerate, swap, record the winner (once), next game, end."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.customs import cog as customs_cog
from bot.cogs.customs.lolamis import LolAmisError, Slot, Teams

ORGANISER, MEMBER = 1, 2
TEAMS = Teams(tuple(Slot(i, i) for i in range(5)), tuple(Slot(5 + i, i) for i in range(5)), 0.52)


def custom_row(**kw):
    row = {
        "id": 5,
        "guild_id": 10,
        "organiser_id": ORGANISER,
        "channel_id": 20,
        "message_id": 30,
        "mode": "fixed",
        "start_at": None,
        "status": "signup",
    }
    row.update(kw)
    return row


def game(winner=None, number=1, lolamis_game_id=None):
    return {
        "number": number,
        "teams": json.dumps(TEAMS.to_json()),
        "winner": winner,
        "lolamis_game_id": lolamis_game_id,
    }


def signups(n):
    return [{"id": i, "user_id": 100 + i, "person_id": i} for i in range(n)]


def interaction(user_id=ORGANISER, mod=False):
    i = MagicMock()
    i.user.id = user_id
    i.user.guild_permissions.manage_messages = mod
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.followup.send = AsyncMock()
    return i


@pytest.fixture
def cog():
    bot = MagicMock()
    c = customs_cog.CustomsCog(bot)
    c.client = MagicMock()
    for name in ("generate", "evaluate", "record_game", "set_winner", "find_game"):
        setattr(c.client, name, AsyncMock())
    c.client.generate.return_value = TEAMS
    c.client.record_game.return_value = 77
    c.client.find_game.return_value = None
    return c


def svc(**fns):
    return patch.multiple(customs_cog.service, **{k: AsyncMock(return_value=v) for k, v in fns.items()})


async def test_generate_refused_at_nine(cog):
    with svc(get=custom_row(), signups=signups(9), add_game=1):
        await cog.handle(interaction(), "gen", 5, None)
        customs_cog.service.add_game.assert_not_called()
    cog.client.generate.assert_not_called()


async def test_generate_sends_the_ten_and_starts_game_one(cog):
    with svc(get=custom_row(), signups=signups(12), add_game=1):
        await cog.handle(interaction(), "gen", 5, None)
        customs_cog.service.add_game.assert_awaited_once_with(5, TEAMS.to_json())
    cog.client.generate.assert_awaited_once_with(list(range(10)))


async def test_only_organiser_generates(cog):
    with svc(get=custom_row(), signups=signups(10), add_game=1):
        await cog.handle(interaction(user_id=MEMBER), "gen", 5, None)
    cog.client.generate.assert_not_called()


async def test_regenerate_refused_once_a_winner_is_in(cog):
    with svc(get=custom_row(status="teams"), games=[game(winner="blue")], set_game_teams=True):
        await cog.handle(interaction(), "regen", 5, None)
    cog.client.generate.assert_not_called()


async def test_swap_across_teams_is_rescored_and_saved(cog):
    cog.client.evaluate.side_effect = lambda t: t
    with svc(set_game_teams=True):
        out = await cog.swap_and_score(5, 1, TEAMS, 1, 8)
        saved = customs_cog.service.set_game_teams.await_args.args
    assert Slot(8, 1) in out.blue and Slot(1, 3) in out.red
    assert saved[:2] == (5, 1)
    cog.client.evaluate.assert_awaited_once()


async def test_double_win_click_records_once(cog):
    with svc(get=custom_row(status="teams"), games=[game()], claim_winner=False, set_lolamis_game_id=None):
        await cog.handle(interaction(), "win", 5, 0)
    cog.client.record_game.assert_not_called()


async def test_win_records_in_lolamis_and_keeps_its_id(cog):
    with svc(get=custom_row(status="teams"), games=[game()], claim_winner=True, set_lolamis_game_id=None):
        await cog.handle(interaction(user_id=MEMBER, mod=True), "win", 5, 1)
        customs_cog.service.set_lolamis_game_id.assert_awaited_once_with(5, 1, 77)
    assert cog.client.record_game.await_args.args[1] == "red"


async def test_lolamis_down_after_claim_leaves_a_retryable_state(cog):
    cog.client.record_game.side_effect = LolAmisError("down")
    i = interaction()
    with svc(get=custom_row(status="teams"), games=[game()], claim_winner=True, set_lolamis_game_id=None):
        await cog.handle(i, "win", 5, 0)
        customs_cog.service.set_lolamis_game_id.assert_not_called()
    assert "corriger" in i.followup.send.await_args.args[0]


async def test_next_game_needs_a_winner_first(cog):
    with svc(get=custom_row(status="teams"), games=[game()], signups=signups(10), add_game=2):
        await cog.handle(interaction(), "next", 5, None)
        customs_cog.service.add_game.assert_not_called()


async def test_next_game_uses_current_roster_with_waitlist_promoted_after_a_withdrawal(cog):
    roster = [s for s in signups(11) if s["person_id"] != 3]  # #3 left; #10 moves up
    with svc(get=custom_row(status="teams"), games=[game(winner="blue")], signups=roster, add_game=2):
        await cog.handle(interaction(), "next", 5, None)
    assert cog.client.generate.await_args.args[0] == [0, 1, 2, 4, 5, 6, 7, 8, 9, 10]


async def test_end_closes_as_done(cog):
    with svc(get=custom_row(status="teams"), end=True):
        await cog.handle(interaction(), "end", 5, None)
        customs_cog.service.end.assert_awaited_once_with(5, "done")


async def test_teams_card_shows_balance_and_score(cog):
    with svc(get=custom_row(status="teams"), games=[game(winner="blue"), game(number=2)]):
        cog._persons_at = float("inf")  # cached names, no lol-amis call
        embed, view = await cog.render(5)
    assert "52 / 48" in embed.description and "1 – 0" in embed.description
    assert view is not None
