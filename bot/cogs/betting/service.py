from __future__ import annotations

import datetime
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg
from loguru import logger

from bot.cogs.currency import service as currency_service
from bot.cogs.currency.service import HOUSE_USER_ID
from bot.db.client import get_pool

PlaceBetResult = Literal["ok", "closed", "insufficient_funds", "invalid_amount", "other_outcome"]

# What it costs to open a community bet. See charge_creation_fee for why a fee and not a cap.
CREATE_FEE = 100

# What the house stakes on each side of a market when it opens. See seed_market.
HOUSE_SEED_PER_OUTCOME = 250

# ...and what it stakes on a *custom* one. Lower, and not by taste: a custom market's winner is
# declared by a member, so anyone who can bet on it can also decide it. The most a bettor can
# ever take off the house on a market is strictly less than the seed of the losing side, so
# keeping that seed under the fee charged to open the market makes `create -> stake -> resolve`
# structurally unprofitable — in solo, and in collusion. This inequality *is* the fix:
#
#     CUSTOM_SEED_PER_OUTCOME <= CREATE_FEE
#
# Provider markets keep the fat seed: their result comes from an API nobody here can bend.
CUSTOM_SEED_PER_OUTCOME = 100

# How long after **kickoff** (start_time, not the final whistle) before someone is reminded to
# settle a market, and how often the nudge repeats until they do. One ignorable ping isn't
# enough when other people's coins are stuck behind it.
#
# Note this fires while a football match (~1h50-2h05) is often still being played — which is
# why claim_settle_reminders takes `exclude_ids` and the providers report a "pending" status.
# Raising the window would paper over that; a longer format would just move the same bug.
RESOLVE_REMINDER_AFTER = datetime.timedelta(hours=2)
RESOLVE_REMINDER_REPEAT = datetime.timedelta(hours=24)

# A market still locked this long after kickoff is never going to be settled by hand. Refund
# it rather than leave the stakes frozen forever — a refund cannot make anyone worse off.
STUCK_VOID_AFTER = datetime.timedelta(days=7)


@dataclass
class BetOutcome:
    """Result of attempting a bet.

    `existing_outcome` is set only for "other_outcome", so the caller can name the side the
    user is already on. `topped_up` distinguishes adding to a position from opening one.
    """

    status: PlaceBetResult
    new_balance: int | None = None
    existing_outcome: str | None = None
    topped_up: bool = False


# ---------------------------------------------------------------------------
# Pure payout math
# ---------------------------------------------------------------------------


