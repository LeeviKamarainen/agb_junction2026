"""The glasses' link to the backend's memory (app.search, app.agent) and its chat model.

A photo from the glasses becomes an image memory, captioned with the chat model's description,
so it is found by vector search and by keywords. Questions go through the same question loop as
/api/ask.
"""

import base64
from datetime import UTC, datetime

import anyio
from sqlmodel.ext.asyncio.session import AsyncSession

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


async def describe(chat: Chat, jpeg: bytes) -> str:
    """The chat model's description of a photo. Raises UpstreamError."""
    message = await chat(
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


async def remember(
    session: AsyncSession, embed: Embedder, jpeg: bytes, caption: str, owner: int
) -> int:
    """Stores a photo as `owner`'s image memory in the glasses collection and returns its id.

    Raises UpstreamError.
    """
    name = await anyio.to_thread.run_sync(media.save_image, jpeg)
    try:
        (vector,) = await embed([await anyio.to_thread.run_sync(media.data_url, name)])
        width = vectors.VECTOR_TABLES[TABLE].dimensions
        if len(vector) != width:
            raise UpstreamError(f"The embedding model gave {len(vector)} numbers, not {width}")
        memory = await add_memory(
            session,
            "image",
            vector,
            datetime.now(UTC),
            caption,
            name,
            collection=COLLECTION,
            owner_id=owner,
        )
        await session.commit()
    except BaseException:
        media.delete(name)
        raise
    assert memory.id is not None
    return memory.id


async def ask(
    session: AsyncSession, question: str, chat: Chat, embed: Embedder, owners: list[int] | None
) -> agent.Answer:
    """The question loop of /api/ask, over `owners`' memories. Raises UpstreamError."""
    return await agent.answer(session, question, chat, embed, owners)
