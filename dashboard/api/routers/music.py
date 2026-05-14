import os
from typing import Annotated

from db import get_db
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))


@router.get("")
async def get_music_state(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    state = await pool.fetchrow("SELECT * FROM music_state WHERE guild_id = $1", GUILD_ID)
    if state is None:
        return {"playing": False}

    queue = await pool.fetch(
        "SELECT * FROM music_queue WHERE guild_id = $1 ORDER BY position",
        GUILD_ID,
    )

    return {
        "playing": True,
        "track": {
            "title": state["track_title"],
            "author": state["track_author"],
            "uri": state["track_uri"],
            "length_ms": state["track_length_ms"],
            "artwork": state["track_artwork"],
            "requester_id": state["requester_id"],
        },
        "is_paused": state["is_paused"],
        "volume": state["volume"],
        "autoplay_enabled": state["autoplay_enabled"],
        "position_ms": state["position_ms"],
        "updated_at": state["updated_at"].isoformat(),
        "queue": [
            {
                "position": q["position"],
                "title": q["track_title"],
                "author": q["track_author"],
                "uri": q["track_uri"],
                "length_ms": q["track_length_ms"],
                "requester_id": q["requester_id"],
            }
            for q in queue
        ],
    }


async def _write_command(pool, guild_id: int, command: str, payload: dict | None = None) -> None:
    await pool.execute(
        "INSERT INTO music_commands (guild_id, command, payload) VALUES ($1, $2, $3)",
        guild_id,
        command,
        payload,
    )


def _require_playing(state) -> None:
    if state is None:
        raise HTTPException(status_code=404, detail="Nothing is playing")


@router.post("/pause")
async def toggle_pause(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await conn.fetchrow(
                "SELECT is_paused FROM music_state WHERE guild_id = $1 FOR UPDATE",
                GUILD_ID,
            )
            _require_playing(state)
            cmd = "resume" if state["is_paused"] else "pause"
            await conn.execute(
                "INSERT INTO music_commands (guild_id, command, payload) VALUES ($1, $2, $3)",
                GUILD_ID,
                cmd,
                None,
            )
    return {"command": cmd}


@router.post("/skip")
async def skip_track(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    state = await pool.fetchrow("SELECT guild_id FROM music_state WHERE guild_id = $1", GUILD_ID)
    _require_playing(state)
    await _write_command(pool, GUILD_ID, "skip")
    return {"command": "skip"}


@router.post("/stop")
async def stop_player(
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    state = await pool.fetchrow("SELECT guild_id FROM music_state WHERE guild_id = $1", GUILD_ID)
    _require_playing(state)
    await _write_command(pool, GUILD_ID, "stop")
    return {"command": "stop"}


class VolumeBody(BaseModel):
    volume: int = Field(..., ge=0, le=100)


@router.put("/volume")
async def set_volume(
    body: VolumeBody,
    _user: Annotated[dict, Depends(get_current_user)],
    pool=Depends(get_db),
):
    state = await pool.fetchrow("SELECT guild_id FROM music_state WHERE guild_id = $1", GUILD_ID)
    _require_playing(state)
    await _write_command(pool, GUILD_ID, "volume", {"volume": body.volume})
    return {"command": "volume", "volume": body.volume}
