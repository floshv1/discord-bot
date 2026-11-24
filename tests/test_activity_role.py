from __future__ import annotations

import re
from pathlib import Path

import pytest
from loguru import logger

from bot.db import activity_role
from bot.db.activity_role import ROLE, ensure_activity_role

SECRET = "s3cr3t-Pass_word"


class FakeConn:
    def __init__(self, *, fail_on: str | None = None, exists: bool = False, memberships=()):
        self.memberships = list(memberships)
        self.executed: list[str] = []
        self.fail_on = fail_on
        self.exists = exists

    async def fetchval(self, sql, *args):
        if "quote_literal" in sql:
            return "'" + args[0].replace("'", "''") + "'"
        if "pg_roles" in sql:
            return 1 if self.exists else None
        if "current_database" in sql:
            return "db"
        return None

    async def fetch(self, sql, *args):
        return [{"name": n} for n in self.memberships]

    async def execute(self, sql, *args):
        self.executed.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"boom while running: {sql}")  # message carries the SQL, hence the password

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Tx()


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Acq()


@pytest.fixture
def logs():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG", diagnose=False)
    yield lines
    logger.remove(sink)


@pytest.mark.parametrize("pw", [None, ""])
async def test_no_password_creates_nothing(pw):
    conn = FakeConn()
    assert await ensure_activity_role(FakePool(conn), pw) is False
    assert conn.executed == []


@pytest.mark.parametrize("pw", [None, ""])
async def test_no_password_switches_an_existing_role_off(pw):
    conn = FakeConn(exists=True)
    assert await ensure_activity_role(FakePool(conn), pw) is False
    assert conn.executed[0] == f"ALTER ROLE {ROLE} NOLOGIN PASSWORD NULL"
    assert "pg_terminate_backend" in conn.executed[-1]
    assert not any("GRANT" in q for q in conn.executed)


async def test_plaintext_password_is_never_in_any_statement():
    conn = FakeConn()
    await ensure_activity_role(FakePool(conn), SECRET)
    assert not any(SECRET in q for q in conn.executed)
    assert "PASSWORD 'SCRAM-SHA-256$4096:" in " ".join(conn.executed)


async def test_non_ascii_password_is_refused_without_touching_the_role(logs):
    conn = FakeConn(exists=True)
    assert await ensure_activity_role(FakePool(conn), "qäzxörd") is False
    assert conn.executed == []
    assert not any("zx" in line for line in logs)


async def test_memberships_are_revoked():
    conn = FakeConn(exists=True, memberships=['"pg_monitor"'])
    await ensure_activity_role(FakePool(conn), SECRET)
    assert f'REVOKE "pg_monitor" FROM {ROLE}' in conn.executed


def test_scram_verifier_is_consistent_with_its_salt():
    import base64
    import hashlib
    import hmac

    head, rest = activity_role.scram_verifier("pencil").split("$", 1)
    iters, salt = rest.split("$")[0].split(":")
    stored, server = rest.split("$")[1].split(":")
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", base64.b64decode(salt), int(iters))
    ck = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    assert head == "SCRAM-SHA-256" and iters == "4096"
    assert base64.b64decode(stored) == hashlib.sha256(ck).digest()
    assert base64.b64decode(server) == hmac.new(salted, b"Server Key", hashlib.sha256).digest()


async def test_creates_role_with_restricted_attributes():
    conn = FakeConn()
    assert await ensure_activity_role(FakePool(conn), SECRET) is True
    sql = "\n".join(conn.executed)
    assert f"CREATE ROLE {ROLE}" in sql
    for attr in ("LOGIN", "NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE"):
        assert attr in sql


async def test_existing_role_is_altered_and_attributes_reasserted():
    conn = FakeConn(exists=True)
    assert await ensure_activity_role(FakePool(conn), SECRET) is True
    sql = "\n".join(conn.executed)
    assert "CREATE ROLE" not in sql
    assert f"ALTER ROLE {ROLE}" in sql
    assert "NOSUPERUSER" in sql and "NOCREATEDB" in sql and "NOCREATEROLE" in sql