def settle_parimutuel(bets: Sequence[Mapping], winner: str) -> dict[int, int]:
    """Compute payouts for a parimutuel market. Returns {bet_id: payout} for winning bets only.

    Winners split the entire losing pool proportional to their stake, on top of getting
    their own stake back. A market where nobody bet the winning outcome pays out nothing.
    """
    total_pool = sum(b["amount"] for b in bets)
    winning_bets = [b for b in bets if b["outcome"] == winner]
    winning_pool = sum(b["amount"] for b in winning_bets)
    if winning_pool == 0:
        return {}
    losing_pool = total_pool - winning_pool
    return {b["id"]: b["amount"] + (b["amount"] * losing_pool) // winning_pool for b in winning_bets}


def player_bets(bets: Sequence[Mapping]) -> list[Mapping]:
    """Just the bets real people placed.

    The house's seed is a bet like any other as far as the payout maths is concerned — that
    is the whole point, it wins and loses on the same terms. But it is *not* interesting as
    a fact about the server: a card that said "1 backer" because the house is on that side,
    or a P&L board topped by the bank, would be lying about what the members did. So the
    money view of a market includes the house and the people view does not.
    """
    return [b for b in bets if b.get("user_id") != HOUSE_USER_ID]


def house_stakes(bets: Sequence[Mapping]) -> dict[str, int]:
    """What the house has riding on each outcome — its line, as shown on the card."""
    stakes: dict[str, int] = {}
    for b in bets:
        if b.get("user_id") == HOUSE_USER_ID:
            stakes[b["outcome"]] = stakes.get(b["outcome"], 0) + b["amount"]
    return stakes


def seed_for_market(market: Mapping) -> int:
    """What the house stakes per outcome on this market. See CUSTOM_SEED_PER_OUTCOME."""
    return CUSTOM_SEED_PER_OUTCOME if market["provider"] == "custom" else HOUSE_SEED_PER_OUTCOME


def house_pnl(bets: Sequence[Mapping]) -> int:
    """What a settled market made (+) or cost (-) the house, in coins.

    The house is paid twice: through its own winning bets, and as the residual claimant of
    whatever integer division leaves in the pool. Both count, or the number lies.
    """
    paid = sum(b["payout"] or 0 for b in bets)
    residual = sum(b["amount"] for b in bets) - paid
    house = [b for b in bets if b["user_id"] == HOUSE_USER_ID]
    return sum(b["payout"] or 0 for b in house) + residual - sum(b["amount"] for b in house)


def creator_holds_gavel(market: Mapping, bets: Sequence[Mapping]) -> bool:
    """Whether the creator of a custom bet is still the one who settles it.

    They are, right up until they stake on it — at which point the gavel passes to the mods.
    That is the creator's own free choice, made by betting rather than by answering one more
    question in /bet create: play your bet, or referee it. Not both.

    The house's seed is not a stake for this purpose: it is on every market ever opened, so
    counting it would leave no creator holding the gavel, ever.
    """
    creator = market["creator_user_id"]
    if creator is None:
        return False  # a provider match has no creator — the mods have it
    return not any(b["user_id"] == creator for b in bets)


def may_settle(market: Mapping, bets: Sequence[Mapping], user_id: int, *, is_mod: bool) -> bool:
    """Whether this person may /bet resolve or /bet cancel this market.

    Nobody settles a custom bet they have money on — not the creator, not a moderator. Its
    winner is *declared*, not observed, so anyone who both stakes and settles can simply hand
    themselves the pool, and that pool is other members' coins.

    A provider match is different and deliberately looser: any mod can settle one even having
    bet on it. Its result is public, so a mod who called it wrong gets caught immediately, and
    the ability to settle by hand is the only escape hatch when an API never reports a result.
    Take it away and everyone's stakes stay frozen forever.

    /bet cancel is gated exactly like /bet resolve: once the real result is known, a refund is
    an exit for whoever was about to lose, and it is *not* neutral for whoever was about to win.
    """
    if market["provider"] != "custom":
        return is_mod
    if any(b["user_id"] == user_id for b in bets):
        return False
    return is_mod or market["creator_user_id"] == user_id


def pool_totals(bets: Sequence[Mapping]) -> dict[str, dict[str, int]]:
    """Aggregate bets by outcome: {outcome: {"total", "count", "backers"}}.

    `count` is the number of bets; `backers` the number of distinct people. They differ when
    someone tops up an existing position, and it's `backers` that people actually care about
    ("12 people are on France"), the way Twitch counts predictors.
    """
    totals: dict[str, dict[str, int]] = {}
    users: dict[str, set] = {}
    for b in bets:
        entry = totals.setdefault(b["outcome"], {"total": 0, "count": 0, "backers": 0})
        entry["total"] += b["amount"]
        entry["count"] += 1
        if "user_id" in b:
            users.setdefault(b["outcome"], set()).add(b["user_id"])
    for outcome, entry in totals.items():
        entry["backers"] = len(users.get(outcome, ())) or entry["count"]
    return totals


def pool_shares(bets: Sequence[Mapping]) -> dict[str, float]:
    """Each outcome's share of the total pool, 0.0-1.0 — the Twitch-style percentage split."""
    totals = pool_totals(bets)
    total_pool = sum(t["total"] for t in totals.values())
    if not total_pool:
        return {}
    return {outcome: t["total"] / total_pool for outcome, t in totals.items()}


def implied_odds(bets: Sequence[Mapping]) -> dict[str, float]:
    """Current payout multiplier per outcome — the parimutuel 'cote'.

    An outcome paying 2.5x means a 100 stake returns 250 if it wins. It falls straight out
    of the pools (total / outcome), and is exactly what settle_parimutuel would pay today.
    Outcomes with no money on them are omitted: their odds are undefined, not infinite.
    """
    totals = pool_totals(bets)
    total_pool = sum(t["total"] for t in totals.values())
    return {outcome: total_pool / t["total"] for outcome, t in totals.items() if t["total"] > 0}


def outcomes_for_market(market: Mapping) -> list[tuple[str, str]]:
    """The bettable (outcome_key, label) pairs for a market — only football can be drawn."""
    outcomes = [("home", market["home_name"])]
    if market["sport"] == "football":
        outcomes.append(("draw", "Draw"))
    outcomes.append(("away", market["away_name"]))
    return outcomes


# ---------------------------------------------------------------------------
# Routing: which channel a card belongs in
# ---------------------------------------------------------------------------

CATEGORY_FOOT = "foot"
CATEGORY_LEC = "lec"
CATEGORY_INTER = "inter"
CATEGORY_CUSTOM = "custom"

CATEGORIES = (CATEGORY_FOOT, CATEGORY_LEC, CATEGORY_INTER, CATEGORY_CUSTOM)

CATEGORY_LABELS = {
    CATEGORY_FOOT: "Foot",
    CATEGORY_LEC: "LEC",
    CATEGORY_INTER: "International",
    CATEGORY_CUSTOM: "Paris perso",
}

# LoL splits by league; football deliberately does not — one board for the lot. The key is the
# league's exact PandaScore name, the same string that lands in betting_markets.competition.
#
# Every name in pandascore.LEAGUES must appear here, or a newly-followed league silently pools
# into International. tests/test_betting_routing.py locks that: the mapping and the poller's
# league list are two halves of one decision and drift apart the moment nobody is watching.
_LOL_CATEGORIES = {
    "LEC": CATEGORY_LEC,
    "Worlds": CATEGORY_INTER,
    "Mid-Season Invitational": CATEGORY_INTER,
    "Esports World Cup": CATEGORY_INTER,
}


def category_for(sport: str, competition: str) -> str:
    """Which betting channel a market belongs in. Pure — the routing decision, in one place.

    An unrecognised LoL league falls into International rather than erroring: a card in a
    slightly odd channel is a nuisance, a card that never posts loses a market.
    """
    if sport == "custom":
        return CATEGORY_CUSTOM
    if sport == "football":
        return CATEGORY_FOOT
    return _LOL_CATEGORIES.get(competition, CATEGORY_INTER)


def category_of_market(market: Mapping) -> str:
    return category_for(market["sport"], market["competition"])


def competitions_in_category(category: str) -> list[str]:
    """What the pinned board announces as followed in a channel."""
    if category == CATEGORY_FOOT:
        return list(_FOOTBALL_COMPETITION_LABELS.values())
    if category == CATEGORY_CUSTOM:
        return []
    return [name for name, cat in _LOL_CATEGORIES.items() if cat == category]


# Display names for the football competitions the poller follows, keyed by the football-data
# code so the two can't drift: a code added to `football_data.COMPETITIONS` without a label
# here would leave the board quietly claiming to follow less than it does. Keyed rather than a
# bare list precisely so tests/test_betting_routing.py can lock it, the same way the LoL
# categories are locked against pandascore.LEAGUES. The API's own name is what ends up on a
# card; this is only for the board, which has to name them before any fixture lands.
_FOOTBALL_COMPETITION_LABELS = {
    "WC": "Coupe du Monde",
    "EC": "Euro",
    "CL": "Ligue des Champions",
    "FL1": "Ligue 1",
}


async def seed_market(market_id: int) -> bool:
    """Stake the house on every outcome of a freshly opened market. Returns whether it did.

    Without this, a market's odds are whatever the crowd happens to have staked — and on a
    small server the crowd is often one person. Backing the only outcome anyone bet on paid
    `1.00x`: your own stake, handed back to you. Betting alone was pointless, which is the
    thing members actually complained about.

    Seeding both sides gives every market a real opening line (2.00x on a two-way) and, more
    importantly, gives a lone bettor someone to win *from*. The house is an ordinary row in
    betting_bets, so settle_parimutuel needs no special case: it wins and loses on exactly the
    same terms as a member, and no coins are minted to pay anyone.

    Only an `open` market may be seeded. Seeding one that has already locked would move the
    odds under people who have already bet — they were promised the pool they could see.

    A custom market is seeded lighter than a provider one — see CUSTOM_SEED_PER_OUTCOME.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow("SELECT * FROM betting_markets WHERE id = $1 FOR UPDATE", market_id)
            if not market or market["status"] != "open":
                return False

            already_seeded = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM betting_bets WHERE market_id = $1 AND user_id = $2)",
                market_id,
                HOUSE_USER_ID,
            )
            if already_seeded:
                return False

            outcomes = outcomes_for_market(market)
            per_outcome = seed_for_market(market)
            needed = per_outcome * len(outcomes)

            # Read the house wallet directly rather than through get_balance_locked, which
            # would happily *create* it with a member's opening balance. If the house has no
            # wallet, something is wrong and quietly inventing a 1000 🪙 bank is not the fix.
            balance = await conn.fetchval(
                "SELECT balance FROM currency_wallets WHERE user_id = $1 FOR UPDATE", HOUSE_USER_ID
            )
            if balance is None:
                logger.warning(f"House wallet missing — market {market_id} opens unseeded")
                return False
            if balance < needed:
                # Broke, not broken: the market still works, it just prices itself off the
                # crowd like it used to. The losing pools it collects will refill it.
                logger.warning(f"House has {balance} 🪙, needs {needed} — market {market_id} opens unseeded")
                return False

            await currency_service.adjust(
                conn, guild_id=market["guild_id"], user_id=HOUSE_USER_ID, amount=-needed, reason="house_seed"
            )
            for key, _label in outcomes:
                await conn.execute(
                    "INSERT INTO betting_bets (market_id, user_id, outcome, amount) VALUES ($1, $2, $3, $4)",
                    market_id,
                    HOUSE_USER_ID,
                    key,
                    per_outcome,
                )
            return True


async def charge_creation_fee(*, guild_id: int, user_id: int) -> tuple[bool, int]:
    """Debit the fee for opening a community bet. Returns (charged, balance).

    Opening a bet posts a public card, and nothing else stops one person papering the
    channel with them. A fee is a better brake than a hard cap on how many bets you may
    have open: on a busy night with a dozen real events, a cap punishes the person doing
    the work, while a fee just asks them to have skin in the game.

    It goes to the house, never into the pool — a bet's pool must be exactly what bettors put
    in, or the payout maths stops being parimutuel. Paying the house rather than burning it
    is what funds the seed on the next market. Not refunded when a bet is cancelled, or
    create-and-cancel would be a free spam loop.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            balance = await currency_service.get_balance_locked(conn, guild_id=guild_id, user_id=user_id)
            if balance < CREATE_FEE:
                return False, balance
            new_balance = await currency_service.adjust(
                conn, guild_id=guild_id, user_id=user_id, amount=-CREATE_FEE, reason="bet_create_fee"
            )
            await currency_service.adjust(
                conn, guild_id=guild_id, user_id=HOUSE_USER_ID, amount=CREATE_FEE, reason="bet_create_fee"
            )
            return True, new_balance


async def create_custom_market(
    *,
    guild_id: int,
    creator_user_id: int,
    title: str,
    option_a: str,
    option_b: str,
    closes_at,
) -> int:
    """Open a user-created binary market. Its two options are stored as home_name/away_name.

    Custom markets have no provider, so the resolution ticker skips them — the creator
    (or a mod) settles them by hand with /bet resolve.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO betting_markets
            (guild_id, provider, external_id, sport, competition,
             home_name, away_name, start_time, creator_user_id)
        VALUES ($1, 'custom', $2, 'custom', $3, $4, $5, $6, $7)
        RETURNING id
        """,
        guild_id,
        str(uuid.uuid4()),
        title,
        option_a,
        option_b,
        closes_at,
        creator_user_id,
    )
    return row["id"]


