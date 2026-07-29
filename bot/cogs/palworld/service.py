from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import StrEnum

import aiohttp
import asyncpg
from loguru import logger

from bot.db.client import get_pool

# --- timings ------------------------------------------------------------------------

# No automatic shutdown for this long after someone pressed Démarrer. The server needs
# ~90 s before it answers at all, and whoever clicked still has to launch the game and
# load in. Cutting the power under them is how a feature like this gets turned off.
START_GRACE = 20 * 60.0

# Empty for this long and the server goes down. There is deliberately no in-game warning
# beforehand: by construction nobody is connected to read it. The notice goes to Discord,
# after the fact.
EMPTY_TIMEOUT = 15 * 60.0

# How long a click is believed over what Komodo reports. Both executions are slow enough
# that the card would otherwise show the old state for a full tick — but a start that
# never comes up must not leave it reading "démarrage…" until the next restart either.
TRANSITION_TTL = 300.0

# Komodo runs the compose command synchronously: `docker compose stop` sends SIGTERM and
# waits out stop_grace_period (30 s in the palworld compose), so a stop legitimately takes
# most of a minute.
EXECUTE_TIMEOUT = aiohttp.ClientTimeout(total=180)
READ_TIMEOUT = aiohttp.ClientTimeout(total=10)
# The game's REST API answers in milliseconds once it is up, and not at all before. A
# generous timeout here would stall every tick for the whole boot window — which is
# exactly when the card most needs to keep redrawing.
REST_TIMEOUT = aiohttp.ClientTimeout(total=4)


class Status(StrEnum):
    """What the panel shows. Derived from two sources, never stored."""

    OFFLINE = "offline"
    BOOTING = "booting"
    ONLINE = "online"
    STOPPING = "stopping"
    # The stack exists but is not merely stopped — destroyed, paused, or in a state a
    # button cannot fix. `docker compose start` would fail, so the panel says so and
    # points at Komodo instead of pretending.
    DOWN = "down"
    # Komodo did not answer. We know nothing, and saying "hors ligne" would be a guess
    # that invites a pointless click.
    UNKNOWN = "unknown"


# Komodo's StackState enum, mapped to what a player cares about. Read from the OpenAPI
# document Komodo serves at :9120/docs — the full list is deploying, running, paused,
# stopped, created, restarting, dead, removing, unhealthy, down, unknown.
_RUNNING_STATES = frozenset({"running", "unhealthy"})
_BOOTING_STATES = frozenset({"deploying", "restarting"})
_OFFLINE_STATES = frozenset({"stopped", "created", "dead"})
_STOPPING_STATES = frozenset({"removing"})


@dataclass(frozen=True)
class Player:
    name: str
    level: int


@dataclass(frozen=True)
class ServerStatus:
    status: Status
    players: list[Player] = field(default_factory=list)

    @property
    def player_count(self) -> int:
        return len(self.players)


class KomodoError(Exception):
    """Komodo refused or never answered. Never shown to a member verbatim."""


def derive_status(
    stack_state: str | None,
    players: list[Player] | None,
    transition: tuple[str, float] | None = None,
    now: float = 0.0,
) -> Status:
    """Combine what Komodo says with what the game answers.

    ``players`` is ``None`` for "the REST API did not answer" — which is not the same as
    the empty list. A running stack whose game is still silent is the ~90 s boot window,
    and that distinction is the only thing that lets the card say "démarrage" instead of
    flapping between offline and online.

    ``transition`` is the optimistic state set by a click, ``("starting" | "stopping",
    monotonic time)``. It wins only while it is still plausible: a stopping stack that
    Komodo already reports as stopped is simply stopped, whatever the click said.
    """
    if transition is not None:
        kind, started_at = transition
        if now - started_at < TRANSITION_TTL:
            if kind == "starting" and players is None:
                return Status.BOOTING
            if kind == "stopping" and stack_state in _RUNNING_STATES:
                return Status.STOPPING

    if stack_state is None:
        return Status.UNKNOWN
    if stack_state in _RUNNING_STATES:
        return Status.ONLINE if players is not None else Status.BOOTING
    if stack_state in _BOOTING_STATES:
        return Status.BOOTING
    if stack_state in _STOPPING_STATES:
        return Status.STOPPING
    if stack_state in _OFFLINE_STATES:
        return Status.OFFLINE
    return Status.DOWN


@dataclass
class EmptyWatch:
    """Decides when an idle server should be shut down.

    Kept free of discord and aiohttp so the only non-trivial logic in this cog can be
    tested by moving a clock forward, rather than by waiting a quarter of an hour.
    """

    empty_since: float | None = None
    grace_until: float = 0.0

    def note_start(self, now: float) -> None:
        """A start was just requested — hold off on shutting it down again."""
        self.grace_until = now + START_GRACE
        self.empty_since = None

    def observe(self, status: Status, player_count: int, now: float) -> bool:
        """Feed one tick in. Returns ``True`` when the server should be stopped now.

        Anything other than a confirmed online-and-empty server resets the clock, so a
        boot window, a Komodo outage or a single connected player all postpone the
        shutdown rather than counting towards it.
        """
        if status is not Status.ONLINE or player_count > 0:
            self.empty_since = None
            return False
        if self.empty_since is None:
            self.empty_since = now
            return False
        if now < self.grace_until:
            return False
        return now - self.empty_since >= EMPTY_TIMEOUT


