from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.db import init_db
from app.routes import memories, notes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


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


api.include_router(notes.router)
api.include_router(memories.router)
app.include_router(api)
