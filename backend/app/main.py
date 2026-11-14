import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app import auth, demo
from app.db import get_engine, init_db, new_session
from app.llm import get_embedder
from app.routes import auth as auth_routes
from app.routes import demo as demo_routes
from app.routes import glasses, memories, notes
from app.settings import get_settings

log = logging.getLogger(__name__)
SWEEP_EVERY_S = 300


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    async with new_session() as session:
        await auth.ensure_admin(session)
        await auth.purge_expired_guests(session)
    sweeper = asyncio.create_task(_sweep_guests())
    loader = asyncio.create_task(_load_demo())
    yield
    sweeper.cancel()
    loader.cancel()
    await get_engine().dispose()


async def _load_demo() -> None:
    """Embed and store the demo dataset (app/demo.py) when it is not stored yet. In the
    background, so a slow or missing embedding model never holds up the start; the timeline works
    without it, only asking about the demo waits for it."""
    if not get_settings().embed_url:
        log.warning("No EMBED_URL: the demo dataset is not stored, so asking finds nothing")
        return
    try:
        async with new_session() as session:
            await demo.load_demo_memories(session, get_embedder())
    except Exception:
        log.exception("Storing the demo dataset failed; it is tried again on the next start")


async def _sweep_guests() -> None:
    """Deletes expired guests and their memories every few minutes, even with nobody logging in."""
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            async with new_session() as session:
                await auth.purge_expired_guests(session)
        except Exception:
            log.exception("Deleting expired guests failed")


# Everything lives under /api: in production Caddy sends /api/* here and the rest to Next.js.
app = FastAPI(
    title="Junction 2026",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

api = APIRouter(prefix="/api")


@api.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


api.include_router(auth_routes.router)
api.include_router(demo_routes.router)
api.include_router(notes.router)
api.include_router(memories.router)
api.include_router(glasses.router)
app.include_router(api)
