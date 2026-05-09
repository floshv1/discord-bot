async def resolve_users(pool, user_ids: list) -> dict:
    """Batch-resolve Discord user IDs to cached display info."""
    ids = list({i for i in user_ids if i is not None})
    if not ids:
        return {}
    rows = await pool.fetch(
        "SELECT user_id, username, display_name FROM discord_users WHERE user_id = ANY($1::bigint[])",
        ids,
    )
    return {row["user_id"]: dict(row) for row in rows}


def get_name(users: dict, user_id) -> str | None:
    if user_id is None:
        return None
    u = users.get(user_id)
    if not u:
        return None
    return u.get("display_name") or u.get("username")
