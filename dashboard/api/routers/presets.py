import os
from typing import Annotated

import asyncpg
from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


class PresetCreate(BaseModel):
    name: str
    player_count: int


@router.get("")
async def list_presets(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    rows = await pool.fetch(
        "SELECT id, guild_id, name, player_count FROM game_presets WHERE guild_id=$1 ORDER BY name",
        guild_id,
    )

    return [dict(row) for row in rows]


@router.post("", status_code=201)
async def create_preset(
    body: PresetCreate,
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    try:
        row = await pool.fetchrow(
            "INSERT INTO game_presets (guild_id, name, player_count)"
            " VALUES ($1, $2, $3)"
            " RETURNING id, guild_id, name, player_count",
            guild_id,
            body.name,
            body.player_count,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="A preset with that name already exists for this guild")

    return dict(row)


@router.delete("/{preset_id}")
async def delete_preset(
    preset_id: int,
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    guild_id = GUILD_ID

    active = await pool.fetchval(
        "SELECT COUNT(*) FROM game_queues WHERE preset_id=$1 AND guild_id=$2 AND status IN ('open','filled')",
        preset_id,
        guild_id,
    )
    if active:
        raise HTTPException(status_code=409, detail="Preset has active queues — cancel them first")

    async with pool.acquire() as conn:
        async with conn.transaction():
            closed_ids = await conn.fetch(
                "SELECT id FROM game_queues WHERE preset_id=$1 AND guild_id=$2",
                preset_id,
                guild_id,
            )
            if closed_ids:
                ids = [r["id"] for r in closed_ids]
                await conn.execute("DELETE FROM queue_members WHERE queue_id = ANY($1::bigint[])", ids)
                await conn.execute("DELETE FROM game_queues WHERE id = ANY($1::bigint[])", ids)
            result = await conn.fetchrow(
                "DELETE FROM game_presets WHERE id=$1 AND guild_id=$2 RETURNING id",
                preset_id,
                guild_id,
            )

    if result is None:
        raise HTTPException(status_code=404, detail="Preset not found")

    return {"deleted": True}
