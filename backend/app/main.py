import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlmodel import Session

from app import auth
from app.db import get_engine, init_db
from app.routes import auth as auth_routes
from app.routes import glasses, memories, notes

log = logging.getLogger(__name__)
SWEEP_EVERY_S = 300


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with Session(get_engine()) as session:
        auth.ensure_admin(session)
        auth.purge_expired_guests(session)
    sweeper = asyncio.create_task(_sweep_guests())
    yield
    sweeper.cancel()


def _purge() -> None:
    with Session(get_engine()) as session:
        auth.purge_expired_guests(session)


async def _sweep_guests() -> None:
    """Deletes expired guests and their memories every few minutes, even with nobody logging in."""
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            await asyncio.to_thread(_purge)
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
api.include_router(notes.router)
api.include_router(memories.router)
api.include_router(glasses.router)
app.include_router(api)