async def test_password_is_quoted_as_a_literal():
    conn = FakeConn()
    await ensure_activity_role(FakePool(conn), SECRET)
    assert SECRET not in "\n".join(conn.executed)


async def test_password_is_never_logged(logs):
    conn = FakeConn()
    await ensure_activity_role(FakePool(conn), SECRET)
    assert not any(SECRET in line or "s3cr3t" in line for line in logs)


@pytest.mark.parametrize("fail_on", ["ROLE", "GRANT SELECT"])
async def test_password_is_never_logged_on_sql_error(logs, fail_on):
    conn = FakeConn(fail_on=fail_on)
    assert await ensure_activity_role(FakePool(conn), SECRET) is False  # never raises
    assert logs, "a failure must be logged"
    assert not any("s3cr3t" in line for line in logs)
    assert any("RuntimeError" in line for line in logs)


async def test_grants_cover_only_the_game_tables():
    conn = FakeConn()
    await ensure_activity_role(FakePool(conn), SECRET)
    sql = "\n".join(conn.executed)
    lowered = sql.lower()
    for forbidden in (
        "moderation",
        "warn",
        "audit",
        "betting",
        "birthday",
        "music",
        "currency_log_cursor",
        "loldle_config",
        "voice",
        "reprimand",
        "customs",
        "all privileges",
        "default privileges",
        "delete",
        "truncate",
    ):
        assert forbidden not in lowered, forbidden
    assert "SELECT, INSERT, UPDATE ON loldle_days" not in sql
    assert "SELECT, INSERT, UPDATE ON loldle_results" not in sql
    granted = set(re.findall(r"ON (?:TABLE )?(\w+) TO", sql))
    assert {
        "loldle_days",
        "loldle_results",
        "loldle_players",
        "currency_wallets",
        "loldle_guesses",
        "loldle_hints",
        "currency_transactions",
    } <= granted
    assert "SEQUENCE currency_transactions_id_seq" in sql


async def test_runs_after_migrations():
    src = (Path(__file__).parent.parent / "bot" / "core" / "bot.py").read_text(encoding="utf-8")
    assert src.index("run_migrations(pool") < src.index("ensure_activity_role(")
    assert "activity_db_password" in src


def test_module_exposes_role_name():
    assert activity_role.ROLE == "loldle_activity"


def test_config_reads_password(monkeypatch):
    from bot.core.config import Config

    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("LOG_CHANNEL_ID", "2")
    monkeypatch.delenv("ACTIVITY_DB_PASSWORD", raising=False)
    assert Config().activity_db_password is None
    monkeypatch.setenv("ACTIVITY_DB_PASSWORD", "pw")
    assert Config().activity_db_password == "pw"


@pytest.mark.parametrize("bad", ["p@ss", "a/b", "a:b", "a b", "a%20b", "a?b#c", "a'b"])
async def test_url_unsafe_password_is_refused_without_touching_the_role(logs, bad):
    conn = FakeConn(exists=True)
    ok = await ensure_activity_role(FakePool(conn), bad)
    assert ok is False
    assert conn.executed == []
    assert any("ACTIVITY_DB_PASSWORD" in line for line in logs)
    assert not any(bad in line for line in logs if bad.strip())


async def test_role_has_a_connection_limit():
    conn = FakeConn(exists=True)
    await ensure_activity_role(FakePool(conn), SECRET)
    alter = next(q for q in conn.executed if q.startswith("ALTER ROLE") and "PASSWORD" in q)
    assert "CONNECTION LIMIT 15" in alter
    conn = FakeConn(exists=False)
    await ensure_activity_role(FakePool(conn), SECRET)
    create = next(q for q in conn.executed if q.startswith("CREATE ROLE"))
    assert "CONNECTION LIMIT 15" in create