async def list_settleable_markets(guild_id: int, user_id: int, *, is_mod: bool) -> list[asyncpg.Record]:
    """Every market this person is actually allowed to settle. The SQL mirrors may_settle().

    The exclusion has to happen in the query and not in Python: this feeds an autocomplete, and
    an autocomplete re-runs on every keystroke — one extra round-trip per open market would be
    a dozen queries per letter typed.

    Provider markets are included for mods even if they bet on them: their result is public,
    and settling one by hand is the only escape hatch when an API never reports a result.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT m.* FROM betting_markets m
        WHERE m.guild_id = $1
          AND m.status IN ('open', 'locked')
          AND (
                ($3 AND m.provider <> 'custom')
             OR (
                    m.provider = 'custom'
                AND ($3 OR m.creator_user_id = $2)
                AND NOT EXISTS (
                        SELECT 1 FROM betting_bets b
                        WHERE b.market_id = m.id AND b.user_id = $2
                    )
                )
          )
        ORDER BY m.start_time
        """,
        guild_id,
        user_id,
        is_mod,
    )


# ---------------------------------------------------------------------------
# Guild config
# ---------------------------------------------------------------------------


async def set_betting_channel(guild_id: int, channel_id: int, staff_channel_id: int | None = None) -> None:
    """Point the betting cards at a channel, and optionally the settlement recaps at another.

    A re-run with no staff channel clears the old one on purpose: /setup is re-runnable, and a
    stale staff channel would keep mirroring settlements into a channel the admin just
    dropped.
    """
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO betting_config (guild_id, channel_id, staff_channel_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id,
                                             staff_channel_id = EXCLUDED.staff_channel_id
        """,
        guild_id,
        channel_id,
        staff_channel_id,
    )


async def get_betting_channel(guild_id: int) -> int | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT channel_id FROM betting_config WHERE guild_id = $1", guild_id)
    return row["channel_id"] if row else None


async def get_staff_channel(guild_id: int) -> int | None:
    """Where settlement recaps go. NULL means nowhere, and nothing breaks — see migration 026."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT staff_channel_id FROM betting_config WHERE guild_id = $1", guild_id)
    return row["staff_channel_id"] if row else None


