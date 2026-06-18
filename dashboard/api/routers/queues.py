import asyncio
import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from utils import get_name, resolve_users

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


@router.get("")
async def list_queues(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    queues = await pool.fetch(
        """
        SELECT gq.id, gq.guild_id, gq.channel_id, gq.preset_id, gq.status,
               gq.created_at, gq.filled_at, gq.start_time, gq.reminder_sent,
               gq.creator_user_id,
               gp.name as game_name, COALESCE(gq.player_count, gp.player_count) as player_count
        FROM game_queues gq
        JOIN game_presets gp ON gp.id = gq.preset_id
        WHERE gq.guild_id = $1 AND gq.status IN ('open', 'filled')
        ORDER BY gq.created_at DESC
        """,
        guild_id,
    )

    if not queues:
        return []

    members_list = await asyncio.gather(
        *[
            pool.fetch(
                "SELECT user_id, joined_at, in_lane, cant_attend"
                " FROM queue_members WHERE queue_id = $1 ORDER BY joined_at",
                q["id"],
            )
            for q in queues
        ]
    )

    all_user_ids = [q["creator_user_id"] for q in queues]
    for members in members_list:
        all_user_ids.extend(m["user_id"] for m in members)
    users = await resolve_users(pool, all_user_ids)

    result = []
    for queue, members in zip(queues, members_list):
        result.append(
            {
                "id": queue["id"],
                "guild_id": queue["guild_id"],
                "channel_id": queue["channel_id"],
                "preset_id": queue["preset_id"],
                "status": queue["status"],
                "created_at": queue["created_at"].isoformat() if queue["created_at"] else None,
                "filled_at": queue["filled_at"].isoformat() if queue["filled_at"] else None,
                "start_time": queue["start_time"].isoformat() if queue["start_time"] else None,
                "reminder_sent": queue["reminder_sent"],
                "creator_user_id": queue["creator_user_id"],
                "creator_name": get_name(users, queue["creator_user_id"]),
                "game_name": queue["game_name"],
                "player_count": queue["player_count"],
                "members": [
                    {
                        "user_id": m["user_id"],
                        "display_name": get_name(users, m["user_id"]),
                        "joined_at": m["joined_at"].isoformat() if m["joined_at"] else None,
                        "in_lane": m["in_lane"],
                        "cant_attend": m["cant_attend"],
                    }
                    for m in members
                ],
            }
        )

    return result


@router.delete("/{queue_id}")
async def cancel_queue(
    queue_id: int,
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    result = await pool.fetchrow(
        "UPDATE game_queues SET status='cancelled' WHERE id=$1 AND guild_id=$2 RETURNING id",
        queue_id,
        guild_id,
    )

    if result is None:
        raise HTTPException(status_code=404, detail="Queue not found")

    return {"cancelled": True}
