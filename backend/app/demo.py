"""The demo dataset: one day seen through the glasses, as the home page plays it back.

The moments below are the whole dataset. GET /api/demo/timeline serves them in order to anyone,
and load_demo_memories stores each one as a demo memory (owner DEMO_OWNER, collection "demo"),
embedded with the embedding model, so every logged-in user can ask Gemma about them. Edit the
list to change the demo; the next start replaces the stored copies. The demo memories are text
only: their captions are what the embedding model and Gemma see.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import vectors
from app.llm import Embedder
from app.models import Memory
from app.search import DEMO_OWNER, TABLE, add_memory

log = logging.getLogger(__name__)

COLLECTION = "demo"
DAY = "2026-11-14"
ZONE = timezone(timedelta(hours=2))  # Espoo in November (EET)


@dataclass(frozen=True)
class Moment:
    time: str  # HH:MM on DAY, local time
    place: str  # a short label for the timeline
    text: str  # what the glasses saw: the memory's caption, which is embedded and searched

    @property
    def at(self) -> datetime:
        return datetime.fromisoformat(f"{DAY}T{self.time}:00").replace(tzinfo=ZONE)


# Keep these in time order; the timeline and the tests rely on it.
MOMENTS: list[Moment] = [
    Moment(
        "07:42",
        "Kitchen",
        "Kettle on the kitchen counter, two mugs, a half-eaten rye toast "
        "with cheese and cucumber on a blue plate.",
    ),
    Moment(
        "07:58",
        "Hallway",
        "Put the house keys with the red bottle-opener keyring into the "
        "left pocket of the grey winter coat hanging by the front door.",
    ),
    Moment(
        "08:15",
        "Bus stop",
        "Waiting at the bus stop on Otaniementie in light snow; the "
        "display says bus 102 arrives in 4 minutes.",
    ),
    Moment(
        "08:41",
        "Aalto campus",
        "Walking past the red-brick main building of Aalto "
        "University towards Dipoli, where the Junction banners hang over the entrance.",
    ),
    Moment(
        "09:05",
        "Registration",
        "Registration desk at Junction: picked up a name badge, a "
        "black hoodie and a sticker sheet from a volunteer in a yellow vest.",
    ),
    Moment(
        "09:30",
        "Team table",
        "Our team table in the main hall: four laptops, a pair of "
        "smart glasses in a black case and a whiteboard with 'visual memory' written on it.",
    ),
    Moment(
        "10:12",
        "Coffee",
        "Coffee break with Ilmari at the Paulig stand; he ordered an oat "
        "latte, I had a filter coffee and a cinnamon bun.",
    ),
    Moment(
        "11:20",
        "Workshop",
        "Workshop on on-device models: the slide shows EmbeddingGemma "
        "and a chart comparing image and text embeddings in one vector space.",
    ),
    Moment(
        "12:35",
        "Lunch",
        "Lunch queue: salmon soup with dark bread; sat next to Arno and "
        "Pauli by the window looking at the snowy courtyard.",
    ),
    Moment(
        "13:50",
        "Hacking",
        "Laptop screen with a FastAPI route and a sqlite-vec query; a "
        "sticky note on the bezel says 'timestamps are timezone-aware!'.",
    ),
    Moment(
        "14:40",
        "Charger",
        "Left the white USB-C laptop charger plugged in under the "
        "beanbags in the chill-out corner on the second floor.",
    ),
    Moment(
        "15:25",
        "Mentor",
        "Talked with a mentor wearing a green Junction lanyard about "
        "privacy: memories stay per user and guests expire after an hour.",
    ),
    Moment(
        "16:10",
        "Snack",
        "Took a banana and a bottle of sparkling water from the snack "
        "table; the fridge next to it is out of energy drinks.",
    ),
    Moment(
        "17:30",
        "Demo test",
        "Testing the glasses: the display shows a reminder 'watch out "
        "for the stool' as a wooden bar stool comes into view.",
    ),
    Moment(
        "18:45",
        "Dinner",
        "Pizza dinner in the main hall: pepperoni and a vegan slice on a "
        "paper plate, the Junction stage lit in orange behind the tables.",
    ),
    Moment(
        "20:15",
        "Wrap-up",
        "Back at the team table: packed the smart glasses into their "
        "black case and put the case in the front pocket of my backpack.",
    ),
]


def timeline() -> list[Moment]:
    """The moments, earliest first."""
    return sorted(MOMENTS, key=lambda moment: moment.at)


async def _stored(session: AsyncSession) -> list[Memory]:
    query = select(Memory).where(Memory.owner_id == DEMO_OWNER, Memory.collection == COLLECTION)
    return list(await session.exec(query.order_by(col(Memory.created_at), col(Memory.id))))


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def is_loaded(session: AsyncSession) -> bool:
    """Whether the stored demo memories match the dataset, so asking about them works."""
    stored = [(_aware(memory.created_at), memory.text) for memory in await _stored(session)]
    return stored == [(moment.at, moment.text) for moment in timeline()]


async def load_demo_memories(session: AsyncSession, embed: Embedder) -> bool:
    """Store the dataset as demo memories unless they are already there; True when it stored
    them. Replaces stale copies. Raises app.llm.UpstreamError when the embedding model fails, and
    then leaves the stored memories as they were."""
    if await is_loaded(session):
        return False
    moments = timeline()
    embeddings = await embed([moment.text for moment in moments])  # before deleting anything
    for memory in await _stored(session):
        assert memory.id is not None
        await vectors.delete(await session.connection(), TABLE, memory.id)
        await session.delete(memory)
    await session.flush()
    for moment, embedding in zip(moments, embeddings, strict=True):
        await add_memory(
            session,
            "text",
            embedding,
            created_at=moment.at,
            text=moment.text,
            collection=COLLECTION,
            owner_id=DEMO_OWNER,
        )
    await session.commit()
    log.info("Stored %d demo memories", len(moments))
    return True
