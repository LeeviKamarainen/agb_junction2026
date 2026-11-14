"""The vector_search tool: how the chat model finds the memories that answer a question.

The model calls vector_search with the question as the user asked it, a few rewordings, keywords
and an optional timespan. Every query is embedded and searched in memory_embedding; the keywords
are matched against the memories' text. The ranked lists are merged with reciprocal rank fusion,
so a memory that several queries (or a query and a keyword) agree on comes first.

VECTOR_SEARCH_TOOL is the OpenAI-format tool definition to hand to the model, and
VectorSearchArgs validates what it sends back. Add memories with add_memory.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlmodel import Session, col, select

from app import vectors
from app.llm import Embedder, UpstreamError
from app.models import Memory, MemoryPublic

TABLE = "memory_embedding"
MemoryKind = Literal["image", "text"]
RRF_K = 60  # the usual reciprocal rank fusion constant: damps the gap between rank 1 and rank 2
CANDIDATES_PER_LIST = 4  # each list contributes up to limit * this candidates before fusion


class Timespan(BaseModel):
    start: datetime | None = Field(None, description="Inclusive. ISO 8601, e.g. 2026-11-14T09:00Z")
    end: datetime | None = Field(None, description="Exclusive. ISO 8601, e.g. 2026-11-14T12:00Z")

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, moment: datetime | None) -> datetime | None:
        """A time without an offset is taken as UTC, which the system prompt asks for."""
        if moment is not None and moment.tzinfo is None:
            return moment.replace(tzinfo=UTC)
        return moment


class VectorSearchArgs(BaseModel):
    user_query: str = Field(min_length=1, max_length=500)
    alternative_queries: list[str] = Field(default_factory=list, max_length=5)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    timespan: Timespan | None = None
    kinds: list[MemoryKind] = Field(default_factory=list)  # empty: every kind
    limit: int = Field(default=8, ge=1, le=20)


class SearchResult(BaseModel):
    id: int
    kind: str
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
                    "everything. Times are ISO 8601 with an offset, e.g. 2026-11-14T09:00:00Z.",
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
                "limit": {
                    "type": "integer",
                    "description": "How many results to return, 1 to 20. Default 8.",
                },
            },
            "required": ["user_query"],
        },
    },
}


def image_url(memory: Memory) -> str | None:
    return f"/api/memories/{memory.id}/image" if memory.image_file else None


def public(memory: Memory) -> MemoryPublic:
    assert memory.id is not None
    return MemoryPublic(
        id=memory.id,
        kind=memory.kind,
        created_at=memory.created_at,
        text=memory.text,
        image_url=image_url(memory),
    )


def add_memory(
    session: Session,
    kind: MemoryKind,
    embedding: Sequence[float],
    created_at: datetime | None = None,
    text: str = "",
    image_file: str | None = None,
) -> Memory:
    """Store a memory and its embedding in the session's transaction; the caller commits.

    created_at defaults to now and must be timezone-aware.
    """
    if kind not in ("image", "text"):
        raise ValueError(f"Unknown memory kind {kind!r}")
    moment = created_at or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware, e.g. datetime.now(UTC)")
    memory = Memory(kind=kind, created_at=moment, text=text, image_file=image_file)
    session.add(memory)
    session.flush()  # assigns the id
    assert memory.id is not None
    vectors.upsert(session.connection(), TABLE, memory.id, embedding, moment, {"kind": kind})
    return memory


def _keyword_ranking(
    session: Session, args: VectorSearchArgs, candidates: int
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
    if args.kinds:
        query = query.where(col(Memory.kind).in_(args.kinds))
    if args.timespan and args.timespan.start:
        query = query.where(Memory.created_at >= args.timespan.start)
    if args.timespan and args.timespan.end:
        query = query.where(Memory.created_at < args.timespan.end)

    matched: dict[int, list[str]] = {}
    newest: dict[int, datetime] = {}
    for id, text, created_at in session.exec(query):
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


def vector_search(session: Session, args: VectorSearchArgs, embed: Embedder) -> list[SearchResult]:
    """Run one vector_search call: the best `args.limit` memories, best first."""
    candidates = args.limit * CANDIDATES_PER_LIST
    queries = list(dict.fromkeys(q.strip() for q in [args.user_query, *args.alternative_queries]))
    queries = [q for q in queries if q]
    since = args.timespan.start if args.timespan else None
    until = args.timespan.end if args.timespan else None
    # vec0 filters metadata by equality only, so search once per kind asked for.
    filters: list[dict[str, Any] | None] = (
        [{"kind": kind} for kind in dict.fromkeys(args.kinds)] if args.kinds else [None]
    )

    rankings: list[list[int]] = []
    best_distance: dict[int, float] = {}
    connection = session.connection()
    for vector in embed(queries) if queries else []:
        if problem := width_error(len(vector)):
            raise UpstreamError(problem)
        hits = [
            hit
            for where in filters
            for hit in vectors.nearest(connection, TABLE, vector, candidates, since, until, where)
        ]
        hits.sort(key=lambda hit: hit.distance)
        rankings.append([hit.id for hit in hits[:candidates]])
        for hit in hits:
            best_distance[hit.id] = min(hit.distance, best_distance.get(hit.id, hit.distance))

    keyword_ids, matched = _keyword_ranking(session, args, candidates)
    rankings.append(keyword_ids)

    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, id in enumerate(ranking):
            scores[id] += 1 / (RRF_K + rank + 1)
    top = sorted(scores, key=lambda id: -scores[id])[: args.limit]

    memories = {m.id: m for m in session.exec(select(Memory).where(col(Memory.id).in_(top)))}
    return [
        SearchResult(
            id=id,
            kind=memories[id].kind,
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
                "seen_at": result.created_at.isoformat(timespec="seconds"),
                "text": result.text,
                "image_attached": result.image_url is not None,
            }
            for result in results
        ]
    )
