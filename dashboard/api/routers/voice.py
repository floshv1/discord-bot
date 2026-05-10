import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends
from utils import get_name, resolve_users

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


def _fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


@router.get("")
async def get_voice_leaderboard(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    weekly = await pool.fetch(
        """
        SELECT user_id,
               SUM(EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at))) AS total_seconds
        FROM voice_sessions
        WHERE guild_id = $1
          AND started_at > NOW() - INTERVAL '7 days'
        GROUP BY user_id
        ORDER BY total_seconds DESC
        LIMIT 10
        """,
        guild_id,
    )

    alltime = await pool.fetch(
        """
        SELECT user_id,
               SUM(EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at))) AS total_seconds
        FROM voice_sessions
        WHERE guild_id = $1
        GROUP BY user_id
        ORDER BY total_seconds DESC
        LIMIT 10
        """,
        guild_id,
    )

    all_ids = [r["user_id"] for r in weekly] + [r["user_id"] for r in alltime]
    users = await resolve_users(pool, all_ids)

    return {
        "weekly": [
            {
                "rank": i + 1,
                "user_id": r["user_id"],
                "user_name": get_name(users, r["user_id"]),
                "duration": _fmt_duration(r["total_seconds"]),
                "total_seconds": float(r["total_seconds"]),
            }
            for i, r in enumerate(weekly)
        ],
        "alltime": [
            {
                "rank": i + 1,
                "user_id": r["user_id"],
                "user_name": get_name(users, r["user_id"]),
                "duration": _fmt_duration(r["total_seconds"]),
                "total_seconds": float(r["total_seconds"]),
            }
            for i, r in enumerate(alltime)
        ],
    }
