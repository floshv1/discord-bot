from unittest.mock import MagicMock

from bot.cogs.announce.embeds import build_announcement_embed
from bot.cogs.announce.service import allowed_mentions_for, ping_content


def _role(is_default: bool = False):
    role = MagicMock()
    role.is_default.return_value = is_default
    role.mention = "<@&123>"
    return role


# --- what actually goes in the message content ------------------------------


def test_no_ping_puts_nothing_in_the_content():
    assert ping_content(None) is None


def test_a_normal_role_is_mentioned_the_usual_way():
    assert ping_content(_role()) == "<@&123>"


def test_everyone_must_be_the_literal_string():
    # discord.py's Role.mention returns `<@&{id}>` for EVERY role, @everyone included.
    # Discord does not notify anyone for that form — pinging everyone requires the literal
    # text. Using .mention here would have silently pinged nobody.
    assert ping_content(_role(is_default=True)) == "@everyone"


# --- the security-relevant one: who can this announcement ping? -------------


def test_no_ping_means_nothing_can_be_mentioned():
    # The default. An announcement must be silent unless the admin explicitly opted in.
    am = allowed_mentions_for(None)
    assert am.everyone is False
    assert am.roles is False
    assert am.users is False


def test_pinging_a_role_allows_only_that_role():
    role = _role()
    am = allowed_mentions_for(role)
    assert am.roles == [role]
    assert am.everyone is False
    assert am.users is False


def test_pinging_everyone_needs_the_everyone_flag():
    # @everyone is the guild's default role, and Discord gates it behind `everyone`,
    # not `roles` — passing it as a role alone would silently fail to ping.
    am = allowed_mentions_for(_role(is_default=True))
    assert am.everyone is True
    assert am.users is False


# --- the embed --------------------------------------------------------------


def test_embed_carries_the_title_and_body():
    embed = build_announcement_embed("🤖 Mise à jour", "## ✨ Nouveau\n`/help`", author_name="flosh")
    assert embed.title == "🤖 Mise à jour"
    assert "## ✨ Nouveau" in embed.description
    assert "`/help`" in embed.description


def test_embed_credits_the_author():
    embed = build_announcement_embed("t", "b", author_name="flosh")
    assert "flosh" in embed.footer.text


def test_embed_keeps_newlines_intact():
    # The whole reason this feature uses a modal — the body is multi-line.
    body = "ligne 1\nligne 2\n\nligne 4"
    embed = build_announcement_embed("t", body, author_name="a")
    assert embed.description == body
