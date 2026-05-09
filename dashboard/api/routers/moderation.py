import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends, Query
from utils import get_name, resolve_users

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


@router.get("")
async def list_mod_actions(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    action_type: str | None = Query(None),
    target_id: int | None = Query(None),
):
    guild_id = GUILD_ID
    offset = (page - 1) * page_size

    conditions = ["guild_id=$1"]
    params: list = [guild_id]
    idx = 2

    if action_type is not None:
        conditions.append(f"action_type=${idx}")
        params.append(action_type)
        idx += 1

    if target_id is not None:
        conditions.append(f"target_id=${idx}")
        params.append(target_id)
        idx += 1

    where = " AND ".join(conditions)

    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM mod_actions WHERE {where}",
        *params,
    )

    rows = await pool.fetch(
        f"""
        SELECT id, guild_id, target_id, moderator_id, action_type, reason, created_at
        FROM mod_actions
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        page_size,
        offset,
    )

    user_ids = [row["target_id"] for row in rows] + [row["moderator_id"] for row in rows]
    users = await resolve_users(pool, user_ids)

    items = [
        {
            "id": row["id"],
            "guild_id": row["guild_id"],
            "target_id": row["target_id"],
            "target_name": get_name(users, row["target_id"]),
            "moderator_id": row["moderator_id"],
            "moderator_name": get_name(users, row["moderator_id"]),
            "action_type": row["action_type"],
            "reason": row["reason"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