async def set_category_channels(guild_id: int, channels: Mapping[str, int]) -> None:
    """Replace this guild's per-category routing wholesale.

    Replace, not merge: /setup is re-runnable and its arguments are the whole truth, so a
    category left out of a re-run is one the admin dropped. Merging would strand cards in a
    channel nobody meant to keep routing to — the same reason set_betting_channel clears a
    staff channel it wasn't given.
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM betting_channels WHERE guild_id = $1", guild_id)
        for category, channel_id in channels.items():
            await conn.execute(
                "INSERT INTO betting_channels (guild_id, category, channel_id) VALUES ($1, $2, $3)",
                guild_id,
                category,
                channel_id,
            )


async def get_category_channels(guild_id: int) -> dict[str, asyncpg.Record]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM betting_channels WHERE guild_id = $1", guild_id)
    return {row["category"]: row for row in rows}


async def get_channel_for_category(guild_id: int, category: str) -> int | None:
    """Where a card of this category goes, falling back to the one betting channel.

    The fallback is what keeps routing optional: a guild that never runs the new /setup form
    behaves exactly as it did before, and a category with no channel of its own still posts
    somewhere rather than dropping the market.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT channel_id FROM betting_channels WHERE guild_id = $1 AND category = $2",
        guild_id,
        category,
    )
    if row:
        return row["channel_id"]
    return await get_betting_channel(guild_id)


