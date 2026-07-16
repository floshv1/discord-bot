from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord

from bot.cogs.betting.service import (
    CATEGORY_CUSTOM,
    CATEGORY_LABELS,
    competitions_in_category,
    creator_holds_gavel,
    house_pnl,
    house_stakes,
    implied_odds,
    outcomes_for_market,
    player_bets,
    pool_shares,
    pool_totals,
)

BAR_WIDTH = 12
PNL_SIDE_COUNT = 5  # how many winners, and how many losers, to name
BOARD_FIXTURE_COUNT = 10  # how many upcoming matches the pinned board lists


def build_board_embed(category: str, markets: Sequence[Mapping]) -> discord.Embed:
    """The pinned "what's followed here, and what's coming" message for one betting channel.

    `markets` are that category's open fixtures, soonest first. Kept separate from the cards:
    a channel has to say what it is for before any fixture lands in it, and an empty channel
    with no board reads as broken rather than as out-of-season.
    """
    label = CATEGORY_LABELS.get(category, category)
    embed = discord.Embed(title=f"📌 Paris — {label}", color=discord.Color.blurple())

    if category == CATEGORY_CUSTOM:
        embed.description = "Les paris créés avec `/bet create` atterrissent ici."
        return embed

    followed = competitions_in_category(category)
    if followed:
        embed.add_field(name="Compétitions suivies", value=" · ".join(followed), inline=False)

    if not markets:
        embed.add_field(name="Prochains matchs", value="*Rien de prévu pour l'instant.*", inline=False)
        return embed

    lines = []
    for market in markets[:BOARD_FIXTURE_COUNT]:
        # A Discord timestamp renders in each reader's own timezone, which a formatted date
        # cannot do — and this board is read by people who are not all in the same one.
        when = int(market["start_time"].timestamp())
        lines.append(f"• **{market['home_name']}** vs **{market['away_name']}** — <t:{when}:R>")
    embed.add_field(name="Prochains matchs", value="\n".join(lines), inline=False)
    return embed


def _pnl_line(rank: int, row: Mapping, names: Mapping[int, str]) -> str:
    name = names.get(row["user_id"], f"<@{row['user_id']}>")
    # The bet count matters: +900 over 2 bets and +900 over 50 are not the same story.
    return f"**#{rank}** {name} — **{row['profit']:+,}** 🪙 *({row['wins']}/{row['bets']} paris gagnés)*"


def build_pnl_embed(rows: Sequence[Mapping], names: Mapping[int, str], updated: str) -> discord.Embed:
    """Who is actually winning at betting, and who is bleeding.

    Rows arrive sorted by profit descending. Both ends are shown: a board that only named
    the winners would tell half the story, and the losing half is the funnier half.
    """
    embed = discord.Embed(title="📈 Bénéfices & pertes", color=discord.Color.green())
    embed.set_footer(text=f"Paris réglés uniquement · mis à jour {updated}")

    if not rows:
        embed.description = "*Aucun pari réglé pour l'instant.*"
        return embed

    winners = [r for r in rows if r["profit"] > 0][:PNL_SIDE_COUNT]
    losers = [r for r in rows if r["profit"] < 0]
    # rows are best-first, so the worst are at the very end — and worst-first reads better.
    losers = list(reversed(losers))[:PNL_SIDE_COUNT]

    if winners:
        embed.add_field(
            name="🔥 Dans le vert",
            value="\n".join(_pnl_line(i, r, names) for i, r in enumerate(winners, 1)),
            inline=False,
        )
    if losers:
        embed.add_field(
            name="💀 Dans le rouge",
            value="\n".join(_pnl_line(i, r, names) for i, r in enumerate(losers, 1)),
            inline=False,
        )
    if not winners and not losers:
        embed.description = "*Tout le monde est à l'équilibre.*"
    return embed


OUTCOME_EMOJI = {"home": "🏠", "draw": "🤝", "away": "🛫"}
CUSTOM_OUTCOME_EMOJI = {"home": "🅰️", "away": "🅱️"}
SPORT_EMOJI = {"football": "⚽", "lol": "🎮", "custom": "🎲"}


def _is_custom(market: Mapping) -> bool:
    return market["sport"] == "custom"


