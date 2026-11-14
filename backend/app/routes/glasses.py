"""The glasses' API: every response is a `GlassesReply` the client draws and speaks.

Clients: the browser harness (frontend /glasses) and, later, glasses-android. Memory is the
backend's own (app/routes/memories.py): photos become image memories, questions go through the
same question loop as /api/ask. Without LLM_URL and EMBED_URL the glasses still take and show
photos; they say memory search isn't set up.
"""

from typing import Annotated, Self

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from app.db import SessionDep
from app.glasses import memory, photos, screens
from app.glasses.intents import BUTTON_ACTIONS, interpret
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, UpstreamError, get_chat, get_embedder

router = APIRouter(prefix="/glasses", tags=["glasses"])


def optional_chat() -> Chat | None:
    """The chat model, or None when LLM_URL is not set."""
    try:
        return get_chat()
    except HTTPException:
        return None


def optional_embedder() -> Embedder | None:
    """The embedding model, or None when EMBED_URL is not set."""
    try:
        return get_embedder()
    except HTTPException:
        return None


ChatDep = Annotated[Chat | None, Depends(optional_chat)]
EmbedderDep = Annotated[Embedder | None, Depends(optional_embedder)]


class Command(BaseModel):
    """Exactly one of: what the wearer said (`text`), or the action of the button they pressed."""

    text: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def one_of(self) -> Self:
        if (self.text is None) == (self.action is None):
            raise ValueError("send exactly one of text or action")
        return self


@router.get("/home", response_model=GlassesReply)
def home():
    return screens.home()


@router.post("/command", response_model=GlassesReply)
def command(body: Command, session: SessionDep, chat: ChatDep, embed: EmbedderDep):
    if body.action is not None:
        if body.action not in BUTTON_ACTIONS:
            raise HTTPException(422, f"unknown action {body.action!r}")
        name, text = body.action, ""
    else:
        intent = interpret(body.text or "")
        name, text = intent.name, intent.text

    if name == "home":
        return screens.home()
    if name == "help":
        return screens.help_()
    if name == "take_photo":
        return screens.capture()
    if name == "gallery":
        return screens.gallery()
    if name == "ask_memory":
        if chat is None or embed is None:
            return screens.memory_problem(text, "Memory search isn’t set up yet.")
        try:
            answer = memory.ask(session, text, chat, embed)
        except UpstreamError:
            return screens.memory_problem(text, "I can’t reach the memory right now.")
        images = [source.image_url for source in answer.sources if source.image_url]
        said = answer.answer or "I couldn’t find that in your memories."
        return screens.memory_answer(text, said, images)
    return screens.unknown(text)


@router.post(
    "/photos",
    response_model=GlassesReply,
    status_code=201,
    description=(
        "Saves the still. With a chat model the glasses describe it; with an embedding model it "
        "also becomes an image memory, captioned with that description."
    ),
)
def upload_photo(
    photo: Annotated[UploadFile, File(description="One JPEG still")],
    session: SessionDep,
    chat: ChatDep,
    embed: EmbedderDep,
):
    data = photo.file.read(photos.MAX_BYTES + 1)
    if len(data) > photos.MAX_BYTES:
        raise HTTPException(413, "photo is larger than 10 MB")
    if not photos.is_jpeg(data):
        raise HTTPException(415, "photo must be a JPEG")
    name = photos.save(data)

    description, problems, stored = "", [], False
    if chat is not None:
        try:
            description = memory.describe(chat, data)
        except UpstreamError:
            problems.append("couldn’t describe it")
    if embed is not None:
        try:
            memory.remember(session, embed, data, description)
            stored = True
        except UpstreamError:
            problems.append("couldn’t save it to memory")

    note = "; ".join(problems) or ("saved to memory" if stored else None)
    if description:
        return screens.photo_described(name, description, note)
    return screens.photo_saved(name, note)


@router.get(
    "/photos/{name}",
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}}}, 404: {}},
)
def get_photo(name: str):
    file = photos.path(name)
    if file is None:
        raise HTTPException(404, "no such photo")
    return FileResponse(file, media_type="image/jpeg")
