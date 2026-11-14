from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app import auth
from app.db import get_engine, init_db, new_session
from app.routes import auth as auth_routes
from app.routes import glasses, memories, notes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    async with new_session() as session:
        await auth.ensure_admin(session)
    yield
    await get_engine().dispose()


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