async def set_board_message(guild_id: int, category: str, message_id: int | None) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE betting_channels SET board_message_id = $1 WHERE guild_id = $2 AND category = $3",
        message_id,
        guild_id,
        category,
    )


# ---------------------------------------------------------------------------
# Market CRUD
# ---------------------------------------------------------------------------


async def create_market(
    *,
    guild_id: int,
    provider: str,
    external_id: str,
    sport: str,
    competition: str,
    home_name: str,
    away_name: str,
    start_time,
) -> int | None:
    """Insert a new market. Returns its id, or None if it already exists for this provider/external_id."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO betting_markets
            (guild_id, provider, external_id, sport, competition, home_name, away_name, start_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (provider, external_id) DO NOTHING
        RETURNING id
        """,
        guild_id,
        provider,
        external_id,
        sport,
        competition,
        home_name,
        away_name,
        start_time,
    )
    return row["id"] if row else None


async def set_market_message(market_id: int, channel_id: int, message_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE betting_markets SET channel_id = $1, message_id = $2 WHERE id = $3",
        channel_id,
        message_id,
        market_id,
    )


async def get_market(market_id: int) -> asyncpg.Record | None:
    pool = get_pool()
    return await pool.fetchrow("SELECT * FROM betting_markets WHERE id = $1", market_id)


