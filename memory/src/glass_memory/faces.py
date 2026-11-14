"""Faces in images made unrecognisable before anything keeps them: every face found is filled.

Images from the glasses show bystanders, so each one goes through `anonymise` as it arrives, before
it is stored, embedded or shown to a model. Why a fill and not a blur, why this detector and where
its settings come from: docs/adr/0002-anonymise-faces.md at the repo root.

This file is both backend/app/faces.py and memory/src/glass_memory/faces.py, each beside the same
model, because the backend and the memory API deploy separately; a backend test fails when the two
copies differ.

To see what it does to images, without storing anything: `python -m app.faces IMAGE...` in
backend/ (`python -m glass_memory.faces IMAGE...` in memory/) writes IMAGE.anonymised.jpg beside
each image and prints each face found, with its size and score, and the time the search took.
"""

import argparse
import io
import threading
import time
from functools import cache
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

# YuNet, OpenCV's face detector, from github.com/opencv/opencv_zoo; its MIT licence is beside it.
MODEL = Path(__file__).with_name("face_detection_yunet_2023mar.onnx")
# The settings below were measured on WIDER FACE's labelled photos made to look like the glasses'
# frames, and on photos of rooms with no one in them (backend/scripts/face_recall.py, ADR 0002).
#
# Images are searched at their own size. Upscaled 1.5 or 2 times they filled 11-14 points more of
# the faces 10-16 px tall and 4-5 more of those 16-24 px, but put 1.8 to 2.4 times as many fills
# on things that are not faces and took three to five times as long; a lower MIN_SCORE gains as
# much for the same or fewer false fills.
UPSCALE = 1
# Larger images are shrunk to this long side first (twice the frames' 896 px): the search in a
# 12 MP photo then takes 0.11 s instead of 0.44 s and over 1 GB of memory, and is still more
# detailed than in a frame.
MAX_SIDE = 1792
# The lowest score a face is filled at. 0.5 fills 85% of faces 16 px or taller and 88-94% of those
# 32 px or taller, and puts 0.25 fills per photo on photos with no one in them: mostly small (a
# clock, a fruit), but one photo in 60 gets one over a quarter of the frame, hiding a bowl or a
# phone. 0.4 fills 88% of faces and doubles the false fills; OpenCV's usual 0.9 fills 29%.
MIN_SCORE = 0.5
# Each face's box is enlarged this much about its middle before it is filled, so hair, ears and
# chin go too: the default of deface (github.com/ORB-HD/deface), a widely used face-hiding tool.
ENLARGE = 1.3
# Every face is filled with this one grey, so a fill tells nothing about the face, not even its
# skin tone.
FILL = (128, 128, 128)
# The JPEG is decoded and saved again downstream; this high a quality keeps that second loss small.
QUALITY = 95


class Face(NamedTuple):
    """A face found, in pixels of the image that was searched."""

    x: float
    y: float
    width: float
    height: float
    score: float


def anonymise(data: bytes) -> bytes:
    """The image with every face found filled, turned upright, as a JPEG with no metadata (so no
    EXIF and no GPS position). Raises ValueError for bytes that are not an image."""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Not an image that can be read (JPEG, PNG, WebP, ...)") from error
    except Image.DecompressionBombError as error:
        raise ValueError(f"Too large an image: {error}") from error
    covered = cover(image, detect(image))
    covered.info.clear()  # else Pillow writes the original's JPEG comment back out
    buffer = io.BytesIO()
    covered.save(buffer, "JPEG", quality=QUALITY)
    return buffer.getvalue()


def detect(
    image: Image.Image, upscale: float = UPSCALE, min_score: float = MIN_SCORE
) -> list[Face]:
    """The faces in an RGB image. The keywords are for backend/scripts/face_recall.py, which
    measures other settings against these defaults."""
    scale = min(upscale, MAX_SIDE / max(image.size))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    searched = np.asarray(image.resize(size, Image.Resampling.BILINEAR))
    with _lock:  # one detector for every thread, and it keeps the settings it was last given
        detector = _detector()
        detector.setInputSize(size)
        detector.setScoreThreshold(min_score)
        _, found = detector.detect(cv2.cvtColor(searched, cv2.COLOR_RGB2BGR))
    if found is None:
        return []
    # A row is the box, five landmarks (two numbers each) and the score.
    return [
        Face(x / scale, y / scale, width / scale, height / scale, score)
        for x, y, width, height, *_, score in found.tolist()
    ]


def cover(image: Image.Image, faces: list[Face]) -> Image.Image:
    """A copy of the image with each face's box, enlarged ENLARGE times about its middle, filled
    as an ellipse in FILL."""
    covered = image.copy()
    draw = ImageDraw.Draw(covered)
    for face in faces:
        middle_x, middle_y = face.x + face.width / 2, face.y + face.height / 2
        half_width, half_height = face.width * ENLARGE / 2, face.height * ENLARGE / 2
        draw.ellipse(
            (
                middle_x - half_width,
                middle_y - half_height,
                middle_x + half_width,
                middle_y + half_height,
            ),
            fill=FILL,
        )
    return covered


def load() -> None:
    """Loads the detector now, so that a missing or broken model stops the app as it starts."""
    _detector()


_lock = threading.Lock()


@cache
def _detector():
    return cv2.FaceDetectorYN.create(str(MODEL), "", (MAX_SIDE, MAX_SIDE), MIN_SCORE)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill the faces in images, to check the result.")
    parser.add_argument("images", nargs="+", type=Path)
    load()
    for path in parser.parse_args().images:
        image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        start = time.perf_counter()
        faces = detect(image)
        took = (time.perf_counter() - start) * 1000
        out = path.with_name(f"{path.stem}.anonymised.jpg")
        cover(image, faces).save(out, quality=QUALITY)
        print(f"{path}: {len(faces)} faces, searched in {took:.0f} ms -> {out}")
        for face in faces:
            print(
                f"  {face.width:.0f}x{face.height:.0f} px at ({face.x:.0f}, {face.y:.0f}),"
                f" score {face.score:.2f}"
            )


if __name__ == "__main__":
    main()
