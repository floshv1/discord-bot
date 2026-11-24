"""Session check for member-only routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

from activity.server import session
from activity.server.keys import CLOCK, SETTINGS, USER_ID, WRITE_LIMITER

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def error(status: int, code: str) -> web.Response:
    return web.json_response({"error": code}, status=status)


async def read_json(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except web.HTTPException:
        raise
    except Exception:  # noqa: BLE001 - malformed JSON
        return None
    return body if isinstance(body, dict) else None


def throttled(request: web.Request) -> bool:
    """True when this member has used up their ~5 writes a second."""
    return not request.app[WRITE_LIMITER].allow(request[USER_ID], request.app[CLOCK]())


def bearer(request: web.Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def require_member(handler: Handler) -> Handler:
    """Reject without a valid session; otherwise set `request["user_id"]` (an int)."""

    async def wrapper(request: web.Request) -> web.StreamResponse:
        token = bearer(request)
        user_id = session.verify(request.app[SETTINGS].session_secret, token, request.app[CLOCK]()) if token else None
        if user_id is None:
            return error(401, "unauthorized")
        request[USER_ID] = user_id
        return await handler(request)

    return wrapper
