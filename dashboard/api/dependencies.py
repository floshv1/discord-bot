import os

from fastapi import Cookie, HTTPException
from jose import JWTError, jwt

SECRET = os.environ.get("DASHBOARD_SECRET", "")


async def get_current_user(token: str | None = Cookie(default=None)):
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