class Komodo:
    """The start/stop half. Holds an API key scoped to the palworld stack alone."""

    def __init__(self, url: str, key: str, secret: str, stack: str, session: aiohttp.ClientSession) -> None:
        self._url = url.rstrip("/")
        self._headers = {"X-Api-Key": key, "X-Api-Secret": secret}
        self._stack = stack
        self._session = session

    async def _call(self, route: str, type_: str, params: dict, timeout: aiohttp.ClientTimeout) -> dict | list:
        try:
            async with self._session.post(
                f"{self._url}{route}",
                json={"type": type_, "params": params},
                headers=self._headers,
                timeout=timeout,
            ) as response:
                body = await response.text()
                if response.status != 200:
                    raise KomodoError(f"{type_} returned HTTP {response.status}: {body[:200]}")
                return await response.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise KomodoError(f"{type_} could not reach Komodo: {exc}") from exc
        except TimeoutError as exc:
            raise KomodoError(f"{type_} timed out") from exc

    async def stack_state(self) -> str | None:
        """The stack's docker state, or ``None`` if Komodo could not be read.

        Uses ListStacks rather than GetStack: the live container state belongs to the
        *list item*, and asking for the full resource returns the configuration instead.
        """
        try:
            stacks = await self._call("/read", "ListStacks", {}, READ_TIMEOUT)
        except KomodoError as exc:
            logger.warning("Could not read the Palworld stack state: {}", exc)
            return None

        if not isinstance(stacks, list):
            logger.warning("ListStacks returned {} instead of a list.", type(stacks).__name__)
            return None
        for item in stacks:
            if item.get("name") == self._stack:
                state = item.get("info", {}).get("state")
                return str(state) if state else None
        logger.warning("Komodo knows no stack named {!r}.", self._stack)
        return None

    async def start(self) -> None:
        await self._call("/execute", "StartStack", {"stack": self._stack}, EXECUTE_TIMEOUT)

    async def stop(self) -> None:
        await self._call("/execute", "StopStack", {"stack": self._stack}, EXECUTE_TIMEOUT)


class PalworldRest:
    """The read half: who is connected, plus the two calls made before a shutdown.

    Reachable only because the bot joined ``palworld_net``. The port stays published on
    127.0.0.1 alone, so no other container on the machine — including the deliberately
    vulnerable ones — can talk to the game's admin API.
    """

    def __init__(self, url: str, admin_password: str, session: aiohttp.ClientSession) -> None:
        self._url = url.rstrip("/")
        credentials = base64.b64encode(f"admin:{admin_password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {credentials}"}
        self._session = session

    async def players(self) -> list[Player] | None:
        """Connected players, or ``None`` when the game did not answer.

        The ``None`` is load-bearing: it is what tells the panel a running container is
        still booting rather than empty, so nothing counts those 90 seconds towards an
        automatic shutdown.
        """
        try:
            async with self._session.get(
                f"{self._url}/v1/api/players", headers=self._headers, timeout=REST_TIMEOUT
            ) as response:
                if response.status != 200:
                    logger.debug("Palworld REST /players returned HTTP {}.", response.status)
                    return None
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            # Expected on every tick while the server is down. Not worth a log line each
            # time — the panel already says so, out loud, to everyone.
            return None

        entries = payload.get("players") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return None
        return [
            Player(name=str(entry.get("name", "?")), level=int(entry.get("level", 0) or 0))
            for entry in entries
            if isinstance(entry, dict)
        ]

    async def _post(self, path: str, payload: dict) -> bool:
        try:
            async with self._session.post(
                f"{self._url}{path}", json=payload, headers=self._headers, timeout=REST_TIMEOUT
            ) as response:
                return response.status < 300
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("Palworld REST {} failed: {}", path, exc)
            return False

    async def announce(self, message: str) -> bool:
        return await self._post("/v1/api/announce", {"message": message})

    async def save(self) -> bool:
        """Ask the game to write the world out before we take the container away.

        Belt and braces: `docker compose stop` sends SIGTERM, and the image's term_handler
        already runs a graceful shutdown that saves. This costs one request and removes
        any dependence on that handler still doing so after an image update.
        """
        return await self._post("/v1/api/save", {})


# --- panel configuration ------------------------------------------------------------


async def get_config(guild_id: int) -> asyncpg.Record | None:
    """The panel's channel and message id, or ``None`` if `/setup palworld` never ran."""
    pool = get_pool()
    return await pool.fetchrow(
        "SELECT guild_id, channel_id, panel_message_id FROM palworld_config WHERE guild_id = $1",
        guild_id,
    )


async def set_config(guild_id: int, channel_id: int, panel_message_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO palworld_config (guild_id, channel_id, panel_message_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
          SET channel_id       = EXCLUDED.channel_id,
              panel_message_id = EXCLUDED.panel_message_id
        """,
        guild_id,
        channel_id,
        panel_message_id,
    )


async def configured_guild_ids() -> list[int]:
    """Every guild with a panel — what the ticker iterates over."""
    pool = get_pool()
    rows = await pool.fetch("SELECT guild_id FROM palworld_config")
    return [row["guild_id"] for row in rows]
