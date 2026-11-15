"""A public, curated playback sequence with admin-only authoring."""

import json
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app import demo, showcase
from app.auth import OptionalViewerDep, ViewerDep
from app.db import SessionDep
from app.models import Showcase, ShowcaseFields

router = APIRouter(prefix="/demo", tags=["demo"])


class ShowcaseCue(BaseModel):
    """Something the wearer says at `at_s` seconds into the clip, and the `answer` Gemma gave,
    recorded beforehand. One that `stays` is shown as active (a trigger, say) from then on, until
    `until_s` or until a later cue `stops` it: by its id, or "*" for every cue before it.

    The admin's editor records the answers, and the triggers that fired, by playing the clip to
    the backend as a throwaway guest (frontend: admin/recording.ts); playback only shows them."""

    id: str = Field(min_length=1, max_length=40)
    at_s: float = Field(ge=0, le=3600)
    text: str = Field(min_length=1, max_length=300)
    stays: bool = True
    stops: str | None = Field(default=None, max_length=40)
    until_s: float | None = Field(default=None, ge=0, le=3600)
    answer: str = Field(default="", max_length=1000)
    # "trigger": not something the wearer said, but what the glasses showed when a trigger
    # fired at `at_s`, recorded with the answers; `text` is that message.
    kind: Literal["prompt", "trigger"] = "prompt"


class ShowcaseWrite(ShowcaseFields):
    kind: Literal["video", "images"] = "images"
    asset_order: list[str] | None = Field(default=None, max_length=showcase.MAX_ASSETS)
    cues: list[ShowcaseCue] | None = Field(default=None, max_length=100)


def cues_json(cues: list[ShowcaseCue]) -> str:
    if len({cue.id for cue in cues}) != len(cues):
        raise HTTPException(422, "Every prompt needs its own id")
    return json.dumps([cue.model_dump() for cue in sorted(cues, key=lambda cue: cue.at_s)])


class ShowcaseAsset(BaseModel):
    name: str
    label: str
    url: str
    content_type: str


class ShowcasePublic(ShowcaseFields):
    id: int
    assets: list[ShowcaseAsset]
    cues: list[ShowcaseCue]


def public(item: Showcase) -> ShowcasePublic:
    return ShowcasePublic(
        **item.model_dump(exclude={"assets_json", "cues_json"}),
        cues=json.loads(item.cues_json),
        assets=[
            ShowcaseAsset(**a, url=f"/api/demo/clips/{item.id}/media/{a['name']}")
            for a in showcase.assets(item)
        ],
    )


def admin(viewer):
    if not viewer.is_admin:
        raise HTTPException(403, "Only the admin manages demos")


async def find(session, id: int) -> Showcase:
    item = await session.get(Showcase, id)
    if item is None:
        raise HTTPException(404, "No such demo")
    return item


@router.get("/clips", response_model=list[ShowcasePublic])
async def list_public(session: SessionDep):
    rows = await session.exec(
        select(Showcase).where(Showcase.published).order_by(Showcase.position, Showcase.id)
    )
    return [public(row) for row in rows]


@router.get("/manage", response_model=list[ShowcasePublic])
async def list_admin(session: SessionDep, viewer: ViewerDep):
    admin(viewer)
    rows = await session.exec(select(Showcase).order_by(Showcase.position, Showcase.id))
    return [public(row) for row in rows]


@router.post("/clips", response_model=ShowcasePublic, status_code=201)
async def create(body: ShowcaseWrite, session: SessionDep, viewer: ViewerDep):
    admin(viewer)
    if body.published or body.asset_order:
        raise HTTPException(422, "Create a draft, upload media, then publish")
    item = Showcase(
        **body.model_dump(exclude={"asset_order", "cues"}), cues_json=cues_json(body.cues or [])
    )
    session.add(item)
    await showcase.commit(session)
    return public(item)


@router.put("/clips/{id}", response_model=ShowcasePublic)
async def update(id: int, body: ShowcaseWrite, session: SessionDep, viewer: ViewerDep):
    admin(viewer)
    item = await find(session, id)
    current = showcase.assets(item)
    if current and body.kind != item.kind:
        raise HTTPException(422, "Create a new demo to change media type")
    if body.published and not current:
        raise HTTPException(422, "Upload media before publishing")
    if body.asset_order is not None:
        names = [a["name"] for a in current]
        if sorted(names) != sorted(body.asset_order):
            raise HTTPException(422, "The order must contain every uploaded asset exactly once")
        by_name = {a["name"]: a for a in current}
        item.assets_json = json.dumps([by_name[name] for name in body.asset_order])
    if body.cues is not None:
        item.cues_json = cues_json(body.cues)
    item.sqlmodel_update(body.model_dump(exclude={"asset_order", "cues"}))
    session.add(item)
    await showcase.commit(session)
    demo.request_sync()  # the demo memories follow the published clips
    return public(item)


