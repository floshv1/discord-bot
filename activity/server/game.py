"""The game API. The one rule: the answer never reaches a member who has not found it.

What keeps that true, so a later change does not quietly break it:
- `build_state` never reads `spells`/`splash`, and names the answer only under `solved`;
- the champion list is the catalog, ordered by name, so it cannot depend on today's answer;
- image hints are re-encoded PNGs under URLs with no champion in them, served only to members who own them.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from aiohttp import web
from loguru import logger

from activity.server import assets, session
from activity.server.auth import error, read_json, require_member, throttled
from activity.server.keys import (
    CLOCK,
    HINT_CACHE,
    HTTP,
    ICON_CACHE,
    ICON_FAILED,
    ICON_FLIGHT,
    ICON_SLOTS,
    ONLINE,
    SETTINGS,
    USER_ID,
)
from bot.cogs.currency import service as currency
from bot.cogs.loldle import catalog, hints, rules, service

MAX_CHAMPION_TEXT = 64
ICON_CONCURRENCY = 4
ICON_COOLDOWN = 60  # seconds a failing icon is answered 502 without asking upstream again
IMAGE_HEADERS = {"Cache-Control": "private, no-store"}
IMAGE_KINDS = ("spell", "splash")
# What `render_hint` says for the image hints, so an owned hint's caption needs no download.
IMAGE_TEXT = {"spell": "✨ Un de ses sorts :", "splash": "🖼️ Un bout de son splash art :"}
ANONYMOUS = "Joueur"


def today(request: web.Request) -> dt.date:
    return rules.paris_day(dt.datetime.fromtimestamp(request.app[CLOCK](), dt.UTC))


async def _answer(guild_id: int, day: dt.date) -> catalog.Champion:
    return service.answer_of(await service.get_or_pick_day(guild_id, day, catalog.load()))


def live_player(row, online: set[int]) -> dict:
    user_id = row["user_id"]
    name = row["display_name"]
    if not name or name == str(user_id):  # live_rows falls back to the id when there is no player row
        name = ANONYMOUS
    return {
        "user_id": str(user_id),
        "name": name,
        "avatar": row["avatar_hash"],
        "tries": row["tries"],
        "solved": row["solved"],
        "last_cells": service.cells_of({"cells": row["last_cells"]}) if row["last_cells"] is not None else [],
        "online": user_id in online,
    }


def idle_player(user_id: int, row, online: set[int]) -> dict:
    """A member with the Activity open who has not guessed yet: no tries, no colours."""
    name = row["display_name"] if row is not None else None
    return {
        "user_id": str(user_id),
        "name": name or ANONYMOUS,
        "avatar": row["avatar_hash"] if row is not None else None,
        "tries": 0,
        "solved": False,
        "last_cells": [],
        "online": user_id in online,
    }


async def live_panel(guild_id: int, day: dt.date, online: set[int]) -> list[dict]:
    """Everyone who played today, plus every online member who has not (they sit on top, client side)."""
    rows = await service.live_rows(guild_id, day)
    panel = [live_player(r, online) for r in rows]
    seen = {r["user_id"] for r in rows}
    waiting = sorted(uid for uid in online if uid not in seen)
    if waiting:
        names = {r["user_id"]: r for r in await service.player_names(guild_id, waiting)}
        panel.extend(idle_player(uid, names.get(uid), online) for uid in waiting)
    return panel


async def build_state(guild_id: int, day: dt.date, user_id: int, online: set[int]) -> dict:
    answer = await _answer(guild_id, day)
    rows = await service.guesses(guild_id, day, user_id)
    won = await service.result(guild_id, day, user_id)
    owned = await service.hints(guild_id, day, user_id)
    wallet, _ = await currency.get_or_create_wallet(guild_id, user_id)

    solved = None
    if won is not None:
        count = rules.streak(await service.all_days(guild_id), await service.solved_days(guild_id, user_id), day)
        solved = {
            "name": answer.name,
            "tries": won["tries"],
            "hints": won["hints"],
            "score": won["score"],
            "streak": count,
        }

    hint_state = {}
    for kind, price in rules.HINTS.items():
        entry = {
            "owned": kind in owned,
            "price": price,
            "available": won is None and rules.can_buy(kind, owned, len(rows)),
        }
        if kind in owned:  # the only place a hint's content is read: owned means paid for
            if kind == "letter":
                entry["text"] = (await hints.render_hint(kind, answer, guild_id, day, None)).text
            else:
                entry["text"] = IMAGE_TEXT[kind]
        hint_state[kind] = entry

    return {
        "day": day.isoformat(),
        "guesses": [
            {
                "number": r["number"],
                "champion_id": r["champion_id"],
                "name": r["name"],
                "cells": service.cells_of(r),
                "clues": service.clues_of(r),
            }
            for r in rows
        ],
        "solved": solved,
        "hints": hint_state,
        "balance": wallet["balance"],
        "live": await live_panel(guild_id, day, online),
    }


async def _state(request: web.Request, day: dt.date) -> dict:
    return await build_state(request.app[SETTINGS].guild_id, day, request[USER_ID], request.app[ONLINE]())


@require_member
async def get_state(request: web.Request) -> web.Response:
    return web.json_response(await _state(request, today(request)))


@require_member
async def get_champions(request: web.Request) -> web.Response:
    champions = sorted(catalog.load().values(), key=lambda c: (catalog.normalise(c.name), c.id))
    return web.json_response([{"id": c.id, "name": c.name} for c in champions])


@require_member
async def post_guess(request: web.Request) -> web.Response:
    if throttled(request):
        return error(429, "rate_limited")
    body = await read_json(request)
    text = body.get("champion") if body else None
    if not isinstance(text, str) or not text or len(text) > MAX_CHAMPION_TEXT:
        return error(400, "bad_request")
    guild_id, user_id, day = request.app[SETTINGS].guild_id, request[USER_ID], today(request)
    answer = await _answer(guild_id, day)
    guess = service.resolve_guess(text, answer)
    if guess is None:
        return web.json_response({"status": "unknown", "state": await _state(request, day)})
    outcome = await service.record_guess(guild_id, day, user_id, guess, answer)
    return web.json_response({"status": outcome.status, "state": await _state(request, day)})


def _prune(cache: dict, day: dt.date) -> None:
    """Keep the current day only."""
    for key in [k for k in cache if k[0] != day]:
        del cache[key]


def _hint_reply(status: str, state: dict) -> web.Response:
    return web.json_response({"status": status, "balance": state["balance"], "state": state})


@require_member
async def post_hint(request: web.Request) -> web.Response:
    kind = request.match_info["kind"]
    if kind not in rules.HINTS:
        return error(404, "not_found")
    if throttled(request):
        return error(429, "rate_limited")
    guild_id, user_id, day = request.app[SETTINGS].guild_id, request[USER_ID], today(request)
    # Plainly owned or not purchasable: answer from the state, no download. `buy_hint` still decides races.
    state = await _state(request, day)
    if state["hints"][kind]["owned"]:
        return _hint_reply("owned", state)
    if not state["hints"][kind]["available"]:
        return _hint_reply("locked", state)
    cache = request.app[HINT_CACHE]
    _prune(cache, day)
    cached = cache.get((day, kind))
    if cached is None:
        # Rendered first, as the cog does: a CDN failure must cost the member nothing.
        try:
            content = await hints.render_hint(kind, await _answer(guild_id, day), guild_id, day, request.app[HTTP])
        except hints.HintUnavailable as exc:
            logger.warning("activity: hint {} unavailable: {}", kind, exc)
            return _hint_reply("unavailable", state)
        cached = content.file_bytes
    if today(request) != day:  # the download can straddle midnight: never sell a finished game
        return _hint_reply("locked", await _state(request, today(request)))
    outcome = await service.buy_hint(guild_id, day, user_id, kind)
    if outcome.status in ("bought", "owned") and cached is not None:
        cache[(day, kind)] = cached
    return _hint_reply(outcome.status, await _state(request, day))


def _png(data: bytes, headers: dict[str, str]) -> web.Response:
    return web.Response(body=data, content_type="image/png", headers=headers)


def _ticket_user(request: web.Request, resource: str) -> int | None:
    return session.verify_ticket(
        request.app[SETTINGS].session_secret, request.query.get("ticket"), resource, request.app[CLOCK]()
    )


async def get_hint_image(request: web.Request) -> web.Response:
    kind = request.match_info["kind"]
    if kind not in IMAGE_KINDS:  # the letter is text only
        return error(404, "not_found")
    user_id = _ticket_user(request, f"hint:{kind}")
    if user_id is None:
        return error(401, "unauthorized")
    guild_id, day = request.app[SETTINGS].guild_id, today(request)
    cache = request.app[HINT_CACHE]
    _prune(cache, day)
    if kind not in await service.hints(guild_id, day, user_id):
        return error(403, "forbidden")
    data = cache.get((day, kind))
    if data is None:
        try:
            content = await hints.render_hint(kind, await _answer(guild_id, day), guild_id, day, request.app[HTTP])
        except hints.HintUnavailable as exc:
            logger.warning("activity: hint {} image unavailable: {}", kind, exc)
            return error(502, "upstream")
        data = cache[(day, kind)] = content.file_bytes
    return _png(data, IMAGE_HEADERS)


async def get_result_splash(request: web.Request) -> web.Response:
    user_id = _ticket_user(request, "splash")
    if user_id is None:
        return error(401, "unauthorized")
    guild_id, day = request.app[SETTINGS].guild_id, today(request)
    cache = request.app[HINT_CACHE]
    _prune(cache, day)
    if await service.result(guild_id, day, user_id) is None:
        return error(403, "forbidden")
    data = cache.get((day, "result"))
    if data is None:
        try:
            data = await assets.render_splash(await _answer(guild_id, day), request.app[HTTP])
        except hints.HintUnavailable as exc:
            logger.warning("activity: result splash unavailable: {}", exc)
            return error(502, "upstream")
        cache[(day, "result")] = data
    return _png(data, IMAGE_HEADERS)


async def _load_icon(app: web.Application, champion_id: str) -> bytes:
    """The one upstream fetch for an id: shared by every waiter, capped globally, remembered on failure."""
    try:
        async with app[ICON_SLOTS]:
            data = await assets.fetch_icon(champion_id, app[HTTP])
    except hints.HintUnavailable:
        app[ICON_FAILED][champion_id] = app[CLOCK]()
        raise
    cache = app[ICON_CACHE]
    while len(cache) >= len(catalog.load()):
        del cache[next(iter(cache))]
    cache[champion_id] = data
    return data


def _retrieved(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # nobody may be waiting any more: do not leave it unretrieved


async def get_icon(request: web.Request) -> web.Response:
    app = request.app
    champion_id = request.match_info["champion_id"]
    if champion_id not in catalog.load():  # exact match against the catalog: nothing else reaches a URL
        return error(404, "not_found")
    data = app[ICON_CACHE].get(champion_id)
    if data is None:
        failed_at = app[ICON_FAILED].get(champion_id)
        if failed_at is not None and app[CLOCK]() - failed_at < ICON_COOLDOWN:
            return error(502, "upstream")
        flight = app[ICON_FLIGHT]
        task = flight.get(champion_id)
        if task is None:
            task = flight[champion_id] = asyncio.ensure_future(_load_icon(app, champion_id))
            task.add_done_callback(lambda t: flight.pop(champion_id, None))
            task.add_done_callback(_retrieved)
        try:
            data = await asyncio.shield(task)  # a client that goes away must not cancel the fetch for the others
        except hints.HintUnavailable as exc:
            logger.warning("activity: icon unavailable: {}", exc)
            return error(502, "upstream")
    return _png(data, {"Cache-Control": "public, max-age=86400"})


def add_routes(app: web.Application) -> None:
    app.router.add_get("/api/state", get_state)
    app.router.add_get("/api/champions", get_champions)
    app.router.add_post("/api/guess", post_guess)
    app.router.add_post("/api/hints/{kind}", post_hint)
    app.router.add_get("/api/hints/{kind}.png", get_hint_image)
    app.router.add_get("/api/result/splash.png", get_result_splash)
    app.router.add_get("/api/icons/{champion_id}.png", get_icon)
