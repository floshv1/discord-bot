import json
import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends, Query

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


@router.get("")
async def list_logs(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    event_type: str | None = Query(None),
    search: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    guild_id = GUILD_ID
    offset = (page - 1) * page_size

    conditions = ["guild_id=$1"]
    params: list = [guild_id]
    idx = 2

    if event_type is not None:
        conditions.append(f"event_type=${idx}")
        params.append(event_type)
        idx += 1

    if search is not None:
        conditions.append(f"details::text ILIKE ${idx}")
        params.append(f"%{search}%")
        idx += 1

    if date_from is not None:
        conditions.append(f"created_at >= ${idx}::timestamptz")
        params.append(date_from)
        idx += 1

    if date_to is not None:
        conditions.append(f"created_at <= ${idx}::timestamptz")
        params.append(date_to)
        idx += 1

    where = " AND ".join(conditions)

    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM audit_logs WHERE {where}",
        *params,
    )

    rows = await pool.fetch(
        f"""
        SELECT id, guild_id, event_type, actor_id, target_id, channel_id, details, created_at
        FROM audit_logs
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        page_size,
        offset,
    )

    items = [
        {
            "id": row["id"],
            "guild_id": row["guild_id"],
            "event_type": row["event_type"],
            "actor_id": row["actor_id"],
            "target_id": row["target_id"],
            "channel_id": row["channel_id"],
            "details": json.loads(row["details"]) if isinstance(row["details"], str) else row["details"],
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
