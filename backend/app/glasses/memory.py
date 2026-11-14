"""The glasses' link to the backend's memory (app.search, app.agent) and its chat model.

A photo from the glasses becomes an image memory, captioned with the chat model's description,
so it is found by vector search and by keywords. Questions go through the same question loop as
/api/ask.
"""

import base64
from datetime import UTC, datetime

from sqlmodel import Session

from app import agent, media, vectors
from app.llm import Chat, Embedder, UpstreamError
from app.search import TABLE, add_memory

COLLECTION = "glasses"  # the collection photos from the glasses are stored in

DESCRIBE = (
    "This photo was taken just now through the wearer's smart glasses. In one or two short "
    "sentences, say what is in front of them, naming the objects they might later look for."
)


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def describe(chat: Chat, jpeg: bytes) -> str:
    """The chat model's description of a photo. Raises UpstreamError."""
    message = chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": DESCRIBE},
                    {"type": "image_url", "image_url": {"url": data_url(jpeg)}},
                ],
            }
        ],
        None,
    )
    return (message.get("content") or "").strip()


def remember(session: Session, embed: Embedder, jpeg: bytes, caption: str) -> int:
    """Stores a photo as an image memory in the glasses collection and returns its id.

    Raises UpstreamError.
    """
    name = media.save_image(jpeg)
    try:
        (vector,) = embed([media.data_url(name)])
        width = vectors.VECTOR_TABLES[TABLE].dimensions
        if len(vector) != width:
            raise UpstreamError(f"The embedding model gave {len(vector)} numbers, not {width}")
        memory = add_memory(
            session, "image", vector, datetime.now(UTC), caption, name, collection=COLLECTION
        )
        session.commit()
    except BaseException:
        media.delete(name)
        raise
    assert memory.id is not None
    return memory.id


def ask(session: Session, question: str, chat: Chat, embed: Embedder) -> agent.Answer:
    """The question loop of /api/ask. Raises UpstreamError."""
    return agent.answer(session, question, chat, embed)
