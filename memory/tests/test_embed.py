"""embed.store and embed.load with a fake embedder: the embedding model is never loaded here."""

import numpy as np
import polars as pl
import pytest

from glass_memory import embed


def fake_vectors(paths, batch=32):
    """One unit vector per still, drawn from the number in its file name; the still must exist."""
    assert all(path.is_file() for path in paths)
    x = np.array(
        [np.random.default_rng(int(path.stem)).normal(size=512) for path in paths], np.float32
    )
    return x / np.linalg.norm(x, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def no_encoder(monkeypatch):
    monkeypatch.setattr(embed, "embed_images", fake_vectors)
    monkeypatch.setattr(
        embed, "encoder", lambda: pytest.fail("the test loaded the embedding model")
    )


def stills(out_dir, numbers):
    """Empty files for stills in out_dir/"keyframes", as (t_s, file name) pairs half a second
    apart."""
    (out_dir / "keyframes").mkdir(parents=True)
    frames = [(i * 0.5, f"{number:05d}.jpg") for i, number in enumerate(numbers)]
    for _, name in frames:
        (out_dir / "keyframes" / name).touch()
    return frames


def test_store_writes_index_and_embeddings_in_frame_order(tmp_path):
    frames = stills(tmp_path, [7, 3, 9, 0, 5, 1, 8, 2])  # names out of order: rows follow frames
    index = embed.store(frames, tmp_path)

    assert index.equals(pl.read_parquet(tmp_path / "index.parquet"))
    assert index.schema == {
        "t_s": pl.Float64,
        "keyframe": pl.String,
        "x": pl.Float32,
        "y": pl.Float32,
    }
    assert list(zip(index["t_s"], index["keyframe"], strict=True)) == frames
    paths = [tmp_path / "keyframes" / name for _, name in frames]
    assert np.array_equal(np.load(tmp_path / "embeddings.npy"), fake_vectors(paths))
    xy = index.select("x", "y").to_numpy()
    assert np.isfinite(xy).all() and len(np.unique(xy, axis=0)) == len(frames)


def test_store_lays_a_small_batch_on_a_line(tmp_path):
    index = embed.store(
        [(t, name) for (_, name), t in zip(stills(tmp_path, [0, 1, 2]), [0, 2, 4], strict=True)],
        tmp_path,
    )

    assert index["x"].to_list() == [0.0, 1.0, 2.0] and index["y"].to_list() == [0.0, 0.0, 0.0]
    assert index["t_s"].to_list() == [0.0, 2.0, 4.0] and index["t_s"].dtype == pl.Float64


def test_load_round_trips_what_store_wrote(tmp_path):
    stored = embed.store(stills(tmp_path, [4, 2, 6]), tmp_path)
    index, embeddings = embed.load(tmp_path)

    assert index.drop("keyframe").equals(stored.drop("keyframe"))
    assert index["keyframe"].to_list() == [
        str(tmp_path / "keyframes" / name) for name in stored["keyframe"]
    ]
    assert np.array_equal(embeddings, np.load(tmp_path / "embeddings.npy")) and len(
        embeddings
    ) == len(index)
