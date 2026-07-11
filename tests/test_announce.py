from unittest.mock import MagicMock

from bot.cogs.announce.embeds import build_announcement_embed
from bot.cogs.announce.service import allowed_mentions_for


def _role(is_default: bool = False):
    role = MagicMock()
    role.is_default.return_value = is_default
    return role


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
