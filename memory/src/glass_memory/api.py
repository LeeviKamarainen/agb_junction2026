"""The frames API: post one batch of stills, get memories, a summary, a map and answers back.

    uvicorn glass_memory.api:app --host 127.0.0.1 --port 8001

Every route except `/` and `/health` needs `Authorization: Bearer <GLASS_API_TOKEN>`. The token
is read from the environment, and the app refuses to start when it is shorter than 32 characters.
A batch is stored in res/items/API-1/<batch>/ under the working directory: what `embed.store`
writes, plus `vectors/` (Gemma's image vectors of each keyframe), `summary.txt` and, once it
has been asked for, `vector_map.npy` (the map of those vectors).
"""

import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from glass_memory import ask, embed

log = logging.getLogger(__name__)
ROOT = Path("res/items/API-1")
PAGE = Path(__file__).parent / "web" / "index.html"
# A batch's id is the UTC time it was posted, to the microsecond. Only such an id names a folder.
ID, ID_PATTERN = "%Y%m%dT%H%M%S%f", re.compile(r"[0-9]{8}T[0-9]{12}")


def _token():
    token = os.environ.get("GLASS_API_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("GLASS_API_TOKEN must be set, to 32 characters or more")
    return token


@asynccontextmanager
async def _lifespan(app):
    _token()  # no token, no start
    if not ask.served():
        ask.gemma()  # loaded once and kept on the GPU, so no request waits for it
    yield


# No /docs or /openapi.json: they would show the routes to anyone.
app = FastAPI(
    title="glass-memory", lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None
)
batches = APIRouter(prefix="/batches")


@app.middleware("http")
async def _authorise(request, call_next):
    """401 for every path but the two open ones, before the body is read: a route dependency
    would run only after FastAPI had parsed, and spooled to disk, whatever was uploaded."""
    if request.url.path not in ("/", "/health"):
        scheme, _, given = request.headers.get("Authorization", "").partition(" ")
        # Bytes, because compare_digest raises on non-ASCII strings.
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            given.encode(), _token().encode()
        ):
            return JSONResponse(
                {"detail": "missing or wrong token"}, 401, {"WWW-Authenticate": "Bearer"}
            )
    return await call_next(request)


def _stored(batch):
    return ID_PATTERN.fullmatch(batch) and (ROOT / batch / "index.parquet").is_file()


def _folder(batch: str):
    if not _stored(batch):
        raise HTTPException(404, "no such batch")
    return ROOT / batch


Folder = Annotated[Path, Depends(_folder)]


def _info(folder):
    summary = folder / "summary.txt"
    return {
        "batch": folder.name,
        "created": datetime.strptime(folder.name, ID).replace(tzinfo=UTC).isoformat(),
        "memories": pl.read_parquet(folder / "index.parquet").height,
        "summary": summary.read_text() if summary.exists() else "",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def page():
    """The map page; anyone can load it, but it can read nothing without the token."""
    return FileResponse(PAGE)


@batches.post("")
def store_batch(
    frames: Annotated[list[UploadFile], File()],
    every_s: Annotated[float, Form(gt=0, allow_inf_nan=False)] = 1.0,
):
    """Store the frames (JPEGs in time order, `every_s` seconds apart, 1000 at most: the form
    parser's limit) as one batch of memories, with Gemma's image vectors of each. If Gemma fails
    the batch is still stored, with an empty summary."""
    stills = [frame.file.read() for frame in frames]
    if not all(still.startswith(b"\xff\xd8") for still in stills):
        raise HTTPException(415, "every frame must be a JPEG")
    folder = ROOT / datetime.now(UTC).strftime(ID)
    (folder / "keyframes").mkdir(parents=True)
    paths = [folder / "keyframes" / f"{i:05d}.jpg" for i in range(len(stills))]
    for path, still in zip(paths, stills, strict=True):
        path.write_bytes(still)
    times = [i * every_s for i in range(len(paths))]
    index = embed.store([(t, path.name) for t, path in zip(times, paths, strict=True)], folder)
    try:
        ask.store(paths)
        summary = ask.summarise(paths, times)
    except Exception:
        log.exception("batch %s is stored, but Gemma gave no summary", folder.name)
        summary = ""
    (folder / "summary.txt").write_text(summary)
    return {"batch": folder.name, "memories": len(index), "summary": summary}


@batches.get("")
def list_batches():
    """Every stored batch, newest first: ids sort by time."""
    return [
        _info(folder) for folder in sorted(ROOT.glob("*"), reverse=True) if _stored(folder.name)
    ]


@batches.get("/{batch}")
def batch_info(folder: Folder):
    return _info(folder)


def _own_gemma():
    if ask.served():
        raise HTTPException(400, "the Gemma API at GEMMA_URL reads stills only")


@batches.get("/{batch}/map")
def batch_map(folder: Folder, vectors: bool = False):
    """Each memory's place on the map of the embeddings, or with `vectors` on the map of
    Gemma's image vectors (one point per still, the mean over its tokens, the same layout). That
    map is made the first time it is asked for and kept."""
    index = pl.read_parquet(folder / "index.parquet").select("t_s", "x", "y", "keyframe")
    if vectors:
        path = folder / "vector_map.npy"
        if not path.exists():
            _own_gemma()
            np.save(path, embed.layout(ask.pooled(embed.load(folder)[0]["keyframe"].to_list())))
        xy = np.load(path)
        index = index.with_columns(x=pl.Series(xy[:, 0]), y=pl.Series(xy[:, 1]))
    return index.to_dicts()


@batches.get("/{batch}/keyframes/{name}")
def keyframe(folder: Folder, name: str):
    path = folder / "keyframes" / name
    if not re.fullmatch(r"\w+\.jpg", name) or not path.is_file():
        raise HTTPException(404, "no such keyframe")
    return FileResponse(path, media_type="image/jpeg")


class Question(BaseModel):
    question: str
    k: int = Field(4, ge=1, le=48)  # as many stills as a summary sends
    vectors: bool = False  # Gemma reads the stills, or its stored image vectors of them


@batches.post("/{batch}/ask")
def answer(folder: Folder, body: Question):
    """Gemma's answer from the k memories whose embeddings are closest to the question: from
    their stills, or with `vectors` from its stored image vectors of them. They are given, and
    their keyframes named, in time order."""
    if body.vectors:
        _own_gemma()
    index, embeddings = embed.load(folder)
    closest = index[np.sort(np.argsort(embeddings @ embed.embed_text(body.question))[-body.k :])]
    stills = closest["keyframe"].to_list()
    return {
        "answer": ask.ask(body.question, stills, closest["t_s"].to_list(), body.vectors),
        "keyframes": [Path(still).name for still in stills],
    }


class Texts(BaseModel):
    model: str = embed.MODEL
    input: str | Annotated[list[str], Field(min_length=1, max_length=16)]


@app.post("/v1/embeddings")
def embeddings(body: Texts):
    """OpenAI-style text embeddings in the stills' embedding space, for another service's search."""
    if body.model != embed.MODEL:
        raise HTTPException(400, f"only {embed.MODEL} is served")
    texts = [body.input] if isinstance(body.input, str) else body.input
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": embed.embed_text(text).tolist()}
            for i, text in enumerate(texts)
        ],
        "model": embed.MODEL,
    }


app.include_router(batches)
