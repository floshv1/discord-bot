"""Static front serving and /api/config. Only the build directory is ever reachable."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from activity.server import app as app_mod
from activity.server.settings import Settings

SETTINGS = Settings("fake-client-id", "csecret", 1, "postgresql://x", "test-session-secret-0123456789abcdef")


@pytest.fixture
def dist(tmp_path, monkeypatch):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>front</html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "assets" / "sub").mkdir()
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    (root / "private.txt").write_text("not under assets", encoding="utf-8")
    monkeypatch.setenv("ACTIVITY_STATIC_DIR", str(root))
    return root


async def client_for(settings=SETTINGS):
    client = TestClient(TestServer(app_mod.create_app(settings, ping=lambda: _ok())))
    await client.start_server()
    return client


async def _ok():
    return None


async def test_root_serves_index_uncached(dist):
    client = await client_for()
    try:
        resp = await client.get("/")
        assert resp.status == 200
        assert "front" in await resp.text()
        assert resp.headers["Cache-Control"] == "no-store"
    finally:
        await client.close()


async def test_assets_are_cached_long(dist):
    client = await client_for()
    try:
        resp = await client.get("/assets/app.js")
        assert resp.status == 200
        assert "max-age=31536000" in resp.headers["Cache-Control"]
        assert "immutable" in resp.headers["Cache-Control"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../secret.txt",
        "/assets/%2e%2e/secret.txt",
        "/assets/%2e%2e%2fsecret.txt",
        "/assets/..%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/assets//etc/passwd",
        "/assets/%2fetc%2fpasswd",
        "/assets/",
        "/assets/sub",
        "/assets/sub/",
        "/private.txt",
        "/assets/%00",
    ],
)
async def test_nothing_outside_assets_is_reachable(dist, path):
    client = await client_for()
    try:
        resp = await client.get(path, skip_auto_headers=())
        body = await resp.text()
        assert resp.status in (400, 404)
        assert "TOP-SECRET" not in body
        assert "not under assets" not in body
    finally:
        await client.close()


async def test_missing_build_is_a_404_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_STATIC_DIR", str(tmp_path / "absent"))
    client = await client_for()
    try:
        assert (await client.get("/")).status == 404
        assert (await client.get("/assets/app.js")).status == 404
        assert (await client.get("/health")).status == 200
    finally:
        await client.close()


async def test_config_exposes_only_the_client_id(dist):
    client = await client_for()
    try:
        resp = await client.get("/api/config")
        assert resp.status == 200
        assert await resp.json() == {"client_id": "fake-client-id"}
    finally:
        await client.close()


async def test_config_is_503_when_unconfigured(dist):
    client = await client_for(None)
    try:
        assert (await client.get("/api/config")).status == 503
    finally:
        await client.close()
