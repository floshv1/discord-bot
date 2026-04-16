import datetime

import discord

from bot.cogs.suggestions.cog import build_suggestion_embed

_NOW = datetime.datetime(2026, 4, 16, tzinfo=datetime.UTC)


def test_embed_title_feature():
    embed = build_suggestion_embed(1, "feature", "Add music bot", "open", 123, _NOW)
    assert embed.title == "#1 · ✨ New Feature"


def test_embed_title_improvement():
    embed = build_suggestion_embed(5, "improvement", "Better logs", "open", 123, _NOW)
    assert embed.title == "#5 · 🔧 Improvement"


def test_embed_description():
    embed = build_suggestion_embed(1, "feature", "My suggestion text", "open", 123, _NOW)
    assert embed.description == "My suggestion text"


def test_embed_color_open():
    embed = build_suggestion_embed(1, "feature", "x", "open", 1, _NOW)
    assert embed.color == discord.Color.blurple()


def test_embed_color_accepted():
    embed = build_suggestion_embed(1, "feature", "x", "accepted", 1, _NOW)
    assert embed.color == discord.Color.green()


def test_embed_color_rejected():
    embed = build_suggestion_embed(1, "feature", "x", "rejected", 1, _NOW)
    assert embed.color == discord.Color.red()


def test_embed_color_implemented():
    embed = build_suggestion_embed(1, "feature", "x", "implemented", 1, _NOW)
    assert embed.color == discord.Color.purple()


def test_embed_footer_contains_status():
    embed = build_suggestion_embed(1, "feature", "x", "accepted", 1, _NOW)
    assert "[ACCEPTED]" in embed.footer.text


def test_embed_footer_contains_date():
    embed = build_suggestion_embed(1, "feature", "x", "open", 1, _NOW)
    assert "16 Apr 2026" in embed.footer.text


def test_embed_author_field():
    embed = build_suggestion_embed(1, "feature", "x", "open", 999888777, _NOW)
    assert any(f.value == "<@999888777>" for f in embed.fields)
