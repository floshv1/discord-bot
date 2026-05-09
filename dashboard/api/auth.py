import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from dependencies import get_current_user
from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from jose import jwt

DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "http://localhost:3100/auth/callback")
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "")
GUILD_ID = os.environ.get("GUILD_ID", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3100/overview")

DISCORD_API_BASE = "https://discord.com/api"

router = APIRouter(prefix="/auth")


def _build_oauth_url(state: str) -> str:
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": state,
    }
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


def _issue_jwt(user_id: int, username: str, avatar: str | None) -> str:
    expiry = datetime.now(tz=UTC) + timedelta(hours=24)
    payload = {
        "sub": str(user_id),
        "username": username,
        "avatar": avatar,
        "exp": expiry,
    }
    return jwt.encode(payload, DASHBOARD_SECRET, algorithm="HS256")


@router.get("/login")
async def login():
    """Redirect the browser to Discord's OAuth2 authorization page."""
    state = secrets.token_hex(16)
    url = _build_oauth_url(state)
    response = RedirectResponse(url=url)
    # Store state in a short-lived cookie for CSRF validation at callback
    response.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
):
    """Exchange the Discord auth code for tokens, verify guild membership, issue JWT."""
    # CSRF check
    if not oauth_state or state != oauth_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    async with httpx.AsyncClient() as client:
        # 1. Exchange code for access token
        token_resp = await client.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to exchange code with Discord")

        token_data = token_resp.json()
        access_token: str = token_data["access_token"]
        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # 2. Fetch the user's identity
        user_resp = await client.get(f"{DISCORD_API_BASE}/users/@me", headers=auth_headers)
        if user_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch user from Discord")

        user_data = user_resp.json()
        user_id: int = int(user_data["id"])
        username: str = user_data["username"]
        avatar: str | None = user_data.get("avatar")

        # 3. Verify the user is a member of the configured guild
        member_resp = await client.get(
            f"{DISCORD_API_BASE}/users/@me/guilds/{GUILD_ID}/member",
            headers=auth_headers,
        )
        if member_resp.status_code == 404 or member_resp.status_code == 403:
            raise HTTPException(status_code=403, detail="You are not a member of this server")
        if member_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to verify guild membership")

    # 4. Issue JWT and set as httpOnly cookie, then redirect to frontend
    token = _issue_jwt(user_id, username, avatar)
    response = RedirectResponse(url=FRONTEND_URL)
    response.delete_cookie("oauth_state")
    response.set_cookie(
        key="token",
        value=token,
        max_age=86400,  # 24 hours
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/me")
async def me(token: str | None = Cookie(default=None)):
    """Return the currently authenticated user's info from the JWT."""
    user = await get_current_user(token)
    return {
        "id": user["sub"],
        "username": user["username"],
        "avatar": user.get("avatar"),
    }


@router.post("/logout")
async def logout():
    """Clear the auth cookie."""
    response = JSONResponse(content={"status": "logged out"})
    response.delete_cookie("token")
    return response
