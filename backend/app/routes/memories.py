from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, col, select

from app import agent, media, vectors
from app.db import SessionDep, get_engine
from app.llm import Chat, Embedder, UpstreamError, get_chat, get_embedder
from app.models import Memory, MemoryPublic, TextMemoryCreate
from app.search import TABLE, SearchResult, VectorSearchArgs, add_memory, public, vector_search

router = APIRouter(tags=["memories"])

ChatDep = Annotated[Chat, Depends(get_chat)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]

MAX_UPLOAD = 25 * 1024 * 1024  # bytes per image, before it is shrunk


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _embed_one(embed: Embedder, input: str) -> list[float]:
    try:
        (vector,) = embed([input])
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error
    width = vectors.VECTOR_TABLES[TABLE].dimensions
    if len(vector) != width:
        raise HTTPException(502, f"The embedding model gave {len(vector)} numbers, not {width}")
    return vector


@router.get("/memories", response_model=list[MemoryPublic])
def list_memories(session: SessionDep):
    """Every memory, newest first."""
    query = select(Memory).order_by(col(Memory.created_at).desc(), col(Memory.id).desc())
    return [public(memory) for memory in session.exec(query.limit(500))]


@router.post("/memories/image", response_model=MemoryPublic, status_code=201)
def add_image(
    session: SessionDep,
    embed: EmbedderDep,
    file: UploadFile,
    text: Annotated[str, Form(max_length=2000)] = "",
    created_at: Annotated[datetime | None, Form()] = None,
):
    """Store one image: shrink it, embed it with the image model, keep an optional caption."""
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Images can be at most 25 MB")
    try:
        name = media.save_image(data)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        vector = _embed_one(embed, media.data_url(name))
        memory = add_memory(session, "image", vector, _aware(created_at), text.strip(), name)
        session.commit()
    except BaseException:
        media.delete(name)
        raise
    return public(memory)


@router.post("/memories/text", response_model=MemoryPublic, status_code=201)
def add_text(body: TextMemoryCreate, session: SessionDep, embed: EmbedderDep):
    """Store a text memory, embedded with the text model."""
    vector = _embed_one(embed, body.text)
    memory = add_memory(session, "text", vector, _aware(body.created_at), body.text)
    session.commit()
    return public(memory)


@router.get("/memories/{id}/image", response_class=FileResponse)
def memory_image(id: int, session: SessionDep):
    memory = session.get(Memory, id)
    if memory is None or memory.image_file is None or not media.path(memory.image_file).exists():
        raise HTTPException(404, "No image for this memory")
    return FileResponse(media.path(memory.image_file), media_type="image/jpeg")


@router.delete("/memories/{id}", status_code=204)
def delete_memory(id: int, session: SessionDep):
    memory = session.get(Memory, id)
    if memory is None:
        raise HTTPException(404, "No such memory")
    vectors.delete(session.connection(), TABLE, id)
    session.delete(memory)
    session.commit()
    if memory.image_file:
        media.delete(memory.image_file)


@router.post("/search", response_model=list[SearchResult])
def search(args: VectorSearchArgs, session: SessionDep, embed: EmbedderDep):
    """Run the vector_search tool directly, with the arguments the chat model would send."""
    try:
        return vector_search(session, args, embed)
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


@router.post("/ask", response_model=agent.Answer)
def ask(body: agent.AskRequest, session: SessionDep, chat: ChatDep, embed: EmbedderDep):
    """Answer a question from the memories: the chat model searches them, then answers."""
    try:
        return agent.answer(session, body.question, chat, embed)
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


@router.post(
    "/ask/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
def ask_stream(body: agent.AskRequest, chat: ChatDep, embed: EmbedderDep):
    """The same as /ask, as newline-delimited JSON: one Step per line as it happens.

    A failure after the first line arrives as {"type": "error", "content": "..."}.
    """

    def lines() -> Iterator[str]:
        # Its own session: the request's would close before the stream ends.
        with Session(get_engine()) as session:
            try:
                for step in agent.run(session, body.question, chat, embed):
                    yield step.model_dump_json() + "\n"
            except UpstreamError as error:
                yield agent.error_line(str(error))
            except Exception as error:  # the stream has started, so no status code is left
                yield agent.error_line(f"{type(error).__name__}: {error}")

    return StreamingResponse(lines(), media_type="application/x-ndjson")