@router.post("/clips/{id}/media", response_model=ShowcasePublic)
async def upload(
    id: int,
    session: SessionDep,
    viewer: ViewerDep,
    files: Annotated[list[UploadFile], File()],
    video_duration_s: Annotated[float | None, Form(gt=0, le=3600)] = None,
):
    admin(viewer)
    item = await find(session, id)
    current = showcase.assets(item)
    if not files or (item.kind == "video" and len(files) != 1):
        raise HTTPException(422, "Upload one video or an ordered batch of images")
    if item.kind == "images" and len(current) + len(files) > showcase.MAX_ASSETS:
        raise HTTPException(422, "A sequence can contain at most 100 images")
    added = []
    total = 0
    try:
        for file in files:
            limit = showcase.MAX_IMAGE if item.kind == "images" else showcase.MAX_UPLOAD
            data = await file.read(limit + 1)
            total += len(data)
            if len(data) > limit or total > showcase.MAX_UPLOAD:
                raise HTTPException(413, "Upload exceeds the media size limit")
            try:
                name, mime = await anyio.to_thread.run_sync(showcase.save, data, item.kind)
            except ValueError as error:
                raise HTTPException(415, str(error)) from error
            added.append(
                {"name": name, "label": (file.filename or "Media")[:200], "content_type": mime}
            )
        item.assets_json = json.dumps(added if item.kind == "video" else current + added)
        if item.kind == "video" and video_duration_s is not None:
            item.duration_s = video_duration_s
        session.add(item)
        with anyio.CancelScope(shield=True):
            await showcase.commit(session)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(showcase.delete, [a["name"] for a in added])
        raise
    if item.kind == "video":
        await anyio.to_thread.run_sync(showcase.delete, [a["name"] for a in current])
    demo.request_sync()
    return public(item)


@router.get("/clips/{id}/media/{name}")
async def get_media(id: int, name: str, session: SessionDep, viewer: OptionalViewerDep):
    item = await find(session, id)
    if not item.published and (viewer is None or not viewer.is_admin):
        raise HTTPException(404, "No such media")
    asset = next((a for a in showcase.assets(item) if a["name"] == name), None)
    if asset is None:
        raise HTTPException(404, "No such media")
    path = showcase.folder() / asset["name"]
    if not path.is_file():
        raise HTTPException(404, "No such media")
    return FileResponse(
        path, media_type=asset["content_type"], headers={"X-Content-Type-Options": "nosniff"}
    )


@router.delete("/clips/{id}", status_code=204)
async def remove(id: int, session: SessionDep, viewer: ViewerDep):
    admin(viewer)
    item = await find(session, id)
    names = [a["name"] for a in showcase.assets(item)]
    await session.delete(item)
    await showcase.commit(session)
    demo.request_sync()  # the demo memories follow the published clips
    await anyio.to_thread.run_sync(showcase.delete, names)


class ShowcaseOrder(BaseModel):
    ids: list[int] = Field(max_length=1000)


@router.post("/order", response_model=list[ShowcasePublic])
async def reorder(body: ShowcaseOrder, session: SessionDep, viewer: ViewerDep):
    admin(viewer)
    items = list(await session.exec(select(Showcase)))
    if sorted(body.ids) != sorted(item.id for item in items):
        raise HTTPException(422, "The order must contain every demo exactly once")
    by_id = {item.id: item for item in items}
    for position, id in enumerate(body.ids):
        by_id[id].position = position
        session.add(by_id[id])
    await showcase.commit(session)
    return [public(by_id[id]) for id in body.ids]


@router.delete("/clips/{id}/media/{name}", response_model=ShowcasePublic)
async def remove_asset(id: int, name: str, session: SessionDep, viewer: ViewerDep):
    admin(viewer)
    item = await find(session, id)
    current = showcase.assets(item)
    remaining = [a for a in current if a["name"] != name]
    if len(remaining) == len(current):
        raise HTTPException(404, "No such media")
    item.assets_json = json.dumps(remaining)
    if not remaining:
        item.published = False
    session.add(item)
    await showcase.commit(session)
    demo.request_sync()  # the demo memories follow the published clips
    await anyio.to_thread.run_sync(showcase.delete, [name])
    return public(item)
