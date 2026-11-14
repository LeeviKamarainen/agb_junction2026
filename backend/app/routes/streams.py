"""The admin's stream wall: the frames clients are sending right now (app/glasses/feeds.py)."""

from fastapi import APIRouter, HTTPException, Response

from app.auth import Viewer, ViewerDep
from app.glasses import feeds

router = APIRouter(prefix="/admin/streams", tags=["admin"])


def _admin(viewer: Viewer) -> None:
    if not viewer.is_admin:
        raise HTTPException(403, "Only the admin sees the streams")


@router.get("", response_model=list[feeds.FeedPublic])
async def list_streams(viewer: ViewerDep):
    """Every client that sent a frame in the last minute, newest first: who, which app, what
    kind of frames and how fast. Faces are blurred in every frame, as everywhere."""
    _admin(viewer)
    return feeds.active()


@router.get(
    "/{feed_id}/frame",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def latest_frame(feed_id: str, viewer: ViewerDep) -> Response:
    """The newest frame of a feed, as a JPEG. 404 once the feed has gone quiet."""
    _admin(viewer)
    jpeg = feeds.latest(feed_id)
    if jpeg is None:
        raise HTTPException(404, "No such stream, or it stopped")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