def _bar(share: float) -> str:
    """A Twitch-style filled bar showing this outcome's share of the pool."""
    filled = round(share * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def build_market_embed(market: Mapping, bets: Sequence[Mapping]) -> discord.Embed:
    status = market["status"]
    custom = _is_custom(market)
    ts = int(market["start_time"].timestamp())
    emoji = CUSTOM_OUTCOME_EMOJI if custom else OUTCOME_EMOJI

    # Two views of the same market, and the difference matters. The *cote* is priced off every
    # coin in the pool, house included, because that is what settlement actually pays out. The
    # bars, the coin counts and the 👤 show only what members staked — a bar that was half full
    # because the bank is on that side would tell the reader nothing about the room.
    odds = implied_odds(bets)
    mine = player_bets(bets)
    totals = pool_totals(mine)
    shares = pool_shares(mine)
    player_pool = sum(t["total"] for t in totals.values())
    house = house_stakes(bets)
    house_pool = sum(house.values())
    total_pool = player_pool + house_pool

    lines = []
    for key, label in outcomes_for_market(market):
        entry = totals.get(key, {"total": 0, "count": 0, "backers": 0})
        # An outcome nobody has backed at all — not even the house — has no meaningful cote.
        cote = f"`{odds[key]:.2f}x`" if key in odds else "`—`"
        winner_mark = " ✅" if status == "resolved" and key == market["winner"] else ""
        share = shares.get(key, 0.0)
        people = entry["backers"]
        lines.append(
            f"{emoji[key]} **{label}** {cote}{winner_mark}\n"
            f"`{_bar(share)}` {share:.0%} · {entry['total']:,} 🪙 · {people} 👤"
        )
    if house_pool:
        line = " / ".join(f"{house.get(key, 0):,}" for key, _ in outcomes_for_market(market))
        lines.append(f"\n🏦 *Mise de la banque : {line} 🪙 — de quoi gagner même seul sur un pari.*")

    if custom:
        # For a custom bet the competition column holds the question being bet on.
        matchup = market["competition"]
        closing_word, closed_word, void_word = "Closes", "Betting closed — awaiting result", "Bet cancelled"
    else:
        matchup = f"**{market['home_name']}** vs **{market['away_name']}**"
        closing_word, closed_word, void_word = "Kickoff", "Betting closed — match in progress", "Match voided"

    if status == "open":
        title = f"{SPORT_EMOJI[market['sport']]} {market['competition'] if not custom else 'Community bet'}"
        color = discord.Color.blurple()
        description = f"{matchup}\n\n{closing_word}: <t:{ts}:R>"
        # Parimutuel odds are indicative: they move as money comes in, and you're paid the
        # odds at settlement, not the odds you saw when you bet. Say so, or it looks like a bug.
        footer = "One option each · odds shift as bets come in — you're paid the final odds"
    elif status == "locked":
        title = f"🔒 {market['competition'] if not custom else 'Community bet'}"
        color = discord.Color.greyple()
        description = matchup
        footer = closed_word
    elif status == "resolved":
        winner = market["winner"]
        winner_label = dict(outcomes_for_market(market)).get(winner, winner)
        title = f"✅ {'Community bet' if custom else market['competition']} — {winner_label} won"
        color = discord.Color.green()
        description = matchup
        # The multiplier that was actually paid, so it must be priced off the whole pool —
        # the same numbers settlement used, not the members-only view above it.
        if totals.get(winner, {"total": 0})["total"] == 0:
            footer = "Nobody bet on the winning outcome — no payouts"
        else:
            footer = f"Payout: {odds[winner]:.2f}x stake"
    else:  # void
        title = f"⚠️ {'Community bet' if custom else market['competition']} — {void_word}"
        color = discord.Color.red()
        description = matchup
        footer = "All bets refunded"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="Cotes", value="\n".join(lines), inline=False)
    if custom and market.get("creator_user_id"):
        embed.add_field(name="Ouvert par", value=f"<@{market['creator_user_id']}>", inline=True)
        # Who settles this is not trivia — it changes who you're trusting with your stake, and
        # it flips the moment the creator bets on their own bet. Say it on the card, before
        # anyone stakes, not in the refusal message afterwards.
        arbiter = f"<@{market['creator_user_id']}>" if creator_holds_gavel(market, bets) else "un modérateur"
        embed.add_field(name="⚖️ Arbitre", value=arbiter, inline=True)
    pool_text = f"Total pool: {total_pool:,} 🪙"
    if house_pool:
        pool_text += f" (dont {house_pool:,} de la banque)"
    embed.set_footer(text=f"{footer} · {pool_text}")
    return embed


STAFF_LINE_LIMIT = 15  # an embed field caps at 1024 chars — a busy market would blow past it


def build_staff_result_embed(market: Mapping, bets: Sequence[Mapping], settled_by: str) -> discord.Embed:
    """The full breakdown of a settled market, for moderators only.

    `settled_by` is not decoration: a custom bet's winner is *declared*, not observed, so the
    one thing moderators need to be able to see is who declared it. This embed is the audit
    surface for that. Mentions inside an embed render as names and notify nobody, which is what
    makes it safe to name every bettor here.

    `bets` is every row, the house included — its P&L is exactly what tells you whether a market
    fed the treasury or drained it.
    """
    resolved = market["status"] == "resolved"
    headline = (
        market["competition"] if market["sport"] == "custom" else f"{market['home_name']} vs {market['away_name']}"
    )
    labels = dict(outcomes_for_market(market))

    if resolved:
        title = f"🏁 {headline} — {labels.get(market['winner'], market['winner'])}"
        color = discord.Color.green()
    else:
        title = f"⚠️ {headline} — annulé, tout remboursé"
        color = discord.Color.red()

    players = player_bets(bets)
    by_user: dict[int, dict[str, int]] = {}
    for bet in players:
        entry = by_user.setdefault(bet["user_id"], {"staked": 0, "payout": 0})
        entry["staked"] += bet["amount"]
        entry["payout"] += bet["payout"] or 0

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Réglé par", value=settled_by, inline=True)
    embed.add_field(name="P&L maison", value=f"**{house_pnl(bets):+,}** 🪙", inline=True)

    pool = sum(b["amount"] for b in bets)
    winners = sum(1 for e in by_user.values() if resolved and e["payout"] > e["staked"])
    embed.add_field(
        name="Pool",
        value=f"{pool:,} 🪙 · {len(by_user)} parieur(s) · {winners} gagnant(s)",
        inline=False,
    )

    ranked = sorted(by_user.items(), key=lambda kv: kv[1]["payout"] - kv[1]["staked"], reverse=True)
    lines = [
        f"<@{user_id}> — {e['staked']:,} → {e['payout']:,} 🪙 (**{e['payout'] - e['staked']:+,}**)"
        for user_id, e in ranked[:STAFF_LINE_LIMIT]
    ]
    if len(ranked) > STAFF_LINE_LIMIT:
        lines.append(f"*… et {len(ranked) - STAFF_LINE_LIMIT} autre(s).*")
    embed.add_field(name="Parieurs", value="\n".join(lines), inline=False)
    return embed
