import os

import asyncpg
from auth import router as auth_router
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.birthdays import router as birthdays_router
from routers.logs import router as logs_router
from routers.moderation import router as moderation_router
from routers.overview import router as overview_router
from routers.presets import router as presets_router
from routers.queues import router as queues_router
from routers.suggestions import router as suggestions_router
from routers.voice import router as voice_router

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
CORS_ORIGINS = [
    origin.strip() for origin in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()
]

app = FastAPI(title="Bot Admin API", redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    app.state.pool = await asyncpg.create_pool(DATABASE_URL)


@app.on_event("shutdown")
async def shutdown():
    await app.state.pool.close()


# Auth router
app.include_router(auth_router)

app.include_router(logs_router, prefix="/api/logs")
app.include_router(moderation_router, prefix="/api/moderation")
app.include_router(queues_router, prefix="/api/queues")
app.include_router(presets_router, prefix="/api/presets")
app.include_router(suggestions_router, prefix="/api/suggestions")
app.include_router(overview_router, prefix="/api/overview")
app.include_router(voice_router, prefix="/api/voice")
app.include_router(birthdays_router, prefix="/api/birthdays")


@app.get("/health")
async def health():
    return {"status": "ok"}
