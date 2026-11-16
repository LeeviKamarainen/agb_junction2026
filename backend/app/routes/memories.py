import base64
import struct
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import agent, demo, demo_bundles, faces, media, vectors
from app.auth import DEMO_OWNER, Viewer, ViewerDep
from app.db import SessionDep, new_session
from app.glasses import feeds
from app.llm import Chat, Embedder, InputType, UpstreamError, get_chat, get_embedder
from app.models import (
    CollectionCount,
    Memory,
    MemoryPublic,
    MemoryVector,
    Showcase,
    TextMemoryCreate,
    User,
)
from app.search import (
    TABLE,
    SearchResult,
    VectorSearchArgs,
    add_memory,
    clean_collection,
    public,
    vector_search,
    width_error,
)
from app.settings import get_settings

router = APIRouter(tags=["memories"])

ChatDep = Annotated[Chat, Depends(get_chat)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]

MAX_UPLOAD = 25 * 1024 * 1024  # bytes per image, before it is shrunk
MAX_VECTORS = 2000  # memories /memories/vectors sends at most, newest first

CollectionsQuery = Annotated[
    list[str], Query(description="Only these collections; leave out for every one")
]


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _collection(name: str | None) -> str:
    try:
        return clean_collection(name)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def _stored_name(name: str) -> str:
    """A collection name as stored, or as given when it is not allowed (it then matches none)."""
    try:
        return clean_collection(name)
    except ValueError:
        return name


def _visible(query, viewer: Viewer, mine: bool = False):
    """`query` limited to the memories `viewer` may see, or with `mine` to their own."""
    owners = viewer.scope(mine)
    return query if owners is None else query.where(col(Memory.owner_id).in_(owners))


def _newest(collections: list[str], viewer: Viewer, query=None, mine: bool = False):
    """`query` (of memories when not given) limited to those `viewer` may see, or with `mine` to
    their own, newest first, only in `collections` unless it is empty."""
    query = select(Memory) if query is None else query
    query = query.order_by(col(Memory.created_at).desc(), col(Memory.id).desc())
    names = [_stored_name(name) for name in collections if name.strip()]
    query = query.where(col(Memory.collection).in_(names)) if names else query
    return _visible(query, viewer, mine)


async def _usernames(session: AsyncSession) -> dict[int, str]:
    return {id: name for id, name in await session.exec(select(User.id, User.username)) if id}


def _owner_for(viewer: Viewer, demo: bool) -> int:
    """Who a new memory belongs to: the uploader, or everyone (demo) when the admin says so."""
    if demo and not viewer.is_admin:
        raise HTTPException(403, "Only the admin adds demo memories")
    return DEMO_OWNER if demo else viewer.id


async def _embed_one(embed: Embedder, input: str, input_type: InputType = "query") -> list[float]:
    try:
        (vector,) = await embed([input], input_type)
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error
    if problem := width_error(len(vector)):
        raise HTTPException(502, problem)
    return vector


@router.get("/memories", response_model=list[MemoryPublic])
async def list_memories(
    session: SessionDep,
    viewer: ViewerDep,
    collection: CollectionsQuery = [],  # noqa: B006
):
    """The newest 500 memories you may see, newest first: a guest sees the demo memories and
    their own, the admin everything."""
    names = await _usernames(session)
    query = _newest(collection, viewer).limit(500)
    return [public(memory, names) for memory in await session.exec(query)]


@router.get("/memories/collections", response_model=list[CollectionCount])
async def list_collections(session: SessionDep, viewer: ViewerDep):
    """Every collection that holds a memory you may see, with how many, largest first."""
    count = func.count(col(Memory.id))
    query = _visible(select(Memory.collection, count), viewer).group_by(col(Memory.collection))
    rows = (await session.exec(query.order_by(count.desc(), col(Memory.collection)))).all()
    return [CollectionCount(name=name, count=n) for name, n in rows]


