"""The vector_search tool: how the chat model finds the memories that answer a question.

The model calls vector_search with the question as the user asked it, a few rewordings, keywords
and an optional timespan. Every query is embedded and searched in memory_embedding; the keywords
are matched against the memories' text. The ranked lists are merged with reciprocal rank fusion,
so a memory that several queries (or a query and a keyword) agree on comes first.

VECTOR_SEARCH_TOOL is the OpenAI-format tool definition to hand to the model, and
VectorSearchArgs validates what it sends back. Add memories with add_memory.
"""

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import vectors
from app.llm import Embedder, UpstreamError
from app.models import Memory, MemoryPublic

DEMO_OWNER = 0  # the same as app.auth.DEMO_OWNER; here to keep search free of the web layer

TABLE = "memory_embedding"
MemoryKind = Literal["image", "text"]
DEFAULT_COLLECTION = "manual"
COLLECTION = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
RRF_K = 60  # the usual reciprocal rank fusion constant: damps the gap between rank 1 and rank 2
CANDIDATES_PER_LIST = 4  # each list contributes up to limit * this candidates before fusion
# The wearer's time zone (the event is in Espoo). Times the chat model reads and writes are local,
# because "this morning" and "at ten" mean the wearer's clock; stored times stay UTC.
LOCAL_TIMEZONE = ZoneInfo("Europe/Helsinki")


def local(moment: datetime) -> str:
    """A time as the chat model sees it: ISO 8601 in the wearer's time zone, with its offset."""
    return moment.astimezone(LOCAL_TIMEZONE).isoformat(timespec="seconds")


class Timespan(BaseModel):
    start: datetime | None = Field(
        None, description="Inclusive. ISO 8601, e.g. 2026-11-14T09:00+02:00"
    )
    end: datetime | None = Field(
        None, description="Exclusive. ISO 8601, e.g. 2026-11-14T12:00+02:00"
    )

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, moment: datetime | None) -> datetime | None:
        """A time without an offset is the wearer's local time, as the system prompt's are."""
        if moment is not None and moment.tzinfo is None:
            return moment.replace(tzinfo=LOCAL_TIMEZONE)
        return moment


class VectorSearchArgs(BaseModel):
    user_query: str = Field(min_length=1, max_length=500)
    alternative_queries: list[str] = Field(default_factory=list, max_length=5)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    timespan: Timespan | None = None
    kinds: list[MemoryKind] = Field(default_factory=list)  # empty: every kind
    collections: list[str] = Field(default_factory=list, max_length=10)  # empty: every collection
    limit: int = Field(default=8, ge=1, le=20)


class SearchResult(BaseModel):
    id: int
    kind: str
    collection: str
    created_at: datetime
    text: str
    image_url: str | None  # for the browser; the chat model gets the file itself
    distance: float | None  # the best cosine distance over the queries; None if only keywords hit
    matched_keywords: list[str]
    score: float  # reciprocal rank fusion score, higher is better


# Hand-written rather than generated from VectorSearchArgs: short, flat descriptions are what a
# mid-sized model follows best. tests/test_search.py checks the two agree.
VECTOR_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "vector_search",
        "description": (
            "Search the user's memories: stills seen through their smart glasses and text notes, "
            "each with the time it was recorded. Returns the best matches, best first. Call it "
            "before answering anything about what the user saw, did or was told."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_query": {
                    "type": "string",
                    "description": "What to look for, as a short description of the scene or "
                    "thing, e.g. 'car keys on a kitchen table'.",
                },
                "alternative_queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 5 other wordings of the same search: synonyms, what "
                    "the place or object would look like, related scenes.",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 10 exact words or names to match in captions and "
                    "notes, e.g. a person's name or a brand.",
                },
                "timespan": {
                    "type": "object",
                    "description": "Only memories recorded in this window. Leave out to search "
                    "everything. Times are local, ISO 8601 with the offset, e.g. "
                    "2026-11-14T09:00:00+02:00.",
                    "properties": {
                        "start": {"type": "string", "description": "Inclusive start."},
                        "end": {"type": "string", "description": "Exclusive end."},
                    },
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["image", "text"]},
                    "description": "Only these kinds of memory. Leave out for all.",
                },
                "collections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only memories in these collections, e.g. 'glasses' for what "
                    "the glasses recorded or 'manual' for what the user added by hand. Leave out "
                    "for all.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many results to return, 1 to 20. Default 8.",
                },
            },
            "required": ["user_query"],
        },
    },
}


