"""aiohttp application: /health, /api/auth, /api/tickets. Game routes live in game.py."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
from aiohttp import web
from loguru import logger

from activity.server import game, live, session
from activity.server.auth import error, require_member, throttled
from activity.server.auth import read_json as _read_json
from activity.server.keys import (
    AUTH_LIMITER,
    CLOCK,
    HINT_CACHE,
    HTTP,
    ICON_CACHE,
    ICON_FAILED,
    ICON_FLIGHT,
    ICON_SLOTS,
    ONLINE,
    PING,
    SETTINGS,
    STATIC_DIR,
    USER_ID,
    WRITE_LIMITER,
)
from activity.server.limits import RateLimiter
from activity.server.settings import Settings
from bot.cogs.loldle import service
from bot.db.client import get_pool

DISCORD_API = "https://discord.com/api/v10"
DISCORD_TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_BODY = 4096
TICKET_RESOURCES = frozenset({"ws", "hint:spell", "hint:splash", "splash"})
_STATUS_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "too_large"}


@web.middleware
async def errors_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        # aiohttp's own 404/405/413: same fixed-code JSON shape, never its text.
        return error(exc.status, _STATUS_CODES.get(exc.status, "error"))
    except Exception as exc:  # noqa: BLE001 - last line of defence; type only, text can carry secrets
        logger.error("unhandled {} on {} {}", type(exc).__name__, request.method, request.path)
        return error(500, "internal")


@web.middleware
async def unconfigured_middleware(request: web.Request, handler):
    if request.app[SETTINGS] is None and (request.path in ("/health", "/ws") or request.path.startswith("/api/")):
        return web.Response(status=503) if request.path == "/health" else error(503, "unavailable")
    return await handler(request)


async def _default_ping() -> None:
    await get_pool().fetchval("SELECT 1")


async def health(request: web.Request) -> web.Response:
    try:
        await request.app[PING]()
    except Exception:  # noqa: BLE001
        return web.Response(status=503)
    return web.Response(status=200)


class UpstreamError(Exception):
    """Discord misbehaved. Carries nothing: its text is never forwarded."""


async def _discord_json(http, method: str, path: str, **kw) -> tuple[int, dict]:
    try:
        async with getattr(http, method)(DISCORD_API + path, timeout=DISCORD_TIMEOUT, **kw) as resp:
            status = resp.status
            body = await resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("discord call failed: {}", type(exc).__name__)
        raise UpstreamError from None
    if not isinstance(body, dict):
        logger.warning("discord answered a non-object body (HTTP {})", status)
        raise UpstreamError
    return status, body


async def auth(request: web.Request) -> web.Response:
    app = request.app
    settings: Settings = app[SETTINGS]
    body = await _read_json(request)
    code = body.get("code") if body else None
    if not isinstance(code, str) or not code or len(code) > 512:
        return error(400, "bad_request")  # malformed requests cost nothing from the shared bucket
    if not app[AUTH_LIMITER].allow("global", app[CLOCK]()):
        return error(429, "rate_limited")

    http = app[HTTP]
    try:
        status, token_body = await _discord_json(
            http,
            "post",
            "/oauth2/token",
            data={
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "grant_type": "authorization_code",
                "code": code,
            },
        )
        if status == 400:
            return error(401, "unauthorized")
        if status == 401:  # may be our own client secret: not the member's fault
            logger.error("discord token exchange answered HTTP 401")
            return error(502, "upstream")
        access_token = token_body.get("access_token")
        if status != 200 or not isinstance(access_token, str) or not access_token:
            return error(502, "upstream")
        headers = {"Authorization": f"Bearer {access_token}"}

        status, user = await _discord_json(http, "get", "/users/@me", headers=headers)
        if status != 200 or not str(user.get("id", "")).isdigit():
            return error(502, "upstream")
        user_id = int(user["id"])

        status, member = await _discord_json(
            http, "get", f"/users/@me/guilds/{settings.guild_id}/member", headers=headers
        )
    except UpstreamError:
        return error(502, "upstream")
    if status == 404:
        return error(403, "forbidden")
    member_user = member.get("user")
    if status != 200 or not isinstance(member_user, dict) or str(member_user.get("id")) != str(user_id):
        return error(502, "upstream")

    name = member.get("nick") or user.get("global_name") or user.get("username") or "Joueur"
    avatar = member.get("avatar") or user.get("avatar")
    await service.upsert_player(settings.guild_id, user_id, str(name)[:80], avatar if isinstance(avatar, str) else None)
    return web.json_response(
        {
            "access_token": access_token,
            "session": session.issue(settings.session_secret, user_id, app[CLOCK]()),
            "user_id": str(user_id),
        }
    )


@require_member
async def tickets(request: web.Request) -> web.Response:
    if throttled(request):
        return error(429, "rate_limited")
    body = await _read_json(request)
    resource = body.get("resource") if body else None
    if not isinstance(resource, str) or resource not in TICKET_RESOURCES:
        return error(400, "bad_request")
    ticket = session.issue_ticket(
        request.app[SETTINGS].session_secret, request[USER_ID], resource, request.app[CLOCK]()
    )
    return web.json_response({"ticket": ticket})


def static_dir() -> Path:
    configured = (os.environ.get("ACTIVITY_STATIC_DIR") or "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / "web" / "dist"


async def config(request: web.Request) -> web.Response:
    """The only public setting. The client id is not a secret (Discord shows it to every member)."""
    return web.json_response({"client_id": request.app[SETTINGS].client_id})


async def index(request: web.Request) -> web.StreamResponse:
    page = request.app[STATIC_DIR] / "index.html"
    if not page.is_file():
        raise web.HTTPNotFound
    return web.FileResponse(page, headers={"Cache-Control": "no-store"})


async def asset(request: web.Request) -> web.StreamResponse:
    """Only direct children of <dist>/assets: no listing, and nothing a decoded `..` or `/` can escape with."""
    name = request.match_info["name"]
    if not name or name.startswith(".") or "/" in name or "\\" in name or "\x00" in name:
        raise web.HTTPNotFound
    folder = (request.app[STATIC_DIR] / "assets").resolve()
    try:
        path = (folder / name).resolve()
    except (OSError, ValueError):
        raise web.HTTPNotFound from None
    if path.parent != folder or not path.is_file():
        raise web.HTTPNotFound
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def create_app(
    settings: Settings | None,
    *,
    http: aiohttp.ClientSession | None = None,
    clock: Callable[[], float] = time.time,
    ping: Callable[[], Awaitable[None]] | None = None,
    online: Callable[[], set[int]] | None = None,
    hub: live.Hub | None = None,
    listen: bool = True,
) -> web.Application:
    app = web.Application(
        client_max_size=MAX_BODY,
        middlewares=[errors_middleware, unconfigured_middleware],
    )
    app[SETTINGS] = settings
    app[STATIC_DIR] = static_dir()
    app[CLOCK] = clock
    app[PING] = ping or _default_ping
    app[AUTH_LIMITER] = RateLimiter(30, 60)
    app[WRITE_LIMITER] = RateLimiter(5, 1)
    hub = hub or live.Hub()
    app[ONLINE] = online or hub.online
    app[HINT_CACHE] = {}
    app[ICON_CACHE] = {}
    app[ICON_FLIGHT] = {}
    app[ICON_FAILED] = {}
    app[ICON_SLOTS] = asyncio.Semaphore(game.ICON_CONCURRENCY)
    if http is not None:
        app[HTTP] = http

    async def http_ctx(app: web.Application):
        owned = HTTP not in app
        if owned:
            app[HTTP] = aiohttp.ClientSession()
        yield
        if owned:
            await app[HTTP].close()

    app.cleanup_ctx.append(http_ctx)
    app.router.add_get("/health", health)
    app.router.add_get("/", index)
    app.router.add_get("/assets/{name}", asset)
    app.router.add_get("/api/config", config)
    app.router.add_post("/api/auth", auth)
    app.router.add_post("/api/tickets", tickets)
    live.setup(app, hub, listen=listen)
    game.add_routes(app)  # before the catch-all, or it would swallow them
    # Any other /api/* path reaches the 503 middleware (unconfigured) or aiohttp's 404 (mapped to JSON).
    app.router.add_route("*", "/api/{tail:.*}", _api_fallback)
    return app


async def _api_fallback(request: web.Request) -> web.Response:
    return error(404, "not_found")
