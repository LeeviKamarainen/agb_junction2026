import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app import auth, faces
from app.db import get_engine, init_db, new_session
from app.routes import auth as auth_routes
from app.routes import glasses, memories, showcases

log = logging.getLogger(__name__)
SWEEP_EVERY_S = 300


@asynccontextmanager
async def lifespan(_app: FastAPI):
    faces.load()  # no face detector, no start: no image may be kept with its faces
    await init_db()
    async with new_session() as session:
        await auth.ensure_admin(session)
        await auth.purge_expired_guests(session)
    sweeper = asyncio.create_task(_sweep_guests())
    yield
    sweeper.cancel()
    await asyncio.gather(sweeper, return_exceptions=True)
    await get_engine().dispose()


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
api.include_router(showcases.router)
api.include_router(memories.router)
api.include_router(glasses.router)
app.include_router(api)
