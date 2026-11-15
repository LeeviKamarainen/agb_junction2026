"""The face anonymiser (app/faces.py) on its own, and on the way in: every image the backend
receives, over HTTP or the glasses' WebSocket, has its faces blurred before it is stored, embedded
or shown to the chat model."""

import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app import faces, vectors
from app.glasses import frames, scores
from app.llm import get_embedder
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder
from app.search import TABLE

REPO = Path(__file__).resolve().parents[2]
# NASA's portrait of the astronaut Eileen Collins (public domain, from scikit-image's sample
# images), and where her face is in it: x, y, width, height.
PORTRAIT = (Path(__file__).parent / "data" / "astronaut.jpg").read_bytes()
FACE = (178, 63, 90, 113)


def picture(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def jpeg(image: Image.Image, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", **options)
    return buffer.getvalue()


def face_blurred(image: Image.Image) -> bool:
    """Whether the middle of the portrait's face has lost its detail: its pixels vary less than
    half as much as in the photo (a standard deviation of 37; blurred, 8)."""
    x, y, w, h = FACE
    middle = image.convert("L").crop((x + w // 4, y + h // 4, x + 3 * w // 4, y + 3 * h // 4))
    return float(np.asarray(middle, float).std()) < 15


def inside(face: faces.Face, box: tuple) -> bool:
    x, y, w, h = box
    return x < face.x + face.width / 2 < x + w and y < face.y + face.height / 2 < y + h


def test_the_portrait_face_is_not_flat_to_begin_with():
    assert not face_blurred(picture(PORTRAIT))  # else the checks below would prove nothing


def test_finds_the_face_in_a_portrait():
    (face,) = faces.detect(picture(PORTRAIT))

    assert inside(face, FACE)


def test_a_face_taller_than_a_third_of_the_image_is_not_filled():
    """The price of the cap that keeps big false fills off scenes: a face this close is left."""
    x, y, w, h = FACE
    close_up = picture(PORTRAIT).crop((x - w // 4, y - h // 4, x + w + w // 4, y + h + h // 4))

    assert faces.detect(close_up) == []
    assert len(faces.detect(close_up, max_face=float("inf"))) == 1  # found, left for its size


def test_finds_small_faces_in_a_glasses_frame():
    """Faces 32 and 48 px tall, in a 504x896 frame compressed as hard as the glasses' video
    stream. They score about 0.86 and 0.92, so OpenCV's usual minimum of 0.9 misses the smaller;
    how the score was chosen is measured by backend/scripts/face_recall.py, not here."""
    x, y, w, h = FACE
    head = picture(PORTRAIT).crop((x - w // 4, y - h // 4, x + w + w // 4, y + h + h // 4))
    frame, placed = Image.new("RGB", (504, 896), (96, 112, 104)), []
    for left, height in [(80, 32), (300, 48)]:
        scale = height / h
        frame.paste(
            head.resize((round(head.width * scale), round(head.height * scale))), (left, 400)
        )
        placed.append((left + w // 4 * scale, 400 + h // 4 * scale, w * scale, h * scale))

    found = faces.detect(picture(jpeg(frame, quality=50)))

    assert all(any(inside(face, box) for face in found) for box in placed)


def test_cover_blurs_the_enlarged_face_and_nothing_else():
    rows, columns = np.indices((200, 200))
    board = ((rows // 4 + columns // 4) % 2 * 255).astype(np.uint8)  # 4 px black and white squares
    image = Image.fromarray(np.stack([board] * 3, axis=-1))

    covered = np.asarray(faces.cover(image, [faces.Face(60, 60, 80, 80, 0.9)]), float)

    face = covered[70:130, 70:130]
    assert face.std() < 3 and 120 < face.mean() < 135  # the squares blurred into one grey
    assert covered[143:150, 98:102].std() < 3  # below the face's box, inside the enlarged one
    untouched = np.ones((200, 200), bool)
    untouched[47:154, 47:154] = False  # the enlarged box: 1.3 times 80 px about the middle
    assert (covered[untouched] == np.asarray(image)[untouched]).all()


def test_anonymise_blurs_the_face_turns_the_image_upright_and_drops_metadata():
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: turn it a quarter clockwise to view it
    exif[0x010F] = "Meta"  # Make
    sideways = jpeg(picture(PORTRAIT).rotate(90, expand=True), exif=exif, comment=b"Anna, Espoo")
    assert Image.open(io.BytesIO(sideways)).info["comment"] == b"Anna, Espoo"

    result = Image.open(io.BytesIO(faces.anonymise(sideways)))

    assert result.format == "JPEG"
    assert face_blurred(result)  # where the face is when the portrait is upright
    assert not result.getexif()
    assert "comment" not in result.info


def test_anonymise_refuses_an_image_too_large_to_decode_safely(monkeypatch):
    """Pillow refuses images over twice its pixel limit; here the limit is lowered so that the
    portrait counts as one."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100_000)

    with pytest.raises(ValueError, match="Too large"):
        faces.anonymise(PORTRAIT)


def test_anonymise_refuses_what_is_not_an_image():
    with pytest.raises(ValueError):
        faces.anonymise(b"\xff\xd8\xff and then no picture")


WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions
WATCHED = "a person"  # what the tests' trigger watches for


def unit(axis: int) -> list[float]:
    return [1.0 if i == axis else 0.0 for i in range(WIDTH)]


def toward_watched(amount: float) -> list[float]:
    """An image whose similarity to WATCHED is `amount`."""
    vector = unit(2)
    vector[0], vector[2] = amount, (1 - amount**2) ** 0.5
    return vector


def decoded(data_url: str) -> Image.Image:
    return picture(base64.b64decode(data_url.partition(",")[2]))


class Models:
    """Fake embedding and chat models that keep every image they are given. Every image embeds
    as `image`; WATCHED points the same way as unit(0), so a test brings it into view by setting
    `image` to unit(0)."""

    def __init__(self):
        self.embedded: list[Image.Image] = []
        self.shown: list[Image.Image] = []
        self.image = unit(0)
        self.answer = json.dumps({"say": "Someone is at the door."})

    async def embed(self, inputs, input_type="query"):
        self.embedded += [decoded(i) for i in inputs if i.startswith("data:image/")]
        return [
            self.image if i.startswith("data:image/") else unit(0 if i == WATCHED else WIDTH - 1)
            for i in inputs
        ]

    async def chat(self, messages, _tools):
        self.shown += [
            decoded(part["image_url"]["url"])
            for message in messages
            if isinstance(message["content"], list)
            for part in message["content"]
            if part["type"] == "image_url"
        ]
        return {"role": "assistant", "content": self.answer}

    def all_filled(self) -> bool:
        """Whether the models were given images, and every one had its face filled."""
        images = self.embedded + self.shown
        return bool(images) and all(map(face_blurred, images))


@pytest.fixture
def models(client):
    frames.reset_streams()
    fake = Models()
    app.dependency_overrides[optional_embedder] = lambda: fake.embed
    app.dependency_overrides[get_embedder] = lambda: fake.embed
    app.dependency_overrides[optional_chat] = lambda: fake.chat
    yield fake
    for dependency in (optional_embedder, get_embedder, optional_chat):
        app.dependency_overrides.pop(dependency, None)


def send_frame(client, source="single"):
    files = {"frame": ("f.jpg", PORTRAIT, "image/jpeg")}
    data = {"source": source, "stream_id": "s1" if source == "stream" else ""}
    reply = client.post("/api/glasses/frames", files=files, data=data)
    assert reply.status_code == 200, reply.text
    return reply.json()


def stored_image(client, memory_id: int) -> Image.Image:
    return picture(client.get(f"/api/memories/{memory_id}/image").content)


def test_a_frame_is_stored_embedded_and_shown_with_its_face_blurred(client, models):
    """The chat model sees a frame when a trigger fires on it, so the portrait is streamed until
    the trigger's level is measured (as in test_frames.py), then brought into view."""
    instruction = "Say who is there."  # the goal: what the wearer asked for
    goal = {"text": instruction, "triggers": [{"name": "Anyone", "watch_for": WATCHED}]}
    assert client.post("/api/glasses/goals", json=goal).status_code == 201
    for i in range(scores.MIN_FRAMES):
        models.image = toward_watched(0.60 + 0.01 * (i % 3))
        send_frame(client, "stream")
    models.image = unit(0)

    fired = send_frame(client, "stream")

    assert fired["message"] is not None and fired["stored"]
    assert face_blurred(stored_image(client, fired["memory_id"]))
    assert models.shown and models.all_filled()


def test_a_frame_over_the_socket_is_stored_and_embedded_with_its_face_blurred(client, models):
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "memory_frame", "id": 1, "source": "single"})
        ws.send_bytes(PORTRAIT)
        result = ws.receive_json()

    assert result["type"] == "memory_result" and result["result"]["stored"], result
    assert face_blurred(stored_image(client, result["result"]["memory_id"]))
    assert models.all_filled()


def test_an_uploaded_image_memory_has_its_face_blurred(client, models):
    reply = client.post("/api/memories/image", files={"file": ("p.jpg", PORTRAIT, "image/jpeg")})

    assert reply.status_code == 201
    assert face_blurred(stored_image(client, reply.json()["id"]))
    assert models.all_filled()


@pytest.mark.parametrize("how", ["http", "socket"])
def test_a_frame_that_is_not_an_image_is_refused(client, models, how):
    """It starts like a JPEG, so only the anonymiser finds out that it is not one."""
    broken = b"\xff\xd8\xff and then no picture"
    if how == "http":
        files = {"frame": ("f.jpg", broken, "image/jpeg")}
        status = client.post("/api/glasses/frames", files=files).status_code
    else:
        with client.websocket_connect("/api/glasses/ws") as ws:
            ws.send_json({"type": "memory_frame", "id": 1, "source": "single"})
            ws.send_bytes(broken)
            status = ws.receive_json()["status"]

    assert status == 415
    assert not models.embedded and not models.shown


def test_uploaded_demo_images_have_their_faces_blurred(client):
    clip = client.post("/api/demo/clips", json={"title": "Portrait"}).json()
    uploaded = client.post(
        f"/api/demo/clips/{clip['id']}/media",
        files={"files": ("portrait.jpg", PORTRAIT, "image/jpeg")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert face_blurred(picture(client.get(uploaded.json()["assets"][0]["url"]).content))


def test_demo_video_frames_are_stored_with_their_faces_blurred(client, models, db, tmp_path):
    """Demo videos are kept as uploaded, so their frames are blurred when stored as memories."""
    import cv2

    from app import demo

    still = cv2.imdecode(np.frombuffer(PORTRAIT, np.uint8), cv2.IMREAD_COLOR)
    path = tmp_path / "portrait.mp4"
    height, width = still.shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 2, (width, height))
    for _ in range(2):
        writer.write(still)
    writer.release()
    clip = client.post("/api/demo/clips", json={"title": "Portrait", "kind": "video"}).json()
    files = {"files": ("portrait.mp4", path.read_bytes(), "video/mp4")}
    assert client.post(f"/api/demo/clips/{clip['id']}/media", files=files).status_code == 200
    client.put(f"/api/demo/clips/{clip['id']}", json={**clip, "published": True})

    async def work(session):
        return await demo.sync(session, models.embed)

    assert db(work) == 1
    stored = [m for m in client.get("/api/memories").json() if m["collection"] == "demo"]
    assert stored and all(face_blurred(stored_image(client, m["id"])) for m in stored)
    assert models.all_filled()


def test_the_admins_stream_wall_shows_frames_with_their_faces_blurred(client, models):
    """GET /api/admin/streams/{id}/frame serves what clients send, so it must be the blurred
    frame."""
    send_frame(client, "stream")
    (feed,) = [f for f in client.get("/api/admin/streams").json() if f["kind"] == "stream"]

    shown = client.get(f"/api/admin/streams/{feed['id']}/frame")

    assert shown.status_code == 200
    assert face_blurred(picture(shown.content))


def test_every_upload_route_is_one_of_those_tested_here():
    """A new multipart upload route fails this until it fills faces first and has its test here.
    It cannot see an image sent another way: the WebSocket's frames are tested above by hand, and
    so must be any new message or base64 image in JSON."""
    uploads = {
        path
        for path, operations in app.openapi()["paths"].items()
        for operation in operations.values()
        if "multipart/form-data" in operation.get("requestBody", {}).get("content", {})
    }

    assert uploads == {
        "/api/glasses/frames",
        "/api/memories/image",
        "/api/demo/clips/{id}/media",
    }


@pytest.mark.parametrize(
    "name",
    ["faces.py", "face_detection_yunet_2023mar.onnx", "face_detection_yunet_2023mar.LICENSE"],
)
def test_the_memory_api_has_the_same_anonymiser(name):
    """The memory API (memory/) deploys on its own, so it keeps a copy of this module."""
    copy = REPO / "memory" / "src" / "glass_memory" / name

    assert copy.read_bytes() == (REPO / "backend" / "app" / name).read_bytes(), (
        f"copy backend/app/{name} to memory/src/glass_memory/"
    )