async def get_bets(market_id: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch("SELECT * FROM betting_bets WHERE market_id = $1 ORDER BY created_at", market_id)


async def get_active_bets_for_user(guild_id: int, user_id: int) -> list[asyncpg.Record]:
    """A user's stakes on unsettled markets, with the pools needed to price them.

    total_pool/outcome_pool come back per row so the caller can show what each bet would
    return at today's odds (stake * total_pool / outcome_pool).
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT b.amount, b.outcome, m.sport, m.competition, m.home_name, m.away_name,
               m.start_time, m.status,
               (SELECT SUM(amount) FROM betting_bets WHERE market_id = m.id) AS total_pool,
               (SELECT SUM(amount) FROM betting_bets
                 WHERE market_id = m.id AND outcome = b.outcome) AS outcome_pool
        FROM betting_bets b
        JOIN betting_markets m ON m.id = b.market_id
        WHERE m.guild_id = $1 AND b.user_id = $2 AND m.status IN ('open', 'locked')
        ORDER BY m.start_time
        """,
        guild_id,
        user_id,
    )


async def get_open_markets(guild_id: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch("SELECT * FROM betting_markets WHERE guild_id = $1 AND status = 'open'", guild_id)


async def lock_due_markets(guild_id: int) -> list[asyncpg.Record]:
    """Flip any open market past its start time to locked, returning the ones just locked."""
    pool = get_pool()
    return await pool.fetch(
        """
        UPDATE betting_markets
        SET status = 'locked'
        WHERE guild_id = $1 AND status = 'open' AND start_time <= NOW()
        RETURNING *
        """,
        guild_id,
    )


async def betting_pnl(guild_id: int, limit: int = 100) -> list[asyncpg.Record]:
    """Realised profit and loss per bettor, best first.

    Only `resolved` markets count. A stake still in play is neither a win nor a loss, and a
    voided bet was refunded in full — counting either would make the numbers lie.

    `payout` is written on settlement: the full return for a winner, 0 for a loser. So
    `payout - amount` is exactly what the bet made or cost.

    The house is excluded — it is staked on both sides of every market, so it would appear on
    this board as the most prolific bettor on the server, which tells nobody anything about
    who is good at betting.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT b.user_id,
               SUM(COALESCE(b.payout, 0) - b.amount) AS profit,
               COUNT(*)                              AS bets,
               COUNT(*) FILTER (WHERE COALESCE(b.payout, 0) > b.amount) AS wins
        FROM betting_bets b
        JOIN betting_markets m ON m.id = b.market_id
        WHERE m.guild_id = $1 AND m.status = 'resolved' AND b.user_id <> $3
        GROUP BY b.user_id
        ORDER BY profit DESC
        LIMIT $2
        """,
        guild_id,
        limit,
        HOUSE_USER_ID,
    )