@router.get("/memories/vectors", response_model=list[MemoryVector])
async def memory_vectors(
    session: SessionDep,
    viewer: ViewerDep,
    collection: CollectionsQuery = [],  # noqa: B006
    clip: Annotated[int | None, Query(description="Only the frames of this demo clip")] = None,
    after_id: Annotated[
        int | None,
        Query(description="Only memories with a larger id: stored after the ones you have"),
    ] = None,
    mine: Annotated[
        bool,
        Query(description="Only your own: not the demo memories, nor (admin) anyone else's"),
    ] = False,
):
    """The stored embeddings with their memories, for drawing a map: the newest 2000.

    Each embedding is base64 of its float32 numbers, little-endian: a third of the size of a
    JSON list. A frame of a published demo clip says which clip it is from (`clip_id`) and how
    many seconds into the clip it was taken (`clip_at_s`). A clip is demo memory, so `clip`
    with `mine` is a 422.
    """
    if clip is not None and mine:
        raise HTTPException(422, "A demo clip is demo memory, never your own: leave out mine")
    # The frames with their clips' starts, in one statement: read apart, a clip that demo.sync
    # replaced in between would leave a frame without its start.
    starts = demo.clip_starts()
    joined = select(Memory, starts.c.start).outerjoin(starts, starts.c.source == Memory.source)
    query = _newest(collection, viewer, joined, mine)
    if clip is not None:
        query = query.where(col(Memory.source).startswith(demo.clip_prefix(clip)))
    if after_id is not None:
        query = query.where(col(Memory.id) > after_id)
    rows = (await session.exec(query.limit(MAX_VECTORS))).all()
    ids = {memory.id for memory, _start in rows if memory.id}
    stored = await vectors.stored(await session.connection(), TABLE, ids)
    names = await _usernames(session)
    return [
        MemoryVector(
            **public(memory, names).model_dump(),
            embedding=base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode(),
            clip_id=demo.clip_of(memory.source),
            clip_at_s=None if start is None else demo.frame_at_s(memory, start),
        )
        for memory, start in rows
        if (vector := stored.get(memory.id or 0)) is not None
    ]


@router.get(
    "/memories/export",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def export_collection(
    session: SessionDep,
    viewer: ViewerDep,
    collection: Annotated[str, Query(description="The collection (a recorded demo's tag)")],
):
    """A zip of one collection's stills you may see, oldest first, with their embeddings: unpack it
    under `backend/demo-data/<collection>/` and commit it, and every start loads it again as demo
    memories (app/demo_bundles.py), however often the database is rebuilt. At most 2000 stills."""
    tag = _collection(collection)
    query = (
        _visible(
            select(Memory).where(col(Memory.collection) == tag, col(Memory.kind) == "image"),
            viewer,
        )
        .order_by(col(Memory.created_at), col(Memory.id))
        .limit(MAX_VECTORS)
    )
    memories = list((await session.exec(query)).all())
    if not memories:
        raise HTTPException(404, f"No images in the collection {tag!r}")
    stored = await vectors.stored(
        await session.connection(), TABLE, {m.id for m in memories if m.id}
    )
    data = await anyio.to_thread.run_sync(demo_bundles.pack, memories, stored, tag)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{tag}.zip"'},
    )


@router.post("/memories/image", response_model=MemoryPublic, status_code=201)
async def add_image(
    session: SessionDep,
    viewer: ViewerDep,
    embed: EmbedderDep,
    file: UploadFile,
    text: Annotated[str, Form(max_length=2000)] = "",
    created_at: Annotated[datetime | None, Form()] = None,
    collection: Annotated[str, Form(max_length=40)] = "manual",
    demo: Annotated[bool, Form()] = False,
):
    """Store one image: blur every face in it, shrink it, embed it with the image model, keep an
    optional caption.

    collection groups it, e.g. "glasses" for stills from the glasses (default "manual"). It is
    yours; with demo=true (admin only) every user sees it.
    """
    collection = _collection(collection)
    owner = _owner_for(viewer, demo)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Images can be at most 25 MB")
    try:
        # Blurring faces, decoding and shrinking are CPU work: off the event loop. The blur comes
        # first, so nothing below sees a face.
        blurred = await anyio.to_thread.run_sync(faces.anonymise, data)
        name = await anyio.to_thread.run_sync(media.save_image, blurred)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        image = await anyio.to_thread.run_sync(media.data_url, name)
        vector = await _embed_one(embed, image)
        memory = await add_memory(
            session, "image", vector, _aware(created_at), text.strip(), name, collection, owner
        )
        await session.commit()
    except BaseException:
        media.delete(name)
        raise
    return public(memory, {viewer.id: viewer.user.username})


@router.post("/memories/text", response_model=MemoryPublic, status_code=201)
async def add_text(
    body: TextMemoryCreate, session: SessionDep, viewer: ViewerDep, embed: EmbedderDep
):
    """Store a text memory, embedded as a document for searches to find. demo=true (admin only)
    shares it."""
    collection = _collection(body.collection)
    owner = _owner_for(viewer, body.demo)
    vector = await _embed_one(embed, body.text, "document")
    memory = await add_memory(
        session,
        "text",
        vector,
        _aware(body.created_at),
        body.text,
        collection=collection,
        owner_id=owner,
    )
    await session.commit()
    return public(memory, {viewer.id: viewer.user.username})


