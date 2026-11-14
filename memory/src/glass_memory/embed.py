"""Turn a video into memories: keyframes on disk, their CLIP embeddings and an index.

.venv/bin/python -m glass_memory.embed VIDEO [--out res/items/EMB-1/<video name>]
"""

import argparse
from functools import cache
from pathlib import Path

import av
import numpy as np
import open_clip
import polars as pl
import torch
import umap
from PIL import Image

MODEL, PRETRAINED = "ViT-B-32", "laion2b_s34b_b79k"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


@cache
def clip():
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL, pretrained=PRETRAINED, device=DEVICE
    )
    return model.eval(), preprocess, open_clip.get_tokenizer(MODEL)


def _normalised(x):
    return torch.nn.functional.normalize(x.float(), dim=-1).cpu().numpy()


@torch.no_grad()
def embed_images(paths, batch=32):
    """L2-normalised float32 embeddings, one row per image."""
    model, preprocess, _ = clip()
    rows = []
    for i in range(0, len(paths), batch):
        images = torch.stack([preprocess(Image.open(p)) for p in paths[i : i + batch]])
        rows.append(_normalised(model.encode_image(images.to(DEVICE))))
    return np.concatenate(rows)


@torch.no_grad()
def embed_text(text):
    """The L2-normalised float32 embedding of a question, in the same space as the images."""
    model, _, tokenizer = clip()
    return _normalised(model.encode_text(tokenizer([text]).to(DEVICE)))[0]


def keyframes(video, out_dir, every_s=1.0, max_side=768):
    """Save one upright still every `every_s` seconds; return (seconds, file name) pairs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved, next_t = [], 0.0
    with av.open(str(video)) as container:
        for frame in container.decode(video=0):
            t = frame.time or 0.0
            if t < next_t:
                continue
            image = frame.to_image()
            if rotation := getattr(frame, "rotation", 0):
                image = image.rotate(rotation, expand=True)
            image.thumbnail((max_side, max_side))
            name = f"{len(saved):05d}.jpg"
            image.save(out_dir / name, quality=85)
            saved.append((t, name))
            next_t = t + every_s
    return saved


def layout(embeddings):
    """Map coordinates, one (x, y) row per memory: UMAP on cosine distance, a line when too few."""
    n = len(embeddings)
    if n < 5:
        return np.column_stack([np.arange(n), np.zeros(n)]).astype(np.float32)
    return umap.UMAP(n_neighbors=min(15, n - 1), metric="cosine", random_state=0).fit_transform(
        embeddings
    )


def store(frames, out_dir):
    """Embed the stills already in out_dir/"keyframes", given as (t_s, file name) pairs; write
    `embeddings.npy` and `index.parquet` (t_s, keyframe, x, y) into out_dir and return the index."""
    index = pl.DataFrame(frames, schema={"t_s": pl.Float64, "keyframe": pl.String}, orient="row")
    embeddings = embed_images([out_dir / "keyframes" / name for name in index["keyframe"]])
    xy = layout(embeddings)
    index = index.with_columns(x=pl.Series(xy[:, 0]), y=pl.Series(xy[:, 1]))
    np.save(out_dir / "embeddings.npy", embeddings)
    index.write_parquet(out_dir / "index.parquet")
    return index


def build(video, out_dir, every_s=1.0):
    """Write `keyframes/`, `embeddings.npy` and `index.parquet` (t_s, keyframe, x, y) into
    out_dir."""
    return store(keyframes(video, out_dir / "keyframes", every_s), out_dir)


def load(out_dir):
    """The index with full keyframe paths, and the embeddings in the same row order."""
    index = pl.read_parquet(out_dir / "index.parquet").with_columns(
        keyframe=pl.lit(str(out_dir / "keyframes") + "/") + pl.col("keyframe")
    )
    return index, np.load(out_dir / "embeddings.npy")


def main():
    parser = argparse.ArgumentParser(description="Turn a video into memories.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, help="default: res/items/EMB-1/<video name>")
    parser.add_argument("--every", type=float, default=1.0, help="seconds between keyframes")
    args = parser.parse_args()
    out_dir = args.out or Path("res/items/EMB-1") / args.video.stem
    index = build(args.video, out_dir, args.every)
    print(f"{len(index)} memories in {out_dir}")


if __name__ == "__main__":
    main()
