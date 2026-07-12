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

# How long after a market locks before someone is reminded to settle it, and how often the
# nudge repeats until they do. One ignorable ping isn't enough when other people's coins are
# stuck behind it.
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
            needed = HOUSE_SEED_PER_OUTCOME * len(outcomes)

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
                    HOUSE_SEED_PER_OUTCOME,
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


async def list_settleable_markets(guild_id: int) -> list[asyncpg.Record]:
    """Every market still awaiting a result.

    Includes provider markets, not just custom ones: if an API never reports a final
    result the market would otherwise stay locked forever with everyone's stakes trapped,
    so a mod needs to be able to settle or cancel it by hand.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT * FROM betting_markets
        WHERE guild_id = $1 AND status IN ('open', 'locked')
        ORDER BY start_time
        """,
        guild_id,
    )


# ---------------------------------------------------------------------------
# Guild config
# ---------------------------------------------------------------------------


async def set_betting_channel(guild_id: int, channel_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO betting_config (guild_id, channel_id)
        VALUES ($1, $2)
        ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
        """,
        guild_id,
        channel_id,
    )


async def get_betting_channel(guild_id: int) -> int | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT channel_id FROM betting_config WHERE guild_id = $1", guild_id)
    return row["channel_id"] if row else None


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


async def claim_settle_reminders(guild_id: int) -> list[asyncpg.Record]:
    """Markets stuck at 'locked' that someone needs to settle. Claims them as it returns.

    A community bet has no provider, so nothing settles it automatically: it sits at 'locked'
    until the creator runs /bet resolve. If they forget, every stake on it is trapped there
    permanently.

    Provider markets are chased too, and used not to be — which is how a finished MSI match
    sat locked with a member's coins in it and the bot never said a word. The resolution
    ticker retries such a market forever in silence; if the API never reports a usable result,
    silence is all anyone ever gets. A market nobody can settle has to be *visible*.

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
        RETURNING *
        """,
        guild_id,
        RESOLVE_REMINDER_AFTER,
        RESOLVE_REMINDER_REPEAT,
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


async def resolve_market(market_id: int, winner: str) -> None:
    """Pay the winners out of the pool and close the market.

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
            market = await conn.fetchrow("SELECT guild_id FROM betting_markets WHERE id = $1 FOR UPDATE", market_id)
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

            await conn.execute(
                "UPDATE betting_markets SET status = 'resolved', winner = $1 WHERE id = $2", winner, market_id
            )


async def void_market(market_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow(
                "SELECT guild_id, provider, creator_user_id FROM betting_markets WHERE id = $1 FOR UPDATE",
                market_id,
            )
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
            if market["provider"] == "custom" and creator is not None:
                if any(bet["user_id"] not in (creator, HOUSE_USER_ID) for bet in bets):
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

            await conn.execute("UPDATE betting_markets SET status = 'void' WHERE id = $1", market_id)
