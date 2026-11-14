import base64
import struct
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, col, func, select

from app import agent, media, vectors
from app.auth import DEMO_OWNER, Viewer, ViewerDep
from app.db import SessionDep, get_engine
from app.llm import Chat, Embedder, UpstreamError, get_chat, get_embedder
from app.models import (
    CollectionCount,
    Memory,
    MemoryPublic,
    MemoryVector,
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


def _visible(query, viewer: Viewer):
    """`query` limited to the memories `viewer` may see."""
    owners = viewer.owners
    return query if owners is None else query.where(col(Memory.owner_id).in_(owners))


def _newest(collections: list[str], viewer: Viewer):
    """Memories `viewer` may see, newest first, only in `collections` unless it is empty."""
    query = select(Memory).order_by(col(Memory.created_at).desc(), col(Memory.id).desc())
    names = [_stored_name(name) for name in collections if name.strip()]
    query = query.where(col(Memory.collection).in_(names)) if names else query
    return _visible(query, viewer)


def _usernames(session: Session) -> dict[int, str]:
    return {id: name for id, name in session.exec(select(User.id, User.username)) if id}


def _owner_for(viewer: Viewer, demo: bool) -> int:
    """Who a new memory belongs to: the uploader, or everyone (demo) when the admin says so."""
    if demo and not viewer.is_admin:
        raise HTTPException(403, "Only the admin adds demo memories")
    return DEMO_OWNER if demo else viewer.id


def _embed_one(embed: Embedder, input: str) -> list[float]:
    try:
        (vector,) = embed([input])
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error
    if problem := width_error(len(vector)):
        raise HTTPException(502, problem)
    return vector


@router.get("/memories", response_model=list[MemoryPublic])
def list_memories(
    session: SessionDep,
    viewer: ViewerDep,
    collection: CollectionsQuery = [],  # noqa: B006
):
    """The newest 500 memories you may see, newest first: a guest sees the demo memories and
    their own, the admin everything."""
    names = _usernames(session)
    query = _newest(collection, viewer).limit(500)
    return [public(memory, names) for memory in session.exec(query)]


@router.get("/memories/collections", response_model=list[CollectionCount])
def list_collections(session: SessionDep, viewer: ViewerDep):
    """Every collection that holds a memory you may see, with how many, largest first."""
    count = func.count(col(Memory.id))
    query = _visible(select(Memory.collection, count), viewer).group_by(col(Memory.collection))
    rows = session.exec(query.order_by(count.desc(), col(Memory.collection))).all()
    return [CollectionCount(name=name, count=n) for name, n in rows]


@router.get("/memories/vectors", response_model=list[MemoryVector])
def memory_vectors(
    session: SessionDep,
    viewer: ViewerDep,
    collection: CollectionsQuery = [],  # noqa: B006
):
    """The stored embeddings with their memories, for drawing a map: the newest 2000.

    Each embedding is base64 of its float32 numbers, little-endian: a third of the size of a
    JSON list.
    """
    memories = session.exec(_newest(collection, viewer).limit(MAX_VECTORS)).all()
    stored = vectors.stored(session.connection(), TABLE, {m.id for m in memories if m.id})
    names = _usernames(session)
    return [
        MemoryVector(
            **public(memory, names).model_dump(),
            embedding=base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode(),
        )
        for memory in memories
        if (vector := stored.get(memory.id or 0)) is not None
    ]


@router.post("/memories/image", response_model=MemoryPublic, status_code=201)
def add_image(
    session: SessionDep,
    viewer: ViewerDep,
    embed: EmbedderDep,
    file: UploadFile,
    text: Annotated[str, Form(max_length=2000)] = "",
    created_at: Annotated[datetime | None, Form()] = None,
    collection: Annotated[str, Form(max_length=40)] = "manual",
    demo: Annotated[bool, Form()] = False,
):
    """Store one image: shrink it, embed it with the image model, keep an optional caption.

    collection groups it, e.g. "glasses" for stills from the glasses (default "manual"). It is
    yours; with demo=true (admin only) every user sees it.
    """
    collection = _collection(collection)
    owner = _owner_for(viewer, demo)
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Images can be at most 25 MB")
    try:
        name = media.save_image(data)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        vector = _embed_one(embed, media.data_url(name))
        memory = add_memory(
            session, "image", vector, _aware(created_at), text.strip(), name, collection, owner
        )
        session.commit()
    except BaseException:
        media.delete(name)
        raise
    return public(memory, {viewer.id: viewer.user.username})


@router.post("/memories/text", response_model=MemoryPublic, status_code=201)
def add_text(body: TextMemoryCreate, session: SessionDep, viewer: ViewerDep, embed: EmbedderDep):
    """Store a text memory, embedded with the text model. demo=true (admin only) shares it."""
    collection = _collection(body.collection)
    owner = _owner_for(viewer, body.demo)
    vector = _embed_one(embed, body.text)
    memory = add_memory(
        session,
        "text",
        vector,
        _aware(body.created_at),
        body.text,
        collection=collection,
        owner_id=owner,
    )
    session.commit()
    return public(memory, {viewer.id: viewer.user.username})


@router.get("/memories/{id}/image", response_class=FileResponse)
def memory_image(id: int, session: SessionDep, viewer: ViewerDep):
    """404, not 403, for someone else's image: nobody learns which ids exist."""
    memory = session.get(Memory, id)
    if (
        memory is None
        or not viewer.can_see(memory.owner_id)
        or memory.image_file is None
        or not media.path(memory.image_file).exists()
    ):
        raise HTTPException(404, "No image for this memory")
    return FileResponse(media.path(memory.image_file), media_type="image/jpeg")


@router.delete("/memories/{id}", status_code=204)
def delete_memory(id: int, session: SessionDep, viewer: ViewerDep):
    """Your own memories; the admin's any. A guest gets 403 for a demo memory."""
    memory = session.get(Memory, id)
    if memory is None or not viewer.can_see(memory.owner_id):
        raise HTTPException(404, "No such memory")
    if not viewer.can_change(memory.owner_id):
        raise HTTPException(403, "Only the admin deletes demo memories")
    vectors.delete(session.connection(), TABLE, id)
    session.delete(memory)
    session.commit()
    if memory.image_file:
        media.delete(memory.image_file)


@router.post("/search", response_model=list[SearchResult])
def search(args: VectorSearchArgs, session: SessionDep, viewer: ViewerDep, embed: EmbedderDep):
    """Run the vector_search tool directly, with the arguments the chat model would send, over
    the memories you may see."""
    try:
        return vector_search(session, args, embed, viewer.owners)
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


@router.post("/ask", response_model=agent.Answer)
def ask(
    body: agent.AskRequest,
    session: SessionDep,
    viewer: ViewerDep,
    chat: ChatDep,
    embed: EmbedderDep,
):
    """Answer a question from the memories you may see: the chat model searches them, then
    answers."""
    try:
        return agent.answer(
            session, body.question, chat, embed, viewer.owners, collections=body.collections
        )
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


@router.post(
    "/ask/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
def ask_stream(body: agent.AskRequest, viewer: ViewerDep, chat: ChatDep, embed: EmbedderDep):
    """The same as /ask, as newline-delimited JSON: one Step per line as it happens.

    A failure after the first line arrives as {"type": "error", "content": "..."}.
    """

    owners = viewer.owners  # read now: the request's session closes before the stream ends

    def lines() -> Iterator[str]:
        # Its own session: the request's would close before the stream ends.
        with Session(get_engine()) as session:
            try:
                for step in agent.run(
                    session,
                    body.question,
                    chat,
                    embed,
                    owners,
                    collections=body.collections,
                ):
                    yield step.model_dump_json() + "\n"
            except UpstreamError as error:
                yield agent.error_line(str(error))
            except Exception as error:  # the stream has started, so no status code is left
                yield agent.error_line(f"{type(error).__name__}: {error}")

    return StreamingResponse(lines(), media_type="application/x-ndjson")
