import json
import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends
from utils import get_name, resolve_users

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


@router.get("")
async def get_overview(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    total_mod_actions = await pool.fetchval(
        "SELECT COUNT(*) FROM mod_actions WHERE guild_id=$1",
        guild_id,
    )

    open_queues = await pool.fetchval(
        "SELECT COUNT(*) FROM game_queues WHERE guild_id=$1 AND status='open'",
        guild_id,
    )

    open_suggestions = await pool.fetchval(
        "SELECT COUNT(*) FROM suggestions WHERE guild_id=$1 AND status='open'",
        guild_id,
    )

    audit_events_today = await pool.fetchval(
        "SELECT COUNT(*) FROM audit_logs WHERE guild_id=$1 AND created_at >= NOW() - INTERVAL '24 hours'",
        guild_id,
    )

    recent_rows = await pool.fetch(
        """
        SELECT event_type, actor_id, channel_id, details, created_at
        FROM audit_logs
        WHERE guild_id=$1
        ORDER BY created_at DESC
        LIMIT 10
        """,
        guild_id,
    )

    actor_ids = [row["actor_id"] for row in recent_rows]
    users = await resolve_users(pool, actor_ids)

    recent_activity = [
        {
            "event_type": row["event_type"],
            "actor_id": row["actor_id"],
            "actor_name": get_name(users, row["actor_id"]),
            "channel_id": row["channel_id"],
            "details": json.loads(row["details"]) if isinstance(row["details"], str) else row["details"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in recent_rows
    ]

    return {
        "total_mod_actions": total_mod_actions,
        "open_queues": open_queues,
        "open_suggestions": open_suggestions,
        "audit_events_today": audit_events_today,
        "recent_activity": recent_activity,
    }
