from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.betting.providers.pandascore import LEAGUES


def test_every_followed_league_has_a_channel():
    """The mapping and the poller's league list are two halves of one decision.

    A league added to LEAGUES but not here doesn't error — it silently pools into
    International, which is exactly the class of quiet mis-routing the unmatched-league
    warning was added to stop. Drift is only invisible until someone looks.
    """
    unrouted = [name for name in LEAGUES if name not in service._LOL_CATEGORIES]
    assert unrouted == [], f"leagues followed but not routed: {unrouted}"


def test_football_is_one_category_whatever_the_competition():
    # Deliberately unlike LoL: one board for the lot.
    for competition in ("FIFA World Cup", "UEFA Champions League", "Ligue 1"):
        assert service.category_for("football", competition) == service.CATEGORY_FOOT


def test_lol_splits_by_league():
    assert service.category_for("lol", "LEC") == service.CATEGORY_LEC
    assert service.category_for("lol", "LCK") == service.CATEGORY_LCK
    assert service.category_for("lol", "World Championship") == service.CATEGORY_INTER
    assert service.category_for("lol", "Mid-Season Invitational") == service.CATEGORY_INTER


def test_the_lfl_shares_the_lec_channel():
    # Both European: one channel whose board says "LEC · LFL", rather than a sixth channel.
    assert service.category_for("lol", "LFL") == service.CATEGORY_LEC
    assert set(service.competitions_in_category(service.CATEGORY_LEC)) == {"LEC", "LFL"}


def test_community_bets_have_their_own_category():
    # `competition` holds the question text for a custom bet, so it must never be a routing key.
    assert service.category_for("custom", "Qui gagne le scrim ?") == service.CATEGORY_CUSTOM


def test_an_unknown_lol_league_still_lands_somewhere():
    # A card in a slightly odd channel is a nuisance; a card that never posts loses a market.
    assert service.category_for("lol", "Some New Split") == service.CATEGORY_INTER


def test_every_category_has_a_label():
    # The labels feed /setup's confirmation and the pinned board title.
    assert all(cat in service.CATEGORY_LABELS for cat in service.CATEGORIES)


@pytest.mark.asyncio
async def test_a_category_with_no_channel_falls_back_to_the_betting_channel():
    # This is what keeps routing optional: a guild that never ran the new /setup form behaves
    # exactly as it did before, instead of dropping every card on the floor.
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[None, {"channel_id": 500}])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        assert await service.get_channel_for_category(1, service.CATEGORY_LCK) == 500


@pytest.mark.asyncio
async def test_a_routed_category_does_not_hit_the_fallback():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"channel_id": 77})
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        assert await service.get_channel_for_category(1, service.CATEGORY_LCK) == 77

    assert pool.fetchrow.await_count == 1  # no second query for the default


@pytest.mark.asyncio
async def test_setup_replaces_the_routing_rather_than_merging_it():
    # /setup is re-runnable and its arguments are the whole truth: a category left out of a
    # re-run is one the admin dropped, and must not keep routing to a stale channel.
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.set_category_channels(1, {service.CATEGORY_FOOT: 10})

    statements = [call[0][0] for call in conn.execute.await_args_list]
    assert statements[0].strip().startswith("DELETE")
    assert "INSERT INTO betting_channels" in statements[1]
