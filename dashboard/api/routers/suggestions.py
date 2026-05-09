import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))

ALLOWED_STATUSES = ["open", "accepted", "rejected", "implemented"]


class StatusUpdate(BaseModel):
    status: str


@router.get("/")
async def list_suggestions(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: str | None = Query(None),
    type: str | None = Query(None),
):
    guild_id = GUILD_ID
    offset = (page - 1) * page_size

    conditions = ["s.guild_id=$1"]
    params: list = [guild_id]
    idx = 2

    if status is not None:
        conditions.append(f"s.status=${idx}")
        params.append(status)
        idx += 1

    if type is not None:
        conditions.append(f"s.type=${idx}")
        params.append(type)
        idx += 1

    where = " AND ".join(conditions)

    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM suggestions s WHERE {where}",
        *params,
    )

    rows = await pool.fetch(
        f"""
        SELECT s.id, s.number, s.guild_id, s.author_id, s.type, s.content,
               s.status, s.message_id, s.created_at,
               COALESCE(SUM(CASE WHEN sv.vote = 1 THEN 1 ELSE 0 END), 0) as upvotes,
               COALESCE(SUM(CASE WHEN sv.vote = -1 THEN 1 ELSE 0 END), 0) as downvotes
        FROM suggestions s
        LEFT JOIN suggestion_votes sv ON sv.suggestion_id = s.id
        WHERE {where}
        GROUP BY s.id
        ORDER BY s.created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        page_size,
        offset,
    )

    items = [
        {
            "id": row["id"],
            "number": row["number"],
            "guild_id": row["guild_id"],
            "author_id": row["author_id"],
            "type": row["type"],
            "content": row["content"],
            "status": row["status"],
            "message_id": row["message_id"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "upvotes": int(row["upvotes"]),
            "downvotes": int(row["downvotes"]),
        }
        for row in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/{suggestion_id}/status")
async def update_suggestion_status(
    suggestion_id: int,
    body: StatusUpdate,
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    if body.status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {', '.join(ALLOWED_STATUSES)}",
        )

    guild_id = GUILD_ID

    row = await pool.fetchrow(
        "UPDATE suggestions SET status=$1 WHERE id=$2 AND guild_id=$3 RETURNING id, status",
        body.status,
        suggestion_id,
        guild_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    return {"id": row["id"], "status": row["status"]}
