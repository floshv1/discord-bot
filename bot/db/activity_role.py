"""The restricted Postgres role used by the (Internet-facing) LoLdle Activity container.

The game's code (`bot/cogs/loldle/service.py`, plus the currency paths it calls) runs in a
container that talks to strangers. If that container is compromised, the database login it holds
must open nothing but the game: no moderation history, no audit log, no betting. The bot owns the
schema, so it creates and maintains the role at every boot.

Grants are exactly what those code paths touch — nothing for `loldle_config` (the bot's own
channel/board bookkeeping), `currency_log_cursor`, betting, moderation, logs, birthdays or music.
There is no DELETE, no TRUNCATE and no DEFAULT PRIVILEGES: a table created tomorrow is closed to
this role until someone writes a grant for it here. `loldle_days` and `loldle_results` are
SELECT, INSERT only: the bot alone updates them (ticker, recap, announcements).

The wallet grant is deliberate and has a cost: `currency_wallets` is writable, so a compromised
container could still move coins (the ledger would then stop reconciling, but nothing here
prevents it).

The password is never logged and never sent as plaintext SQL: Postgres logs the text of a failing
statement by default, so we ship a SCRAM-SHA-256 verifier instead. Only the exception *type* is
logged. With no password configured, an existing role is switched off (NOLOGIN, sessions closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from loguru import logger

ROLE = "loldle_activity"

# 15: the Activity's pool (10) + its LISTEN connection + headroom. A compromised container must
# not be able to exhaust `max_connections` and take the bot down with it.
CONNECTION_LIMIT = 15
_ATTRIBUTES = f"LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {CONNECTION_LIMIT}"

# (privileges, tables). `currency_*` are reached through `get_or_create_wallet`,
# `ensure_house_wallet`, `get_balance_locked` (FOR UPDATE) and `adjust`.
_TABLE_GRANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SELECT, INSERT, UPDATE", ("loldle_players", "currency_wallets")),
    ("SELECT, INSERT", ("loldle_days", "loldle_results", "loldle_guesses", "loldle_hints", "currency_transactions")),
)

# `currency_transactions.id` is BIGSERIAL: inserting needs nextval on its sequence.
_SEQUENCE = "currency_transactions_id_seq"

_SCRAM_ITERATIONS = 4096
_SAFE_PASSWORD = re.compile(r"[A-Za-z0-9_-]+")


def scram_verifier(password: str) -> str:
    """A SCRAM-SHA-256 verifier in Postgres' storage format.

    ASCII only: SASLprep (RFC 4013) is not implemented, and normalising wrongly would lock the
    role out. The deployment generates hex, which is ASCII.
    """
    if not password.isascii():
        raise ValueError("password must be ASCII")
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode()

    return f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def _statements() -> list[str]:
    stmts = [
        # Re-runs must also *shrink* a role that was granted too much once.
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}",
        f"REVOKE CREATE ON SCHEMA public FROM {ROLE}",
        f"GRANT USAGE ON SCHEMA public TO {ROLE}",
    ]
    for privileges, tables in _TABLE_GRANTS:
        for table in tables:
            stmts.append(f"GRANT {privileges} ON {table} TO {ROLE}")
    stmts.append(f"GRANT USAGE ON SEQUENCE {_SEQUENCE} TO {ROLE}")
    return stmts


async def _terminate_sessions(pool) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = $1", ROLE)
    except Exception as exc:
        logger.error(f"Could not close the {ROLE} sessions: {type(exc).__name__}")


async def ensure_activity_role(pool, password: str | None) -> bool:
    """Create or refresh the restricted role; with no password, switch an existing one off.

    Idempotent; never raises. Returns True when the role is up to date and enabled.
    """
    if password and not _SAFE_PASSWORD.fullmatch(password):
        # compose.yml interpolates it into a URL: anything beyond [A-Za-z0-9_-] would corrupt the DSN.
        # Say what is wrong, never the value.
        logger.error(
            f"Could not set up the {ROLE} database role: ACTIVITY_DB_PASSWORD may only contain "
            "letters, digits, '_' and '-' (use `openssl rand -hex 32`)."
        )
        return False
    disabled = False
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE)
                if not password:
                    if not exists:
                        return False
                    await conn.execute(f"ALTER ROLE {ROLE} NOLOGIN PASSWORD NULL")
                    disabled = True
                else:
                    # DDL takes no bind parameters: the server quotes the literal for us. The
                    # literal is a verifier, so the plaintext never appears in any statement.
                    literal = await conn.fetchval("SELECT quote_literal($1::text)", scram_verifier(password))
                    if exists:
                        await conn.execute(f"ALTER ROLE {ROLE} {_ATTRIBUTES} PASSWORD {literal}")
                        memberships = await conn.fetch(
                            "SELECT quote_ident(g.rolname) AS name FROM pg_auth_members m "
                            "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.member "
                            "WHERE r.rolname = $1",
                            ROLE,
                        )
                        for row in memberships:
                            await conn.execute(f"REVOKE {row['name']} FROM {ROLE}")
                    else:
                        await conn.execute(f"CREATE ROLE {ROLE} {_ATTRIBUTES} PASSWORD {literal}")
                    database = await conn.fetchval("SELECT quote_ident(current_database())")
                    await conn.execute(f"GRANT CONNECT ON DATABASE {database} TO {ROLE}")
                    for stmt in _statements():
                        await conn.execute(stmt)
    except Exception as exc:
        # Type only: a failed statement's message may quote the statement.
        logger.error(f"Could not set up the {ROLE} database role: {type(exc).__name__}")
        return False
    if disabled:
        await _terminate_sessions(pool)
        logger.info(f"Database role {ROLE} is disabled (no password configured).")
        return False
    logger.info(f"Database role {ROLE} is up to date.")
    return True
