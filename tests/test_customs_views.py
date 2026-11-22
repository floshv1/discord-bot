"""Signup card behaviour: linking, joining, the slot vote and who may press what."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.customs import cog as customs_cog
from bot.cogs.customs import embeds
from bot.cogs.customs.lolamis import LinkTaken, Person
from bot.cogs.customs.service import AlreadyOpen
from bot.cogs.customs.views import CustomButton, PersonPicker

ORGANISER, MEMBER = 1, 2


def custom_row(**kw):
    row = {
        "id": 5,
        "guild_id": 10,
        "organiser_id": ORGANISER,
        "channel_id": 20,
        "message_id": 30,
        "mode": "fixed",
        "start_at": dt.datetime(2026, 11, 22, 20, tzinfo=dt.UTC),
        "status": "signup",
        "created_at": dt.datetime(2026, 11, 22, 12, tzinfo=dt.UTC),
    }
    row.update(kw)
    return row


def interaction(user_id=MEMBER, mod=False):
    i = MagicMock()
    i.user.id = user_id
    i.user.display_name = "Neo"
    i.user.guild_permissions.manage_messages = mod
    i.guild_id = 10
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.followup.send = AsyncMock()
    return i


@pytest.fixture
def cog():
    bot = MagicMock()
    bot.config.lolamis_url = "http://lol-amis:8000"
    c = customs_cog.CustomsCog(bot)
    c.client = MagicMock()
    c.client.list_persons = AsyncMock(return_value=[Person(7, "Adri", "Bronze I", None)])
    c.client.link = AsyncMock()
    c.client.create_person = AsyncMock(return_value=Person(8, "Neo", "", str(MEMBER)))
    return c


def sent_text(i):
    call = i.followup.send.await_args or i.response.send_message.await_args
    return (call.args[0] if call.args else call.kwargs.get("content")) or ""


def test_button_custom_id_round_trips():
    assert CustomButton.make_id("join", 5) == "customs:join:5"
    assert CustomButton.make_id("vote", 5, 1700000000) == "customs:vote:5:1700000000"
    m = CustomButton.__discord_ui_compiled_template__.fullmatch("customs:win:12:1")
    assert (m["action"], m["cid"], m["arg"]) == ("win", "12", "1")


def test_signup_embed_lists_players_then_waitlist():
    names = [f"P{i}" for i in range(12)]
    e = embeds.build_signup_embed(custom_row(), players=names[:10], waiting=names[10:], votes=None)
    text = "\n".join(f.value for f in e.fields) + (e.description or "")
    assert "10/10" in "".join(f.name for f in e.fields)
    assert "P11" in text and "P0" in text


def test_vote_embed_shows_each_slot_count():
    a = dt.datetime(2026, 11, 22, 19, tzinfo=dt.UTC)
    e = embeds.build_signup_embed(custom_row(mode="vote", start_at=None), players=[], waiting=[], votes=[(a, 8)])
    assert "8" in "".join(f.value for f in e.fields)


async def test_join_without_a_link_opens_the_picker(cog):
    i = interaction()
    with patch.object(customs_cog.service, "get", AsyncMock(return_value=custom_row())):
        await cog.handle(i, "join", 5, None)
    assert isinstance(i.followup.send.await_args.kwargs["view"], PersonPicker)


async def test_join_with_a_link_signs_up_directly(cog):
    cog.client.list_persons.return_value = [Person(7, "Adri", "Bronze I", str(MEMBER))]
    i = interaction()
    add = AsyncMock(return_value=True)
    with (
        patch.object(customs_cog.service, "get", AsyncMock(return_value=custom_row())),
        patch.object(customs_cog.service, "add_signup", add),
    ):
        await cog.handle(i, "join", 5, None)
    add.assert_awaited_once_with(5, MEMBER, 7)


async def test_link_taken_names_the_holder(cog):
    cog.client.link.side_effect = LinkTaken(9, "Mehdi")
    i = interaction()
    ok = await cog.link_member(i, MEMBER, 7)
    assert ok is None and "Mehdi" in sent_text(i)


async def test_not_listed_twice_creates_one_person(cog):
    i = interaction()
    first = await cog.not_listed(i, MEMBER)
    cog.client.list_persons.return_value = [Person(8, "Neo", "", str(MEMBER))]
    second = await cog.not_listed(i, MEMBER)
    assert first == second == 8
    cog.client.create_person.assert_awaited_once()


async def test_only_the_organiser_validates_a_slot(cog):
    i = interaction(user_id=MEMBER)
    claim = AsyncMock()
    with (
        patch.object(customs_cog.service, "get", AsyncMock(return_value=custom_row(mode="vote", start_at=None))),
        patch.object(customs_cog.service, "claim_vote_win", claim),
    ):
        await cog.handle(i, "validate", 5, 1700000000)
    claim.assert_not_called()
    assert "organisateur" in sent_text(i).lower()


async def test_launch_when_one_is_open_links_to_it(cog):
    i = interaction()
    i.guild.get_channel.return_value = MagicMock(id=20)
    with (
        patch.object(customs_cog.service, "get_config", AsyncMock(return_value={"channel_id": 20, "role_id": None})),
        patch.object(customs_cog.service, "create_custom", AsyncMock(side_effect=AlreadyOpen(4))),
        patch.object(customs_cog.service, "get", AsyncMock(return_value=custom_row(id=4, message_id=44))),
    ):
        await cog.launch(i, heure="21h", creneaux=None)
    assert "/44" in sent_text(i)


async def test_lolamis_down_gets_a_clear_message(cog):
    from bot.cogs.customs.lolamis import LolAmisError

    cog.client.list_persons.side_effect = LolAmisError("down")
    i = interaction()
    with patch.object(customs_cog.service, "get", AsyncMock(return_value=custom_row())):
        await cog.handle(i, "join", 5, None)
    assert "ne répond pas" in sent_text(i)
