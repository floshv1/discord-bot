"""Linked players show as Discord mentions on cards, and their lol-amis name follows their Discord tag."""

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.customs import cog as customs_cog
from bot.cogs.customs import service
from bot.cogs.customs.lolamis import Person, Slot, Teams

TEAMS = Teams(tuple(Slot(i, i) for i in range(5)), tuple(Slot(5 + i, i) for i in range(5)), 0.5)


def custom_row(**kw):
    row = {
        "id": 5,
        "guild_id": 10,
        "organiser_id": 1,
        "channel_id": 20,
        "message_id": 30,
        "mode": "fixed",
        "start_at": dt.datetime(2026, 11, 22, 20, tzinfo=dt.UTC),
        "status": "signup",
    }
    row.update(kw)
    return row


@pytest.fixture
def cog():
    c = customs_cog.CustomsCog(MagicMock())
    c.client = MagicMock()
    c.client.list_persons = AsyncMock()
    c.client.rename = AsyncMock()
    c._persons_at = float("inf")
    return c


async def test_signup_card_shows_linked_players_as_mentions_and_guests_by_name(cog):
    cog._persons = {1: Person(1, "Adri", "Or II", "111111111111111111"), 2: Person(2, "Invité", "", None)}
    signups = [{"id": 1, "user_id": 111111111111111111, "person_id": 1}, {"id": 2, "user_id": None, "person_id": 2}]
    with patch.multiple(
        service,
        get=AsyncMock(return_value=custom_row()),
        games=AsyncMock(return_value=[]),
        signups=AsyncMock(return_value=signups),
    ):
        embed, _ = await cog.render(5)
    text = embed.fields[0].value
    assert "<@111111111111111111>" in text and "Adri" not in text
    assert "Invité" in text


async def test_teams_card_uses_mentions_too(cog):
    cog._persons = {i: Person(i, f"P{i}", "", str(10**17 + i)) for i in range(10)}
    game = {"number": 1, "teams": json.dumps(TEAMS.to_json()), "winner": None, "lolamis_game_id": None}
    with patch.multiple(
        service, get=AsyncMock(return_value=custom_row(status="teams")), games=AsyncMock(return_value=[game])
    ):
        embed, _ = await cog.render(5)
    assert f"<@{10**17}>" in embed.fields[0].value and "P0" not in embed.fields[0].value


def _guild(members):
    g = MagicMock()
    g.get_member.side_effect = lambda uid: members.get(uid)
    return g


def _member(name):
    m = MagicMock()
    m.name = name
    return m


async def test_sync_renames_to_the_discord_tag_and_keeps_the_old_name_as_alias(cog):
    cog.client.list_persons.return_value = [Person(1, "Adri", "", "111", aliases=("akjx1",))]
    await cog.sync_names(_guild({111: _member("adri_lol")}))
    cog.client.rename.assert_awaited_once_with(1, "adri_lol", ["akjx1", "Adri"])


async def test_sync_leaves_matching_unlinked_and_departed_players_alone(cog):
    cog.client.list_persons.return_value = [
        Person(1, "adri_lol", "", "111"),  # already the tag
        Person(2, "Mehdi", "", None),  # not linked
        Person(3, "Neo", "", "333"),  # left the server
    ]
    await cog.sync_names(_guild({111: _member("adri_lol")}))
    cog.client.rename.assert_not_called()


async def test_sync_does_not_duplicate_an_existing_alias(cog):
    cog.client.list_persons.return_value = [Person(1, "Adri", "", "111", aliases=("Adri",))]
    await cog.sync_names(_guild({111: _member("adri_lol")}))
    cog.client.rename.assert_awaited_once_with(1, "adri_lol", ["Adri"])


async def test_rename_patches_name_and_aliases():
    from tests.test_customs_lolamis import FakeResponse, client  # reuse the fake HTTP session

    c, s = client(FakeResponse(200, {"id": 1, "name": "adri_lol", "rank_label": "", "discord_id": "111"}))
    await c.rename(1, "adri_lol", ["Adri"])
    assert s.calls == [("PATCH", "http://lol-amis:8000/api/persons/1", {"name": "adri_lol", "aliases": ["Adri"]})]
