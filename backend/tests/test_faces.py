"""The face anonymiser (app/faces.py) on its own: the faces it finds, what it fills, what it
refuses, and its copy in the memory API."""

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app import faces

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


def face_filled(image: Image.Image) -> bool:
    """Whether the middle of the portrait's face is one flat colour, give or take JPEG noise."""
    x, y, w, h = FACE
    middle = image.convert("L").crop((x + w // 4, y + h // 4, x + 3 * w // 4, y + 3 * h // 4))
    return float(np.asarray(middle, float).std()) < 3


def inside(face: faces.Face, box: tuple) -> bool:
    x, y, w, h = box
    return x < face.x + face.width / 2 < x + w and y < face.y + face.height / 2 < y + h


def test_the_portrait_face_is_not_flat_to_begin_with():
    assert not face_filled(picture(PORTRAIT))  # else the checks below would prove nothing


def test_finds_the_face_in_a_portrait():
    (face,) = faces.detect(picture(PORTRAIT))

    assert inside(face, FACE)


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


def test_cover_fills_the_enlarged_face_and_nothing_else():
    rows, columns = np.indices((200, 200))
    board = ((rows // 4 + columns // 4) % 2 * 255).astype(np.uint8)  # 4 px black and white squares
    image = Image.fromarray(np.stack([board] * 3, axis=-1))

    covered = np.asarray(faces.cover(image, [faces.Face(60, 60, 80, 80, 0.9)]))

    assert (covered[70:130, 70:130] == faces.FILL).all()  # the face
    # below the face's box, inside the enlarged one
    assert (covered[143:150, 98:102] == faces.FILL).all()
    untouched = np.ones((200, 200), bool)
    untouched[47:154, 47:154] = False  # the enlarged box: 1.3 times 80 px about the middle
    assert (covered[untouched] == np.asarray(image)[untouched]).all()


def test_anonymise_fills_the_face_turns_the_image_upright_and_drops_metadata():
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: turn it a quarter clockwise to view it
    exif[0x010F] = "Meta"  # Make
    sideways = jpeg(picture(PORTRAIT).rotate(90, expand=True), exif=exif, comment=b"Anna, Espoo")
    assert Image.open(io.BytesIO(sideways)).info["comment"] == b"Anna, Espoo"

    result = Image.open(io.BytesIO(faces.anonymise(sideways)))

    assert result.format == "JPEG"
    assert face_filled(result)  # where the face is when the portrait is upright
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
