"""Stateless HMAC tokens: sessions (Authorization header) and tickets (<img> / WebSocket).

Domain separation: the signed message starts with a different tag for each kind, so a
session can never verify as a ticket (or the reverse), whatever the secret. Every verify
returns None on anything malformed; none of them raises.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

SESSION_TTL = 3600
TICKET_TTL = 60


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not text.isascii():
        raise ValueError("non-ascii")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, message: bytes) -> str:
    return _b64(hmac.new(secret.encode(), message, hashlib.sha256).digest())


def _issue(secret: str, tag: str, fields: list[str]) -> str:
    payload = "|".join([tag, *fields]).encode()
    return f"{_b64(payload)}.{_sign(secret, payload)}"


def _open(secret: str, tag: str, token: object, nfields: int) -> list[str] | None:
    try:
        if not isinstance(token, str):
            return None
        body, _, sig = token.partition(".")
        if not body or not sig or "." in sig:
            return None
        payload = _unb64(body)
        if not hmac.compare_digest(_sign(secret, payload).encode(), sig.encode("ascii")):
            return None
        parts = payload.decode("utf-8").split("|")
        if parts[0] != tag or len(parts) != nfields + 1:
            return None
        return parts[1:]
    except (ValueError, UnicodeError):
        return None


def issue(secret: str, user_id: int, now: float, ttl: int = SESSION_TTL) -> str:
    return _issue(secret, "session", [str(user_id), str(int(now) + ttl)])


def verify(secret: str, token: object, now: float) -> int | None:
    fields = _open(secret, "session", token, 2)
    if fields is None:
        return None
    try:
        user_id, exp = int(fields[0]), int(fields[1])
    except ValueError:
        return None
    return user_id if now < exp else None


def issue_ticket(secret: str, user_id: int, resource: str, now: float, ttl: int = TICKET_TTL) -> str:
    return _issue(secret, "ticket", [str(user_id), resource, str(int(now) + ttl)])


def verify_ticket(secret: str, ticket: object, resource: str, now: float) -> int | None:
    fields = _open(secret, "ticket", ticket, 3)
    if fields is None:
        return None
    try:
        user_id, exp = int(fields[0]), int(fields[2])
    except ValueError:
        return None
    if not hmac.compare_digest(fields[1].encode(), resource.encode()):
        return None
    return user_id if now < exp else None
