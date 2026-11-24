import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from activity.server import app as app_mod
from activity.server.keys import HTTP
from activity.server.session import issue, verify, verify_ticket
from activity.server.settings import Settings

SECRET = "test-session-secret-0123456789abcdef"
GUILD = 123456789012345678
SETTINGS = Settings("cid", "csecret", GUILD, "postgresql://x", SECRET)
NOW = 1_000_000.0
MEMBER_USER = {"user": {"id": "42"}}


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeHttp:
    """aiohttp-style client session: routes by URL suffix, records every call."""

    def __init__(self, routes):
        self.routes, self.calls, self.closed = routes, [], False

    def _go(self, method, url, **kw):
        self.calls.append((method, url, kw))
        # longest suffix first: "/users/@me" is a suffix of nothing else, but be explicit
        for suffix in sorted(self.routes, key=len, reverse=True):
            if url.endswith(suffix):
                r = self.routes[suffix]
                if isinstance(r, Exception):
                    raise r
                return FakeResp(*r)
        raise AssertionError(url)

    def post(self, url, **kw):
        return self._go("POST", url, **kw)

    def get(self, url, **kw):
        return self._go("GET", url, **kw)

    async def close(self):
        self.closed = True


def ok_routes(member=None, user=None):
    return {
        "/oauth2/token": (200, {"access_token": "AT-1"}),
        "/users/@me": (200, user or {"id": "42", "username": "user", "global_name": "Glob"}),
        f"/users/@me/guilds/{GUILD}/member": (200, MEMBER_USER | (member if member is not None else {"nick": "Nicky"})),
    }


@pytest.fixture
def upserts(monkeypatch):
    calls = []

    async def fake(guild_id, user_id, display_name, avatar_hash):
        calls.append((guild_id, user_id, display_name, avatar_hash))

    monkeypatch.setattr(app_mod.service, "upsert_player", fake)
    return calls


