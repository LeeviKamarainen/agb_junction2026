"""How much of a scene the anonymiser's fills hide, against how many faces they cover, for a
range of minimum scores, caps on face height and enlargements: the measurement behind MIN_SCORE
and MAX_FACE in app/faces.py. Uses the same photos as scripts/face_recall.py, made frame-like the
same way, searched at the photos' own size.

- Faces covered: WIDER FACE faces 16 px or taller (and 32 px or taller) with at least three
  quarters of their labelled box inside the fills.
- Hidden: the share of each photo with no people that the fills cover, averaged; also the fills
  per photo, and how many photos got a fill over a quarter of the frame.

In backend/:

    uv run python -m scripts.face_fill_area WIDER_FOLDER NO_PEOPLE_FOLDER [--every N]
"""

import argparse
import itertools
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from app import faces
from scripts.face_recall import frame_like, labelled

SCORES = [0.5, 0.6, 0.7]
CAPS = [None, 1 / 3, 1 / 4]  # the tallest face filled, as a share of the frame's long side
ENLARGES = [1.3, 1.15, 1.0]
SETTINGS = list(itertools.product(SCORES, CAPS, ENLARGES))


def kept(found, size, score, cap):
    return [f for f in found if f.score >= score and not (cap and f.height > cap * max(size))]


def filled(size, found, score, cap, enlarge):
    """Which pixels the fills cover, as faces.cover would draw them with these settings."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for f in kept(found, size, score, cap):
        middle_x, middle_y = f.x + f.width / 2, f.y + f.height / 2
        half_width, half_height = f.width * enlarge / 2, f.height * enlarge / 2
        box = (middle_x - half_width, middle_y - half_height)
        draw.ellipse((*box, middle_x + half_width, middle_y + half_height), fill=1)
    return np.asarray(mask, bool)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wider", type=Path)
    parser.add_argument("no_people", type=Path)
    parser.add_argument("--every", type=int, default=1, help="only every Nth WIDER photo")
    args = parser.parse_args()
    faces.load()

    covered16, covered32, n16, n32 = dict.fromkeys(SETTINGS, 0), dict.fromkeys(SETTINGS, 0), 0, 0
    for i, (path, boxes) in enumerate(labelled(args.wider)):
        if i % args.every:
            continue
        frame, shrink = frame_like(path)
        truth = [(x * shrink, y * shrink, w * shrink, h * shrink) for x, y, w, h, bad in boxes]
        truth = [t for t, (*_, bad) in zip(truth, boxes, strict=True) if not bad and t[3] >= 16]
        n16, n32 = n16 + len(truth), n32 + sum(t[3] >= 32 for t in truth)
        if not truth:
            continue
        found = faces.detect(frame, upscale=1, min_score=min(SCORES), max_face=float("inf"))
        for setting in SETTINGS:
            mask = filled(frame.size, found, *setting)
            for x, y, w, h in truth:
                box = mask[int(y) : int(y + h) + 1, int(x) : int(x + w) + 1]
                if box.size and box.mean() >= 0.75:
                    covered16[setting] += 1
                    covered32[setting] += h >= 32

    hidden, fills, big = (
        dict.fromkeys(SETTINGS, 0.0),
        dict.fromkeys(SETTINGS, 0),
        dict.fromkeys(SETTINGS, 0),
    )
    plain = sorted(args.no_people.glob("*.jpg"))
    for path in plain:
        frame, _ = frame_like(path)
        found = faces.detect(frame, upscale=1, min_score=min(SCORES), max_face=float("inf"))
        quarter = frame.width * frame.height / 4
        for setting in SETTINGS:
            score, cap, enlarge = setting
            mine = kept(found, frame.size, score, cap)
            hidden[setting] += filled(frame.size, found, *setting).mean()
            fills[setting] += len(mine)
            big[setting] += any(np.pi * f.width * f.height * enlarge**2 / 4 > quarter for f in mine)

    print(f"{n16} WIDER faces 16+ px ({n32} of them 32+ px); {len(plain)} photos with no people")
    print(
        "score   cap  enlarge  covered 16+  covered 32+  hidden  fills/photo  photos w/ huge fill"
    )
    for setting in SETTINGS:
        score, cap, enlarge = setting
        cap_text = "none" if cap is None else f"1/{round(1 / cap)}"
        print(
            f"{score:>5}  {cap_text:>4}  {enlarge:>7}  {covered16[setting] / n16:>11.1%}"
            f"  {covered32[setting] / n32:>11.1%}  {hidden[setting] / len(plain):>6.2%}"
            f"  {fills[setting] / len(plain):>11.2f}  {big[setting]:>19}"
        )


if __name__ == "__main__":
    main()