@router.get("/memories/{id}/image", response_class=FileResponse)
async def memory_image(
    id: int, session: SessionDep, viewer: ViewerDep, v: str | None = None
) -> FileResponse:
    """404, not 403, for someone else's image: nobody learns which ids exist.

    With `v` (as `image_url` gives it) matching the image, the browser may keep it for good, since
    a different image gets a different `v`; without it, it has to ask again every time.
    """
    memory = await session.get(Memory, id)
    if (
        memory is None
        or not viewer.can_see(memory.owner_id)
        or memory.image_file is None
        or not media.path(memory.image_file).exists()
    ):
        raise HTTPException(404, "No image for this memory")
    cache = (
        "private, max-age=31536000, immutable"
        if v == media.version(memory.image_file)
        else "private, no-cache"
    )
    return FileResponse(
        media.path(memory.image_file),
        media_type="image/jpeg",
        headers={"Cache-Control": cache},
    )


@router.delete("/memories/{id}", status_code=204)
async def delete_memory(id: int, session: SessionDep, viewer: ViewerDep):
    """Your own memories; the admin's any. A guest gets 403 for a demo memory."""
    memory = await session.get(Memory, id)
    if memory is None or not viewer.can_see(memory.owner_id):
        raise HTTPException(404, "No such memory")
    if not viewer.can_change(memory.owner_id):
        raise HTTPException(403, "Only the admin deletes demo memories")
    await vectors.delete(await session.connection(), TABLE, id)
    await session.delete(memory)
    await session.commit()
    if memory.image_file:
        media.delete(memory.image_file)


class ModelStatus(BaseModel):
    """Which models this backend calls. A model whose URL is not set (app/settings.py) is off:
    every route that needs it answers 503 without trying to reach one."""

    chat: bool
    embeddings: bool


@router.get("/models", response_model=ModelStatus)
async def model_status():
    """Public, so /try knows before anyone logs in whether it can offer asking and reminders, or
    only the clips and their memory map."""
    settings = get_settings()
    return ModelStatus(chat=bool(settings.llm_url), embeddings=bool(settings.embed_url))


@router.post("/search", response_model=list[SearchResult])
async def search(
    args: VectorSearchArgs, session: SessionDep, viewer: ViewerDep, embed: EmbedderDep
):
    """Run the vector_search tool directly, with the arguments the chat model would send, over
    the memories you may see."""
    try:
        return await vector_search(session, args, embed, viewer.owners)
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


async def _demo_scope(
    session: AsyncSession, body: agent.AskRequest
) -> tuple[set[int] | None, datetime | None]:
    """What a question asked while a demo clip plays may search, and the moment it is then
    (agent.run's `memory_ids` and `now`); both None without a demo clip. 404 for a clip that
    is not published, 409 while its frames are not stored yet (app/demo.py stores them)."""
    view = body.demo_view()
    if view is None:
        return None, None
    clip = await session.get(Showcase, view.clip_id)
    if clip is None or not clip.published:
        raise HTTPException(404, f"No published demo clip {view.clip_id}")
    shown = await demo.shown_so_far(session, view)
    if shown is None:
        raise HTTPException(409, f"Demo clip {view.clip_id} is not stored as memories yet")
    return shown


@router.post("/ask", response_model=agent.Answer)
async def ask(
    body: agent.AskRequest,
    session: SessionDep,
    viewer: ViewerDep,
    chat: ChatDep,
    embed: EmbedderDep,
):
    """Answer a question from the memories you may see: the chat model searches them, then
    answers. With mine, only from your own; with demo_clip_id and demo_at_s, only from that clip
    up to there, at its time."""
    memory_ids, now = await _demo_scope(session, body)
    feeds.note(viewer.id, "question", body.question, "chat")
    try:
        return await agent.answer(
            session,
            body.question,
            chat,
            embed,
            viewer.scope(body.mine),
            now=now,
            collections=body.collections,
            owner_id=viewer.id,
            memory_ids=memory_ids,
        )
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


@router.post(
    "/ask/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
async def ask_stream(
    body: agent.AskRequest,
    session: SessionDep,
    viewer: ViewerDep,
    chat: ChatDep,
    embed: EmbedderDep,
):
    """The same as /ask, as newline-delimited JSON: one Step per line as it happens.

    A failure after the first line arrives as {"type": "error", "content": "..."}.
    """

    # Read now: the request's session closes before the stream ends, and a status code can only
    # be sent before the first line.
    owners, owner_id = viewer.scope(body.mine), viewer.id
    memory_ids, now = await _demo_scope(session, body)
    feeds.note(owner_id, "question", body.question, "chat")

    async def lines() -> AsyncIterator[str]:
        # Its own session: the request's would close before the stream ends.
        async with new_session() as session:
            try:
                async for step in agent.run(
                    session,
                    body.question,
                    chat,
                    embed,
                    owners,
                    now=now,
                    collections=body.collections,
                    owner_id=owner_id,
                    memory_ids=memory_ids,
                ):
                    yield step.model_dump_json() + "\n"
            except UpstreamError as error:
                yield agent.error_line(str(error))
            except Exception as error:  # the stream has started, so no status code is left
                yield agent.error_line(f"{type(error).__name__}: {error}")

    return StreamingResponse(lines(), media_type="application/x-ndjson")