def clean_collection(name: str | None) -> str:
    """A collection name as stored: trimmed and lower case, `manual` when empty.

    Raises ValueError unless it is 1 to 40 of a-z, 0-9, _ and -, starting with a letter or digit.
    """
    cleaned = (name or "").strip().lower() or DEFAULT_COLLECTION
    if not COLLECTION.fullmatch(cleaned):
        raise ValueError(
            f"Collection {name!r} must be 1 to 40 of a-z, 0-9, _ and -, starting with a letter "
            "or digit"
        )
    return cleaned


def image_url(memory: Memory) -> str | None:
    return f"/api/memories/{memory.id}/image" if memory.image_file else None


def public(memory: Memory, usernames: dict[int, str] | None = None) -> MemoryPublic:
    """`usernames` (id -> name) fills in `owner`; without it only demo memories are marked."""
    assert memory.id is not None
    demo = memory.owner_id == DEMO_OWNER
    return MemoryPublic(
        id=memory.id,
        kind=memory.kind,
        created_at=memory.created_at,
        text=memory.text,
        image_url=image_url(memory),
        collection=memory.collection,
        demo=demo,
        owner=None if demo else (usernames or {}).get(memory.owner_id),
    )


async def add_memory(
    session: AsyncSession,
    kind: MemoryKind,
    embedding: Sequence[float],
    created_at: datetime | None = None,
    text: str = "",
    image_file: str | None = None,
    collection: str = DEFAULT_COLLECTION,
    owner_id: int = DEMO_OWNER,
) -> Memory:
    """Store a memory and its embedding in the session's transaction; the caller commits.

    created_at defaults to now and must be timezone-aware. collection is cleaned with
    clean_collection, so it raises ValueError for a name that is not allowed. owner_id is the
    User who added it, or DEMO_OWNER for a memory every user sees.
    """
    collection = clean_collection(collection)
    if kind not in ("image", "text"):
        raise ValueError(f"Unknown memory kind {kind!r}")
    moment = created_at or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware, e.g. datetime.now(UTC)")
    memory = Memory(
        kind=kind,
        created_at=moment,
        text=text,
        image_file=image_file,
        collection=collection,
        owner_id=owner_id,
    )
    session.add(memory)
    await session.flush()  # assigns the id
    assert memory.id is not None
    await vectors.upsert(
        await session.connection(),
        TABLE,
        memory.id,
        embedding,
        moment,
        {"kind": kind, "collection": collection, "owner": owner_id},
    )
    return memory


def _collections(args: VectorSearchArgs) -> list[str]:
    """The collections asked for, as stored. A name that is not allowed cannot match any."""
    names = []
    for name in args.collections:
        try:
            names.append(clean_collection(name))
        except ValueError:
            names.append(name)  # kept so the filter is not dropped: it matches nothing
    return list(dict.fromkeys(names))