async def claim_settle_reminders(guild_id: int, exclude_ids: Sequence[int] = ()) -> list[asyncpg.Record]:
    """Markets stuck at 'locked' that someone needs to settle. Claims them as it returns.

    A community bet has no provider, so nothing settles it automatically: it sits at 'locked'
    until the creator runs /bet resolve. If they forget, every stake on it is trapped there
    permanently.

    Provider markets are chased too, and used not to be — which is how a finished MSI match
    sat locked with a member's coins in it and the bot never said a word. The resolution
    ticker retries such a market forever in silence; if the API never reports a usable result,
    silence is all anyone ever gets. A market nobody can settle has to be *visible*.

    `exclude_ids` is how the caller says "the provider told me this match is still being
    played". The window measures from **kickoff**, not from the final whistle — a market locks
    at kickoff and the reminder is due 2h later, which for a football match (~1h50-2h05) lands
    while it is still in play. Without this the bot announced "le résultat n'est jamais arrivé"
    about a match that then settled itself five minutes later: one false alarm per match, which
    is plenty to make everyone stop believing the real ones. Excluding from the *claim* rather
    than filtering the result is deliberate — the claim stamps `resolve_reminded_at`, so a
    claimed-then-discarded market would go quiet for 24h if the result really never arrived.

    The claim and the send cannot be two steps, or a slow tick would ping twice. Stamping
    `resolve_reminded_at` inside the same UPDATE also makes the reminder repeat on a slow
    cadence instead of firing every few minutes.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        UPDATE betting_markets
        SET resolve_reminded_at = NOW()
        WHERE guild_id = $1
          AND status = 'locked'
          AND start_time < NOW() - $2::interval
          AND (resolve_reminded_at IS NULL OR resolve_reminded_at < NOW() - $3::interval)
          AND NOT (id = ANY($4::bigint[]))
        RETURNING *
        """,
        guild_id,
        RESOLVE_REMINDER_AFTER,
        RESOLVE_REMINDER_REPEAT,
        list(exclude_ids),
    )


async def get_stuck_markets(guild_id: int) -> list[asyncpg.Record]:
    """Markets locked so long that nobody is ever going to settle them by hand.

    The last line of defence for the trapped-stakes bug: whatever went wrong — a provider that
    never reported a result, a creator who left the server — the coins come back. Voiding
    refunds every stake exactly, so this can never cost anyone anything; the only thing it
    takes away is the chance of a settlement that was never coming.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT * FROM betting_markets
        WHERE guild_id = $1 AND status = 'locked' AND start_time < NOW() - $2::interval
        """,
        guild_id,
        STUCK_VOID_AFTER,
    )