async def make_client(settings, http=None, ping=None):
    async def fine():
        return None

    app = app_mod.create_app(settings, http=http, clock=lambda: NOW, ping=ping or fine, listen=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_auth_returns_token_and_session(upserts):
    http = FakeHttp(ok_routes())
    c = await make_client(SETTINGS, http)
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 200
        body = await r.json()
        assert body["access_token"] == "AT-1" and body["user_id"] == "42"
        assert verify(SECRET, body["session"], NOW) == 42
        assert upserts == [(GUILD, 42, "Nicky", None)]
        assert http.calls[0][2]["data"]["code"] == "abc"
        assert http.calls[1][2]["headers"]["Authorization"] == "Bearer AT-1"
    finally:
        await c.close()


async def test_display_name_falls_back_to_global_name_then_username(upserts):
    c = await make_client(SETTINGS, FakeHttp(ok_routes({"nick": None, "avatar": "guildav"})))
    try:
        assert (await c.post("/api/auth", json={"code": "abc"})).status == 200
        assert upserts[0][2:] == ("Glob", "guildav")
    finally:
        await c.close()
    user = {"id": "42", "username": "user", "avatar": "uav"}
    c = await make_client(SETTINGS, FakeHttp(ok_routes({}, user)))
    try:
        await c.post("/api/auth", json={"code": "abc"})
        assert upserts[1][2:] == ("user", "uav")
    finally:
        await c.close()


async def test_auth_refuses_a_non_member(upserts):
    routes = ok_routes()
    routes[f"/users/@me/guilds/{GUILD}/member"] = (404, {"message": "Unknown Member"})
    c = await make_client(SETTINGS, FakeHttp(routes))
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 403
        assert await r.json() == {"error": "forbidden"}
        assert upserts == []
    finally:
        await c.close()


async def test_auth_failure_leaks_no_exception_text(upserts):
    routes = ok_routes()
    routes["/oauth2/token"] = RuntimeError("secret-dsn postgres://u:p@h")
    c = await make_client(SETTINGS, FakeHttp(routes))
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 502
        text = await r.text()
        assert "secret-dsn" not in text and "postgres" not in text
        assert json.loads(text) == {"error": "upstream"}
    finally:
        await c.close()


async def test_rejected_code_is_401(upserts):
    routes = ok_routes()
    routes["/oauth2/token"] = (400, {"error": "invalid_grant"})
    c = await make_client(SETTINGS, FakeHttp(routes))
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 401
        assert await r.json() == {"error": "unauthorized"}
    finally:
        await c.close()


async def test_unhandled_exception_is_generic_500(monkeypatch):
    async def boom(*a):
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(app_mod.service, "upsert_player", boom)
    c = await make_client(SETTINGS, FakeHttp(ok_routes()))
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 500
        assert "hunter2" not in await r.text()
        assert await r.json() == {"error": "internal"}
    finally:
        await c.close()


async def test_missing_settings_serves_503_on_api_and_health():
    c = await make_client(None)
    try:
        for method, path in [("get", "/health"), ("post", "/api/auth"), ("get", "/api/state")]:
            r = await getattr(c, method)(path)
            assert r.status == 503
    finally:
        await c.close()


async def test_health_ok_and_pool_down():
    up = {"ok": True}

    async def ping():
        if not up["ok"]:
            raise OSError("db down")

    c = await make_client(SETTINGS, FakeHttp({}), ping=ping)
    try:
        r = await c.get("/health")
        assert r.status == 200 and await r.text() == ""
        up["ok"] = False
        r = await c.get("/health")
        assert r.status == 503 and await r.text() == ""
    finally:
        await c.close()


async def test_oversized_body_is_rejected():
    c = await make_client(SETTINGS, FakeHttp({}))
    try:
        r = await c.post(
            "/api/auth",
            data=json.dumps({"code": "x" * 10_000}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 413
    finally:
        await c.close()


@pytest.mark.parametrize("body", [{}, {"code": 5}, {"code": ""}, [], "nope"])
async def test_bad_body_is_400(body):
    c = await make_client(SETTINGS, FakeHttp({}))
    try:
        r = await c.post("/api/auth", json=body)
        assert r.status == 400
        assert await r.json() == {"error": "bad_request"}
    finally:
        await c.close()


async def test_auth_global_rate_limit(upserts):
    c = await make_client(SETTINGS, FakeHttp(ok_routes()))
    try:
        statuses = [(await c.post("/api/auth", json={"code": "a"})).status for _ in range(31)]
        assert statuses[:30] == [200] * 30 and statuses[30] == 429
    finally:
        await c.close()


async def test_tickets_require_a_session_and_a_known_resource():
    c = await make_client(SETTINGS, FakeHttp({}))
    try:
        assert (await c.post("/api/tickets", json={"resource": "ws"})).status == 401
        h = {"Authorization": "Bearer " + issue(SECRET, 42, NOW)}
        r = await c.post("/api/tickets", json={"resource": "ws"}, headers=h)
        assert r.status == 200
        t = (await r.json())["ticket"]
        assert verify_ticket(SECRET, t, "ws", NOW) == 42
        assert (await c.post("/api/tickets", json={"resource": "admin"}, headers=h)).status == 400
        assert (await c.post("/api/tickets", json={}, headers=h)).status == 400
        bad = {"Authorization": "Bearer junk"}
        assert (await c.post("/api/tickets", json={"resource": "ws"}, headers=bad)).status == 401
        expired = {"Authorization": "Bearer " + issue(SECRET, 42, NOW - 4000)}
        assert (await c.post("/api/tickets", json={"resource": "ws"}, headers=expired)).status == 401
    finally:
        await c.close()


async def test_injected_http_is_not_closed_but_owned_one_is():
    http = FakeHttp({})
    c = await make_client(SETTINGS, http)
    await c.close()
    assert http.closed is False
    c = await make_client(SETTINGS)
    owned = c.server.app[HTTP]
    assert not owned.closed
    await c.close()
    assert owned.closed


def test_settings_from_env(monkeypatch):
    env = {
        "ACTIVITY_CLIENT_ID": "1",
        "ACTIVITY_CLIENT_SECRET": "s",
        "GUILD_ID": "99",
        "ACTIVITY_DATABASE_URL": "postgresql://x",
        "ACTIVITY_SESSION_SECRET": SECRET,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s and s.guild_id == 99 and s.client_id == "1"
    monkeypatch.setenv("ACTIVITY_CLIENT_SECRET", "")
    assert Settings.from_env() is None
    monkeypatch.delenv("ACTIVITY_CLIENT_SECRET")
    assert Settings.from_env() is None
    monkeypatch.setenv("ACTIVITY_CLIENT_SECRET", "s")
    monkeypatch.setenv("GUILD_ID", "abc")
    assert Settings.from_env() is None


def test_weak_session_secret_is_unconfigured(monkeypatch):
    env = {
        "ACTIVITY_CLIENT_ID": "1",
        "ACTIVITY_CLIENT_SECRET": "s",
        "GUILD_ID": "99",
        "ACTIVITY_DATABASE_URL": "postgresql://x",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ACTIVITY_SESSION_SECRET", "z" * 31)
    assert Settings.from_env() is None
    monkeypatch.setenv("ACTIVITY_SESSION_SECRET", "z" * 32)
    assert Settings.from_env() is not None


async def test_malformed_requests_do_not_spend_the_auth_bucket(upserts):
    c = await make_client(SETTINGS, FakeHttp(ok_routes()))
    try:
        for _ in range(40):
            assert (await c.post("/api/auth", json={"nope": 1})).status == 400
        assert (await c.post("/api/auth", json={"code": "a"})).status == 200
    finally:
        await c.close()


async def test_discord_401_on_token_exchange_is_a_502(upserts):
    routes = ok_routes()
    routes["/oauth2/token"] = (401, {"error": "invalid_client"})
    c = await make_client(SETTINGS, FakeHttp(routes))
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 502 and await r.json() == {"error": "upstream"}
    finally:
        await c.close()


@pytest.mark.parametrize("bad", [[], "x", None, {}, {"user": "x"}, {"user": {"id": "7"}}])
async def test_membership_check_fails_closed_on_malformed_200(upserts, bad):
    routes = ok_routes()
    routes[f"/users/@me/guilds/{GUILD}/member"] = (200, bad)
    c = await make_client(SETTINGS, FakeHttp(routes))
    try:
        r = await c.post("/api/auth", json={"code": "abc"})
        assert r.status == 502
        assert upserts == []
    finally:
        await c.close()
