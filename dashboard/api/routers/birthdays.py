import datetime
import os
from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends
from utils import get_name, resolve_users

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
PARIS_TZ = ZoneInfo("Europe/Paris")


def _next_occurrence(day: int, month: int, today: date) -> date:
    try:
        d = date(today.year, month, day)
        if d < today:
            d = date(today.year + 1, month, day)
        return d
    except ValueError:
        return date(today.year + 1, month, day)


@router.get("")
async def get_birthdays(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    rows = await pool.fetch(
        "SELECT user_id, username, day, month, year FROM birthdays WHERE guild_id = $1",
        guild_id,
    )

    today = datetime.datetime.now(PARIS_TZ).date()
    items = []
    for r in rows:
        next_bd = _next_occurrence(r["day"], r["month"], today)
        delta = (next_bd - today).days
        age = next_bd.year - r["year"]
        items.append(
            {
                "user_id": r["user_id"],
                "username": r["username"],
                "day": r["day"],
                "month": r["month"],
                "year": r["year"],
                "next_birthday": next_bd.isoformat(),
                "days_until": delta,
                "age": age,
            }
        )

    items.sort(key=lambda x: x["days_until"])

    user_ids = [item["user_id"] for item in items]
    users = await resolve_users(pool, user_ids)
    for item in items:
        item["display_name"] = get_name(users, item["user_id"])

    return items
