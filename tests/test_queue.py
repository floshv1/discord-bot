import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

from bot.cogs.queue import service
from bot.cogs.queue.embeds import build_queue_embed
from bot.cogs.queue.service import can_close_queue, parse_start_time

PARIS_TZ = ZoneInfo("Europe/Paris")


def _member(user_id: int, in_lane: bool = False, cant_attend: bool = False) -> dict:
    return {"user_id": user_id, "in_lane": in_lane, "cant_attend": cant_attend}


def _queue(status: str = "open", player_count: int = 5, start_time=None, creator_user_id=None) -> dict:
    return {
        "name": "lol",
        "status": status,
        "player_count": player_count,
        "start_time": start_time,
        "creator_user_id": creator_user_id,
    }


def test_queue_embed_open_title():
    embed = build_queue_embed(_queue(status="open"), [_member(1)])
    assert embed.title == "🎮 LOL"
    assert embed.color == discord.Color.blurple()


def test_queue_embed_filled_title():
    embed = build_queue_embed(_queue(status="filled"), [_member(1)])
    assert embed.title == "✅ LOL — Lobby ready!"
    assert embed.color == discord.Color.green()


def test_queue_embed_done_and_cancelled():
    assert build_queue_embed(_queue(status="done"), []).title == "🏁 LOL — Game over!"
    assert build_queue_embed(_queue(status="cancelled"), []).title == "❌ LOL — Cancelled"


def test_queue_embed_player_count_excludes_lane_and_cant():
    members = [_member(1), _member(2), _member(3, in_lane=True), _member(4, cant_attend=True)]
    embed = build_queue_embed(_queue(player_count=2), members)
    players_field = next(f for f in embed.fields if f.name.startswith("Players"))
    assert players_field.name == "Players — 2/2"


def test_queue_embed_waiting_and_cant_fields():
    members = [_member(1), _member(2, in_lane=True), _member(3, cant_attend=True)]
    embed = build_queue_embed(_queue(player_count=1), members)
    names = [f.name for f in embed.fields]
    assert any(n.startswith("Waiting") for n in names)
    assert "Can't attend" in names


def test_queue_embed_no_players_placeholder():
    embed = build_queue_embed(_queue(), [])
    players_field = next(f for f in embed.fields if f.name.startswith("Players"))
    assert players_field.value == "*No players yet*"


def test_queue_embed_start_time_field():
    when = datetime.datetime(2026, 6, 18, 19, 0, tzinfo=datetime.UTC)
    embed = build_queue_embed(_queue(start_time=when), [_member(1)])
    assert any(f.name == "Start time" for f in embed.fields)


def test_queue_embed_host_field():
    embed = build_queue_embed(_queue(creator_user_id=42), [_member(42)])
    assert any(f.name == "Host" and f.value == "<@42>" for f in embed.fields)


def test_parse_start_time_future_today():
    dt = parse_start_time("23:59")
    assert dt is not None
    assert dt.tzinfo == datetime.UTC


def test_parse_start_time_rolls_to_tomorrow_when_past():
    now_paris = datetime.datetime.now(tz=PARIS_TZ)
    past = (now_paris - datetime.timedelta(hours=1)).strftime("%H:%M")
    dt = parse_start_time(past)
    assert dt is not None
    # A time earlier than now should be scheduled for the future (today or tomorrow).
    assert dt > datetime.datetime.now(tz=datetime.UTC)


def test_parse_start_time_invalid():
    assert parse_start_time("not-a-time") is None
    assert parse_start_time("25:00") is None


# --- who may close a queue -------------------------------------------------


def test_host_can_close_their_own_queue():
    assert can_close_queue(_queue(creator_user_id=42), user_id=42, is_mod=False) is True


def test_mod_can_close_anyones_queue():
    assert can_close_queue(_queue(creator_user_id=42), user_id=99, is_mod=True) is True


def test_joiner_cannot_close_someone_elses_queue():
    # A drive-by joiner must not be able to kill the host's lobby.
    assert can_close_queue(_queue(creator_user_id=42), user_id=99, is_mod=False) is False


# --- ad-hoc presets must not pollute the shared panel ----------------------


@pytest.mark.asyncio
async def test_list_presets_only_returns_panel_presets():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.queue.service.get_pool", return_value=pool):
        await service.list_presets(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    assert "on_panel" in sql, "the panel must not list ad-hoc, member-created presets"


@pytest.mark.asyncio
async def test_upsert_preset_defaults_to_on_panel():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": 1, "name": "lol", "player_count": 5})
    with patch("bot.cogs.queue.service.get_pool", return_value=pool):
        await service.upsert_preset(guild_id=1, name="lol", player_count=5)

    assert pool.execute.call_args[0][4] is True


@pytest.mark.asyncio
async def test_upsert_preset_can_create_an_ad_hoc_preset():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": 1, "name": "valorant", "player_count": 5})
    with patch("bot.cogs.queue.service.get_pool", return_value=pool):
        await service.upsert_preset(guild_id=1, name="valorant", player_count=5, on_panel=False)

    assert pool.execute.call_args[0][4] is False
