import datetime
from zoneinfo import ZoneInfo

import discord

from bot.cogs.queue.embeds import build_queue_embed
from bot.cogs.queue.service import parse_start_time

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
