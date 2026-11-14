"""embed.keyframes, embed.store and embed.load with a fake embedder: the embedding model is never
loaded here."""

import av
import numpy as np
import polars as pl
import pytest
from PIL import Image

from glass_memory import embed, faces


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


def test_text_gets_the_model_cards_prompt_for_its_side_of_a_search(monkeypatch):
    prompts = []

    class Encoder:
        def encode(self, text, prompt_name, convert_to_numpy):
            prompts.append(prompt_name)
            return np.array([3.0, 4.0], np.float32)

    monkeypatch.setattr(embed, "encoder", Encoder)

    question = embed.embed_text("where are my keys?")
    embed.embed_text("The keys are in the hall drawer.", "document")

    assert prompts == ["SearchQuery", "Document"]
    assert np.allclose(question, [0.6, 0.8])  # unit length


def test_keyframes_are_kept_with_their_faces_blurred(tmp_path, monkeypatch):
    """A fake detector finds one face in the middle of every frame of a small checkerboard clip."""
    rows, columns = np.indices((64, 64))
    board = np.stack([((rows // 4 + columns // 4) % 2 * 255).astype(np.uint8)] * 3, axis=-1)
    video = tmp_path / "clip.mp4"
    with av.open(str(video), "w") as container:
        stream = container.add_stream("mpeg4", rate=2)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        for _ in range(4):
            container.mux(stream.encode(av.VideoFrame.from_ndarray(board, format="rgb24")))
        container.mux(stream.encode())
    monkeypatch.setattr(faces, "detect", lambda image: [faces.Face(16, 16, 32, 32, 0.9)])

    saved = embed.keyframes(video, tmp_path / "keyframes", every_s=0.5)

    assert len(saved) >= 2
    for _, name in saved:
        still = np.asarray(Image.open(tmp_path / "keyframes" / name).convert("L"), float)
        assert still[24:40, 24:40].std() < 3  # the face, blurred
        assert still[:8, :8].std() > 50  # a corner, still squares
