import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import chat, pipeline, schedule, stars
from config import settings
from services.chromadb_client import get_client as get_chromadb_client

app = FastAPI(title="Project Warszawa API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(stars.router)
app.include_router(pipeline.router)
app.include_router(chat.router)
app.include_router(schedule.router)


@app.get("/health")
async def health() -> dict:
    try:
        await asyncio.to_thread(get_chromadb_client().heartbeat)
        chromadb_status = "ok"
    except Exception:
        chromadb_status = "unavailable"

    return {
        "status": "ok",
        "chromadb": chromadb_status,
        "anthropic": "configured" if settings.anthropic_api_key else "missing",
    }
