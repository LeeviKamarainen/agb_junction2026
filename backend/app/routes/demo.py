"""The home page's demo: the dataset's moments as a timeline, open to everyone."""

from datetime import datetime

from fastapi import APIRouter
from sqlmodel import SQLModel

from app import demo
from app.db import SessionDep

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoMoment(SQLModel):
    index: int  # position in the timeline, from 0
    time: datetime  # when the glasses saw it; timezone-aware
    place: str
    text: str


class DemoTimeline(SQLModel):
    collection: str  # the memories' collection, for asking about only these
    # Whether the moments are stored and embedded as demo memories, so asking about them works.
    # False until the backend reaches the embedding model (it retries on every start).
    searchable: bool
    moments: list[DemoMoment]


@router.get("/timeline", response_model=DemoTimeline)
async def timeline(session: SessionDep):
    """The demo dataset's moments, earliest first. No login needed."""
    return DemoTimeline(
        collection=demo.COLLECTION,
        searchable=await demo.is_loaded(session),
        moments=[
            DemoMoment(index=index, time=moment.at, place=moment.place, text=moment.text)
            for index, moment in enumerate(demo.timeline())
        ],
    )
