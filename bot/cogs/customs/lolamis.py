"""HTTP client for lol-amis, which owns everything LoL: players, ratings, team generation, games.

The bot never re-implements balancing — it asks. Every failure (network, timeout, HTTP ≥ 400,
bad JSON) becomes a `LolAmisError`, whose text is for the logs only: replies to members say
"the generator isn't answering", never what went wrong inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

# The lol-amis UI's own defaults (frontend/src/routes/Generator.svelte, DEFAULT_OPTS) — not the
# backend's, which default `duo` to False. Teams built in Discord must look like the ones the
# group already trusts from the app.
UI_OPTS: dict[str, Any] = {"lanes": "roles", "lane": True, "profile": True, "mix": True, "rand": 2, "duo": True}

Side = Literal["blue", "red"]


class LolAmisError(Exception):
    """lol-amis could not answer. The message is safe to log, never to show."""


class LinkTaken(LolAmisError):
    """That Discord account is already linked to another (active) player."""

    def __init__(self, holder_id: int, holder_name: str) -> None:
        super().__init__(f"discord id already linked to person {holder_id}")
        self.holder_id = holder_id
        self.holder_name = holder_name


@dataclass(frozen=True)
class Person:
    id: int
    name: str
    rank_label: str
    discord_id: str | None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Slot:
    person_id: int
    lane: int


@dataclass(frozen=True)
class Teams:
    blue: tuple[Slot, ...]
    red: tuple[Slot, ...]
    p_blue: float

    def to_json(self) -> dict:
        return {
            "blue": [{"person_id": s.person_id, "lane": s.lane} for s in self.blue],
            "red": [{"person_id": s.person_id, "lane": s.lane} for s in self.red],
            "p_blue": self.p_blue,
        }

    @classmethod
    def from_json(cls, data: dict) -> Teams:
        def side(rows: list[dict]) -> tuple[Slot, ...]:
            return tuple(Slot(int(r["person_id"]), int(r["lane"])) for r in rows)

        return cls(side(data["blue"]), side(data["red"]), float(data.get("p_blue", 0.5)))

    def person_ids(self) -> list[int]:
        return [s.person_id for s in (*self.blue, *self.red)]


def _person(d: dict) -> Person:
    return Person(
        int(d["id"]),
        str(d["name"]),
        str(d.get("rank_label") or ""),
        d.get("discord_id"),
        tuple(str(a) for a in d.get("aliases") or ()),
    )


class LolAmisClient:
    TIMEOUT = aiohttp.ClientTimeout(total=10)

    def __init__(self, url: str, session: aiohttp.ClientSession) -> None:
        self._base = url.rstrip("/")
        self._session = session

    async def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        try:
            async with self._session.request(method, url, json=body, timeout=self.TIMEOUT) as resp:
                try:
                    data = await resp.json(content_type=None)
                except ValueError as exc:
                    raise LolAmisError(f"{method} {path}: invalid JSON") from exc
                if resp.status == 409 and isinstance(data, dict) and isinstance(data.get("detail"), dict):
                    d = data["detail"]
                    raise LinkTaken(int(d["holder_id"]), str(d["holder_name"]))
                if resp.status >= 400:
                    raise LolAmisError(f"{method} {path}: HTTP {resp.status}")
                return data
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise LolAmisError(f"{method} {path}: {type(exc).__name__}") from exc

    async def list_persons(self) -> list[Person]:
        return [_person(d) for d in await self._call("GET", "/api/persons")]

    async def create_person(self, name: str, discord_id: str) -> Person:
        return _person(await self._call("POST", "/api/persons", {"name": name, "discord_id": discord_id}))

    async def link(self, person_id: int, discord_id: str | None) -> Person:
        body = {"discord_id": discord_id or ""}  # "" unlinks
        return _person(await self._call("PATCH", f"/api/persons/{person_id}", body))

    async def rename(self, person_id: int, name: str, aliases: list[str]) -> Person:
        """Rename a player, sending the full alias list (lol-amis replaces it wholesale)."""
        return _person(await self._call("PATCH", f"/api/persons/{person_id}", {"name": name, "aliases": aliases}))

    async def generate(self, person_ids: list[int]) -> Teams:
        return Teams.from_json(await self._call("POST", "/api/generate", {"person_ids": person_ids, "opts": UI_OPTS}))

    async def evaluate(self, teams: Teams) -> Teams:
        body = {**_sides(teams), "opts": UI_OPTS}
        return Teams.from_json(await self._call("POST", "/api/generate/evaluate", body))

    async def record_game(self, teams: Teams, winner: Side) -> int:
        body = {**_sides(teams), "winner": winner, "source": "generateur"}
        return int((await self._call("POST", "/api/games", body))["id"])

    async def find_game(self, teams: Teams, *, exclude: set[int]) -> int | None:
        """The id of a "generateur" game with exactly these sides and lanes, if lol-amis has one."""
        want = (frozenset(teams.blue), frozenset(teams.red))
        for g in await self._call("GET", "/api/games"):
            if g.get("source") != "generateur" or int(g["id"]) in exclude:
                continue
            got = Teams.from_json({**g, "p_blue": 0.5})
            if (frozenset(got.blue), frozenset(got.red)) == want:
                return int(g["id"])
        return None

    async def set_winner(self, game_id: int, teams: Teams, winner: Side) -> None:
        body = {**_sides(teams), "winner": winner, "source": "generateur"}
        await self._call("PUT", f"/api/games/{game_id}", body)


def _sides(teams: Teams) -> dict:
    j = teams.to_json()
    return {"blue": j["blue"], "red": j["red"]}
