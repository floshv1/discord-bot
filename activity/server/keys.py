"""Typed application and request keys, shared by every module (and by later tasks' routes)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
from aiohttp import web

from activity.server.limits import RateLimiter
from activity.server.settings import Settings

SETTINGS = web.AppKey("settings", Settings | None)
CLOCK = web.AppKey("clock", Callable[[], float])
HTTP = web.AppKey("http", aiohttp.ClientSession)
PING = web.AppKey("ping", Callable[[], Awaitable[None]])
AUTH_LIMITER = web.AppKey("auth_limiter", RateLimiter)
WRITE_LIMITER = web.AppKey("write_limiter", RateLimiter)
# Zero-argument callable returning the user ids currently connected (Task 8 swaps in the real presence).
ONLINE = web.AppKey("online", Callable[[], set[int]])
# Rendered image bytes: (day, kind) for hints and the result splash, id for icons. Bounded by their owners.
HINT_CACHE = web.AppKey("hint_cache", dict)
ICON_CACHE = web.AppKey("icon_cache", dict)
USER_ID = web.RequestKey("user_id", int)
ICON_FLIGHT = web.AppKey("icon_flight", dict)  # champion id -> the one shared upstream fetch
ICON_FAILED = web.AppKey("icon_failed", dict)  # champion id -> when it last failed (cooldown)
ICON_SLOTS = web.AppKey("icon_slots", asyncio.Semaphore)  # global cap on concurrent upstream fetches
HUB = web.AppKey("hub", object)  # live.Hub; typed loosely so keys.py does not import live.py
STATIC_DIR = web.AppKey("static_dir", Path)
