"""Regressions found by the final review of the customs feature."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.customs import cog as customs_cog
from bot.cogs.customs import service
from bot.cogs.customs.cog import DOWN
from bot.cogs.customs.lolamis import LolAmisError, Person, Slot, Teams

ORGANISER, MEMBER, OTHER = 1, 2, 3
TEAMS = Teams(tuple(Slot(i, i) for i in range(5)), tuple(Slot(5 + i, i) for i in range(5)), 0.5)


def custom_row(**kw):
    row = {
        "id": 5,
        "guild_id": 10,
        "organiser_id": ORGANISER,
        "channel_id": 20,
        "message_id": 30,
        "mode": "fixed",
        "start_at": None,
        "status": "teams",
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


def interaction(user_id=ORGANISER, mod=False):
    i = MagicMock()
    i.user.id = user_id
    i.user.display_name = "Neo"
    i.user.guild_permissions.manage_messages = mod
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.response.is_done = MagicMock(return_value=True)
    i.followup.send = AsyncMock()
    return i


def last_text(i):
    return i.followup.send.await_args.args[0]


@pytest.fixture
def cog():
    c = customs_cog.CustomsCog(MagicMock())
    c.client = MagicMock()
    for name in (
        "list_persons",
        "link",
        "create_person",
        "generate",
        "evaluate",
        "record_game",
        "find_game",
        "set_winner",
    ):
        setattr(c.client, name, AsyncMock())
    c.client.list_persons.return_value = []
    c.client.generate.return_value = TEAMS
    c.client.find_game.return_value = None
    return c


# 1 — swap must answer Discord before any lol-amis call (3 s window vs a 10 s timeout)
async def test_swap_defers_before_calling_lolamis(cog):
    i = interaction()

    async def slow_list():
        assert i.response.defer.await_count == 1, "lol-amis called before the interaction was acknowledged"
        return []

    cog.client.list_persons.side_effect = slow_list
    with (
        patch.object(service, "get", AsyncMock(return_value=custom_row())),
        patch.object(service, "games", AsyncMock(return_value=[game()])),
    ):
        await cog.handle(i, "swap", 5, None)
    cog.client.list_persons.assert_awaited()


# 2 — picker callbacks run outside handle(): their failures must still reach the member
async def test_picker_failure_reaches_the_member(cog):
    i = interaction(user_id=MEMBER)
    await cog._ensure_linked(i, AsyncMock())
    picker = i.followup.send.await_args.kwargs["view"]
    cog.client.list_persons.side_effect = LolAmisError("down")
    pick = interaction(user_id=MEMBER)
    pick.response.is_done = MagicMock(return_value=False)
    await picker.on_pick(pick, 7)
    sent = pick.followup.send.await_args or pick.response.send_message.await_args
    assert DOWN in sent.args[0]


async def test_swap_same_player_twice_is_answered_not_raised(cog):
    i = interaction()
    cog._persons_at = float("inf")
    with (
        patch.object(service, "get", AsyncMock(return_value=custom_row())),
        patch.object(service, "games", AsyncMock(return_value=[game()])),
    ):
        await cog.handle(i, "swap", 5, None)
    picker = i.followup.send.await_args.kwargs["view"]
    pick = interaction()
    await picker.on_swap(pick, 1, 1)
    assert pick.followup.send.await_args is not None


async def test_relink_restores_the_old_link_when_the_new_one_fails(cog):
    cog.client.list_persons.return_value = [Person(4, "Old", "", str(MEMBER)), Person(7, "New", "", None)]
    cog.client.link.side_effect = [None, LolAmisError("down"), None]
    with pytest.raises(LolAmisError):
        await cog.link_member(interaction(user_id=MEMBER), MEMBER, 7)
    assert cog.client.link.await_args_list[-1].args == (4, str(MEMBER))


# 3 — a double click on Générer / Partie suivante must not open two games
async def test_add_game_only_when_no_game_is_undecided():
    conn = AsyncMock()
    conn.fetchval.return_value = None
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    with patch.object(service, "get_pool", return_value=pool):
        assert await service.add_game(5, TEAMS.to_json()) is None
    sql = " ".join(c.args[0] for c in conn.fetchval.call_args_list)
    assert "NOT EXISTS" in sql and "winner IS NULL" in sql


async def test_second_generate_click_is_told_not_duplicated(cog):
    i = interaction()
    signups = [{"id": n, "user_id": 100 + n, "person_id": n} for n in range(10)]
    with patch.multiple(
        service,
        get=AsyncMock(return_value=custom_row(status="signup")),
        signups=AsyncMock(return_value=signups),
        add_game=AsyncMock(return_value=None),
    ):
        await cog.handle(i, "gen", 5, None)
    assert "déjà" in last_text(i)


# 4 — after a withdrawal with no waitlist, the evening must not deadlock at 9
async def test_decided_teams_card_still_lets_people_join(cog):
    cog._persons_at = float("inf")
    with patch.multiple(
        service, get=AsyncMock(return_value=custom_row()), games=AsyncMock(return_value=[game("blue")])
    ):
        _, view = await cog.render(5)
    ids = [c.custom_id for c in view.children]
    assert "customs:join:5" in ids and "customs:guest:5" in ids


# 5 — « Je ne suis pas dans la liste » racing itself creates one player
async def test_concurrent_not_listed_creates_once(cog):
    made = []

    async def create(name, did):
        await asyncio.sleep(0.01)
        made.append(did)
        cog.client.list_persons.return_value = [Person(8, name, "", did)]
        return Person(8, name, "", did)

    cog.client.create_person.side_effect = create
    i = interaction(user_id=MEMBER)
    a, b = await asyncio.gather(cog.not_listed(i, MEMBER), cog.not_listed(i, MEMBER))
    assert a == b == 8 and made == [str(MEMBER)]


# 6 — nobody silently takes over a player someone else is linked to
async def test_cannot_take_a_player_linked_to_someone_else(cog):
    cog.client.list_persons.return_value = [Person(7, "B", "", str(OTHER))]
    i = interaction(user_id=MEMBER)
    assert await cog.link_member(i, MEMBER, 7) is None
    cog.client.link.assert_not_called()
    assert "B" in last_text(i)


async def test_a_mod_may_reassign_a_linked_player(cog):
    cog.client.list_persons.return_value = [Person(7, "B", "", str(OTHER))]
    assert await cog.link_member(interaction(user_id=MEMBER, mod=True), MEMBER, 7) == 7


# 7 — a retry after a timeout that actually committed must not record the game twice
async def test_retry_finds_the_game_lolamis_already_has(cog):
    cog.client.find_game.return_value = 55
    with patch.multiple(service, set_lolamis_game_id=AsyncMock(), games=AsyncMock(return_value=[game("blue")])):
        ok = await cog._push_result(5, 1, TEAMS, "blue", None)
        service.set_lolamis_game_id.assert_awaited_once_with(5, 1, 55)
    assert ok
    cog.client.record_game.assert_not_called()


# CI: on Linux time.monotonic() counts from boot, so a runner up < 60 s made the never-filled cache
# look fresh — the bot skipped lol-amis entirely (empty picker, "?" names right after a reboot).
async def test_never_loaded_cache_is_stale_even_right_after_boot(cog):
    cog.client.list_persons.return_value = [Person(7, "Adri", "", None)]
    with patch.object(customs_cog.time, "monotonic", return_value=5.0):
        persons = await cog.persons()
    assert 7 in persons
    cog.client.list_persons.assert_awaited_once()
