"""How many faces the anonymiser (app/faces.py) fills, and how many fills land where there is no
face, at each upscale and score: the measurement behind its settings.

Every photo is first made to look like a frame from the glasses: resized to their long side
(896 px; most COCO photos are 640 px, so those grow) and saved as a JPEG at quality 50, about as
rough as the decoded video stream. Times are for faces.detect alone.

- Faces filled: WIDER FACE's validation photos, 39,112 valid labelled faces. A face counts as
  filled when the middle of its labelled box is inside a filled ellipse, which is generous to a
  fill that covers only part of a face. Faces WIDER marks invalid (too unclear to label) are left
  out. Fills that cover no labelled face are counted too, but many of them are faces WIDER left
  unlabelled, mostly in crowds, or the backs of heads.
- Fills without faces (--no-people): any folder of photos with no people in them, where every fill
  is a false one. Ours: 313 COCO val2017 photos whose five captions mention a room or a household
  object and no person or animal (listed from the Hugging Face mirror phiyodr/coco2017).

Get WIDER_val.zip and wider_face_split.zip (http://shuoyang1213.me/WIDERFACE/, mirrored on Hugging
Face as CUHK-CSE/wider_face) and unzip both into one folder. Then, in backend/:

    uv run python -m scripts.face_recall WIDER_FOLDER [--no-people FOLDER] [--every N]
"""

import argparse
import io
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
from PIL import Image

from app import faces

FRAME_SIDE = 896  # the long side of the frames the glasses send (504x896)
QUALITY = 50
UPSCALES = [1, 1.5, 2]
SCORES = [0.3, 0.4, 0.5, 0.6, 0.7, 0.9]
BUCKETS = [(0, 10), (10, 16), (16, 24), (24, 32), (32, 64), (64, 100_000)]  # face height, px


def labelled(folder: Path):
    """(photo, [(x, y, width, height, invalid)]) for every WIDER FACE validation photo."""
    lines = iter(
        (folder / "wider_face_split" / "wider_face_val_bbx_gt.txt").read_text().split("\n")
    )
    for name in lines:
        if not name.strip():
            return
        count = int(next(lines))
        rows = [[int(n) for n in next(lines).split()[:10]] for _ in range(max(count, 1))]
        boxes = [(r[0], r[1], r[2], r[3], r[7]) for r in rows if r[2] > 0 and r[3] > 0]
        yield folder / "WIDER_val" / "images" / name, boxes if count else []


def frame_like(path: Path) -> tuple[Image.Image, float]:
    """The photo made to look like a frame from the glasses, and how much it was shrunk."""
    photo = Image.open(path).convert("RGB")
    shrink = FRAME_SIDE / max(photo.size)
    buffer = io.BytesIO()
    photo.resize((round(photo.width * shrink), round(photo.height * shrink))).save(
        buffer, "JPEG", quality=QUALITY
    )
    return Image.open(buffer).convert("RGB"), shrink


def filled(x: float, y: float, face: faces.Face) -> bool:
    """Whether the point is inside the ellipse that faces.cover fills for this face."""
    half_width, half_height = face.width * faces.ENLARGE / 2, face.height * faces.ENLARGE / 2
    dx = (x - face.x - face.width / 2) / half_width
    dy = (y - face.y - face.height / 2) / half_height
    return dx * dx + dy * dy <= 1


def bucket(height: float) -> int:
    return next(i for i, (low, high) in enumerate(BUCKETS) if low <= height < high)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wider", type=Path)
    parser.add_argument("--no-people", type=Path, help="a folder of photos with no people")
    parser.add_argument("--every", type=int, default=1, help="only every Nth WIDER photo")
    args = parser.parse_args()
    cv2.setNumThreads(2)  # as on a two-core VM, so the times are about what production sees
    faces.load()

    # Per (upscale, score): labelled faces and filled ones per bucket, and fills on no face.
    total, hit = defaultdict(lambda: [0] * len(BUCKETS)), defaultdict(lambda: [0] * len(BUCKETS))
    unlabelled, empty = defaultdict(int), defaultdict(int)
    took, photos = defaultdict(float), 0
    for i, (path, boxes) in enumerate(labelled(args.wider)):
        if i % args.every:
            continue
        frame, shrink = frame_like(path)
        truth = [(x * shrink, y * shrink, w * shrink, h * shrink, bad) for x, y, w, h, bad in boxes]
        middles = [(x + w / 2, y + h / 2) for x, y, w, h, _ in truth]
        photos += 1
        for upscale in UPSCALES:
            start = time.perf_counter()
            found = faces.detect(frame, upscale=upscale, min_score=min(SCORES))
            took[upscale] += time.perf_counter() - start
            for score in SCORES:
                kept = [face for face in found if face.score >= score]
                for (_, _, _, h, bad), (mx, my) in zip(truth, middles, strict=True):
                    if not bad:
                        total[upscale, score][bucket(h)] += 1
                        hit[upscale, score][bucket(h)] += any(filled(mx, my, f) for f in kept)
                unlabelled[upscale, score] += sum(
                    not any(filled(mx, my, face) for mx, my in middles) for face in kept
                )
        if photos % 250 == 0:
            print(f"{photos} WIDER photos", file=sys.stderr, flush=True)

    plain = sorted(args.no_people.glob("*.jpg")) if args.no_people else []
    for path in plain:
        frame, _ = frame_like(path)
        for upscale in UPSCALES:
            found = faces.detect(frame, upscale=upscale, min_score=min(SCORES))
            for score in SCORES:
                empty[upscale, score] += sum(face.score >= score for face in found)

    counts = total[UPSCALES[0], SCORES[0]]
    print(f"{photos} WIDER FACE validation photos with {sum(counts)} valid labelled faces", end="")
    print(f", and {len(plain)} photos with no people" if plain else "", end="")
    print(f"; all shrunk to {FRAME_SIDE} px and saved at JPEG quality {QUALITY}.")
    print("Share of faces filled, by their height in the frame (px), and fills per photo:\n")
    heads = [f"{low}-{high}" if high < 100_000 else f"{low}+" for low, high in BUCKETS]
    print(
        f"{'upscale':>7} {'score':>5} "
        + " ".join(f"{h:>6}" for h in heads)
        + "  16+ px  unlabelled  no people  ms/photo"
    )
    print(f"{'faces':>13} " + " ".join(f"{n:>6}" for n in counts))
    for upscale in UPSCALES:
        for score in SCORES:
            shares = [h / n for h, n in zip(hit[upscale, score], counts, strict=True)]
            from16 = sum(hit[upscale, score][2:]) / sum(counts[2:])
            plain_fills = f"{empty[upscale, score] / len(plain):>9.2f}" if plain else f"{'-':>9}"
            print(
                f"{upscale:>7} {score:>5} "
                + " ".join(f"{s:>6.1%}" for s in shares)
                + f"  {from16:>6.1%}  {unlabelled[upscale, score] / photos:>10.2f}  {plain_fills}"
                + f"  {took[upscale] / photos * 1000:>8.0f}"
            )


if __name__ == "__main__":
    main()