async def _keyword_ranking(
    session: AsyncSession, args: VectorSearchArgs, candidates: int, owners: list[int] | None
) -> tuple[list[int], dict[int, list[str]]]:
    """Ids of memories whose text holds a keyword, most keywords first, then newest first."""
    keywords = list(dict.fromkeys(k.strip() for k in args.keywords if k.strip()))
    if not keywords:
        return [], {}

    def like(keyword: str):
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return col(Memory.text).ilike(f"%{escaped}%", escape="\\")

    query = select(Memory.id, Memory.text, Memory.created_at).where(
        or_(*(like(keyword) for keyword in keywords))
    )
    if owners is not None:
        query = query.where(col(Memory.owner_id).in_(owners))
    if args.kinds:
        query = query.where(col(Memory.kind).in_(args.kinds))
    if collections := _collections(args):
        query = query.where(col(Memory.collection).in_(collections))
    if args.timespan and args.timespan.start:
        query = query.where(Memory.created_at >= args.timespan.start)
    if args.timespan and args.timespan.end:
        query = query.where(Memory.created_at < args.timespan.end)

    matched: dict[int, list[str]] = {}
    newest: dict[int, datetime] = {}
    for id, text, created_at in await session.exec(query):
        lowered = text.casefold()
        matched[id] = [k for k in keywords if k.casefold() in lowered]
        newest[id] = created_at
    ranking = sorted(matched, key=lambda id: (-len(matched[id]), -newest[id].timestamp()))
    return ranking[:candidates], matched


def width_error(got: int) -> str | None:
    """Why a vector of `got` numbers cannot go in the table, or None when it fits."""
    width = vectors.VECTOR_TABLES[TABLE].dimensions
    if got == width:
        return None
    return (
        f"The embedding model gives {got} numbers per input, but {TABLE} holds {width}: "
        f"set its width in app/vectors.py to match (the database rebuilds)"
    )


async def vector_search(
    session: AsyncSession, args: VectorSearchArgs, embed: Embedder, owners: list[int] | None
) -> list[SearchResult]:
    """Run one vector_search call: the best `args.limit` memories, best first.

    `owners` limits it to memories of those owner ids (a guest: the demo ones and their own);
    None searches every memory, for the admin. It is not optional on purpose: every caller says
    whose memories it may reach.
    """
    candidates = args.limit * CANDIDATES_PER_LIST
    queries = list(dict.fromkeys(q.strip() for q in [args.user_query, *args.alternative_queries]))
    queries = [q for q in queries if q]
    since = args.timespan.start if args.timespan else None
    until = args.timespan.end if args.timespan else None
    where: dict[str, Any] = {}
    if owners is not None:
        where["owner"] = list(dict.fromkeys(owners))
    if args.kinds:
        where["kind"] = list(dict.fromkeys(args.kinds))
    if collections := _collections(args):
        where["collection"] = collections

    rankings: list[list[int]] = []
    best_distance: dict[int, float] = {}
    connection = await session.connection()
    for vector in await embed(queries) if queries else []:
        if problem := width_error(len(vector)):
            raise UpstreamError(problem)
        hits = await vectors.nearest(connection, TABLE, vector, candidates, since, until, where)
        rankings.append([hit.id for hit in hits])
        for hit in hits:
            best_distance[hit.id] = min(hit.distance, best_distance.get(hit.id, hit.distance))

    keyword_ids, matched = await _keyword_ranking(session, args, candidates, owners)
    rankings.append(keyword_ids)

    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, id in enumerate(ranking):
            scores[id] += 1 / (RRF_K + rank + 1)
    top = sorted(scores, key=lambda id: -scores[id])[: args.limit]

    found = await session.exec(select(Memory).where(col(Memory.id).in_(top)))
    memories = {m.id: m for m in found}
    return [
        SearchResult(
            id=id,
            kind=memories[id].kind,
            collection=memories[id].collection,
            created_at=memories[id].created_at,
            text=memories[id].text,
            image_url=image_url(memories[id]),
            distance=best_distance.get(id),
            matched_keywords=matched.get(id, []),
            score=scores[id],
        )
        for id in top
        if id in memories  # an embedding without its Memory row is skipped
    ]


def for_model(results: list[SearchResult]) -> str:
    """The tool message the chat model reads: what each memory is and when, without the URLs."""
    return json.dumps(
        [
            {
                "id": result.id,
                "kind": result.kind,
                "collection": result.collection,
                "seen_at": local(result.created_at),
                "text": result.text,
                "image_attached": result.image_url is not None,
            }
            for result in results
        ]
    )