async def get_locked_markets(guild_id: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch("SELECT * FROM betting_markets WHERE guild_id = $1 AND status = 'locked'", guild_id)


# ---------------------------------------------------------------------------
# Betting
# ---------------------------------------------------------------------------


async def place_bet(*, market_id: int, guild_id: int, user_id: int, outcome: str, amount: int) -> BetOutcome:
    """Debit the bettor's wallet and record a bet, atomically.

    One option per person: you may top up the side you already backed, but not back a second
    one. Hedging both sides of a parimutuel pool only ever loses you money, and it makes the
    "who's on which side" read of the card meaningless.
    """
    if amount <= 0:
        return BetOutcome("invalid_amount")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Locking the market row also serializes concurrent bets on it, so a user
            # double-clicking two different outcomes can't slip past the check below.
            market = await conn.fetchrow("SELECT status FROM betting_markets WHERE id = $1 FOR UPDATE", market_id)
            if not market or market["status"] != "open":
                return BetOutcome("closed")

            existing = await conn.fetchrow(
                "SELECT outcome FROM betting_bets WHERE market_id = $1 AND user_id = $2 LIMIT 1",
                market_id,
                user_id,
            )
            if existing and existing["outcome"] != outcome:
                return BetOutcome("other_outcome", existing_outcome=existing["outcome"])

            balance = await currency_service.get_balance_locked(conn, guild_id=guild_id, user_id=user_id)
            if balance < amount:
                # Carry the balance back so the bettor is told what they can actually afford.
                return BetOutcome("insufficient_funds", new_balance=balance)

            new_balance = await currency_service.adjust(
                conn, guild_id=guild_id, user_id=user_id, amount=-amount, reason="bet"
            )
            await conn.execute(
                "INSERT INTO betting_bets (market_id, user_id, outcome, amount) VALUES ($1, $2, $3, $4)",
                market_id,
                user_id,
                outcome,
                amount,
            )
            return BetOutcome("ok", new_balance=new_balance, topped_up=existing is not None)


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


@dataclass
class VoidResult:
    """Outcome of voiding a market.

    `voided` is False when the market had already been settled — the loser of a settle race
    must not refund on top of a payout. `fee_refunded` exists so the caller cannot re-derive
    the rule and get it wrong, which is exactly what happened before.
    """

    voided: bool
    fee_refunded: bool = False


async def resolve_market(market_id: int, winner: str) -> bool:
    """Pay the winners out of the pool and close the market. Returns whether it did.

    The claim is the first statement of the transaction, not the last: two `/bet resolve`
    calls a second apart (double click, two clients, a mod racing the ticker) both take
    `FOR UPDATE`, but `FOR UPDATE` only serializes them — it does not undo a stale status read
    taken before either wrote. Claiming the status change atomically means the loser of the
    race reads back zero rows and must stand down before touching a single coin, the same
    pattern as the tribunal's `claim_verdict`.

    The house is the residual claimant: whatever the pool does not pay out goes to it. That
    is one rule covering two leaks that used to just delete coins — the few 🪙 of rounding
    dust that integer division leaves behind, and (when the house is not staked, so nobody
    backed the winner) the entire pool. Those losing stakes are what refills the treasury and
    pays for the next market's seed, which is what makes the house self-sustaining rather
    than a slow faucet.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow(
                """
                UPDATE betting_markets SET status = 'resolved', winner = $1
                WHERE id = $2 AND status IN ('open', 'locked')
                RETURNING guild_id
                """,
                winner,
                market_id,
            )
            if market is None:
                return False

            bets = await conn.fetch("SELECT * FROM betting_bets WHERE market_id = $1", market_id)
            payouts = settle_parimutuel(bets, winner)
            for bet in bets:
                payout = payouts.get(bet["id"], 0)
                await conn.execute("UPDATE betting_bets SET payout = $1 WHERE id = $2", payout, bet["id"])
                if payout > 0:
                    await currency_service.adjust(
                        conn, guild_id=market["guild_id"], user_id=bet["user_id"], amount=payout, reason="payout"
                    )

            residual = sum(b["amount"] for b in bets) - sum(payouts.values())
            if residual > 0:
                await currency_service.adjust(
                    conn,
                    guild_id=market["guild_id"],
                    user_id=HOUSE_USER_ID,
                    amount=residual,
                    reason="house_margin",
                )

            return True


async def void_market(market_id: int) -> VoidResult:
    """Refund every stake and close the market. See resolve_market for why the claim comes first."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow(
                """
                UPDATE betting_markets SET status = 'void'
                WHERE id = $1 AND status IN ('open', 'locked')
                RETURNING guild_id, provider, creator_user_id
                """,
                market_id,
            )
            if market is None:
                return VoidResult(voided=False)

            bets = await conn.fetch("SELECT * FROM betting_bets WHERE market_id = $1", market_id)
            for bet in bets:
                await conn.execute("UPDATE betting_bets SET payout = $1 WHERE id = $2", bet["amount"], bet["id"])
                await currency_service.adjust(
                    conn, guild_id=market["guild_id"], user_id=bet["user_id"], amount=bet["amount"], reason="refund"
                )

            # Give the creation fee back, but only for a bet other people actually joined.
            # Cancelling a bet nobody touched is the very case the fee exists to deter, and
            # refunding it there would make create-and-cancel a free spam loop — including
            # the creator staking on their own bet to fake participation. The house's seed is
            # not participation either: it is on every bet ever opened, so counting it would
            # make the refund unconditional and hand the spam loop straight back.
            creator = market["creator_user_id"]
            fee_refunded = False
            if market["provider"] == "custom" and creator is not None:
                if any(bet["user_id"] not in (creator, HOUSE_USER_ID) for bet in bets):
                    fee_refunded = True
                    await currency_service.adjust(
                        conn,
                        guild_id=market["guild_id"],
                        user_id=creator,
                        amount=CREATE_FEE,
                        reason="bet_create_fee_refund",
                    )
                    # Out of the house's pocket, since that is where the fee went.
                    await currency_service.adjust(
                        conn,
                        guild_id=market["guild_id"],
                        user_id=HOUSE_USER_ID,
                        amount=-CREATE_FEE,
                        reason="bet_create_fee_refund",
                    )

            return VoidResult(voided=True, fee_refunded=fee_refunded)
