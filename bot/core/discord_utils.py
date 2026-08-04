from __future__ import annotations

import time

import discord
from loguru import logger

# How long to leave a card alone after Discord has told us, in as many words, to stop editing
# it. `discord.py` only raises a 429 to us after exhausting its own five internal retries, so
# by the time we see one the bucket is thoroughly saturated and another attempt is worse than
# useless.
RATE_LIMIT_BACKOFF = 60.0

# message_id -> fingerprint of what we last successfully rendered there.
_last_render: dict[int, int] = {}
# message_id -> monotonic deadline before which we must not touch it again.
_edit_cooldown: dict[int, float] = {}


async def pin(message: discord.Message) -> None:
    """Pin a bot-posted message, so a panel or board stays reachable once the channel scrolls.

    Everything called these "pinned" — the command descriptions, the docs — but nothing
    ever pinned them. Best-effort: missing Manage Messages, or a channel already at
    Discord's 50-pin cap, must not fail the whole setup.

    Lives here rather than in the setup cog because the betting boards pin themselves on a
    ticker, and setup imports the betting cog — so betting cannot import back from setup.
    """
    try:
        await message.pin()
    except discord.HTTPException as exc:
        logger.warning(f"Could not pin setup message {message.id}: {exc}")


def _fingerprint(embed: discord.Embed, view: discord.ui.View | None) -> int:
    """A stable hash of everything a viewer would actually notice.

    The embed's ``timestamp`` is deliberately excluded. Panels stamp it with "last checked",
    which changes on every single tick and would defeat the whole guard — an idle Palworld
    server would keep paying two edits a minute to re-render an identical card with a newer
    clock on it. Nobody is watching that number move; the API bill for it is real.
    """
    payload = embed.to_dict()
    payload.pop("timestamp", None)
    components = view.to_components() if view is not None else None
    return hash(repr((payload, components)))


async def edit_if_changed(
    message: discord.PartialMessage,
    *,
    label: str,
    embed: discord.Embed,
    view: discord.ui.View | None = discord.utils.MISSING,
) -> bool:
    """Edit a pinned card, but only when it would actually look different.

    Returns ``True`` when a request was issued. Panels are redrawn on a ticker, so the common
    case is a card whose rendering has not moved since the last tick — a progress bar is
    quantised into blocks, and a status card spends most of its life unchanged. Skipping those
    is what keeps a fixed-interval ticker from becoming a PATCH storm on one message id.

    Two failure modes are absorbed here so no caller has to think about them:

    - **NotFound** — an admin deleted the card by hand. `/setup status` counts missing
      messages, so swallowing it hides nothing; it just stops a deleted card from taking the
      ticker down on every tick from here on.
    - **429** — back off for ``RATE_LIMIT_BACKOFF`` instead of returning on the next tick to
      be refused again. `discord.ext.tasks` schedules from the *previous scheduled time*, not
      from when the body finished, so a body that overruns its interval re-fires with **zero**
      delay. Combined with an unconditional edit that is a self-sustaining storm that never
      recovers on its own, which is exactly how this bot spent an afternoon hammering one
      message every five and a half seconds.
    """
    now = time.monotonic()
    until = _edit_cooldown.get(message.id)
    if until is not None:
        if now < until:
            return False
        del _edit_cooldown[message.id]

    fingerprint = _fingerprint(embed, None if view is discord.utils.MISSING else view)
    if _last_render.get(message.id) == fingerprint:
        return False

    try:
        await message.edit(embed=embed, view=view)
    except discord.NotFound:
        _last_render.pop(message.id, None)
        logger.debug("{} card {} is gone — re-run its /setup.", label, message.id)
        return False
    except discord.HTTPException as exc:
        if exc.status == 429:
            _edit_cooldown[message.id] = now + RATE_LIMIT_BACKOFF
            logger.warning(
                "Rate limited editing the {} card {} — backing off {:.0f}s.",
                label,
                message.id,
                RATE_LIMIT_BACKOFF,
            )
        else:
            logger.warning(f"Could not edit the {label} card {message.id}: {exc}")
        return False

    # Only recorded on success, so a failed edit is retried rather than assumed applied.
    _last_render[message.id] = fingerprint
    return True


def reset_render_cache() -> None:
    """Forget every cached fingerprint and back-off. For tests.

    The cache is keyed by message id and lives for the process, which is right in production
    (a handful of panels) and wrong in a test suite, where fixtures reuse the same id and one
    test's render would make the next one silently skip its edit and pass without exercising
    anything. `tests/conftest.py` calls this between tests.
    """
    _last_render.clear()
    _edit_cooldown.clear()
