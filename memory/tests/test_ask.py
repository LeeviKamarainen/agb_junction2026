"""ask against a fake Gemma: no model is loaded and no request leaves the test. A still's width
is its number, the fake vision encoder turns a still into a vector holding that number, and the
fake processor notes what it was given."""

import io
import json

import pytest
import torch
from PIL import Image

from glass_memory import ask


class Processor:
    def __init__(self):
        self.prompts = []  # (the text parts, the image sizes, the processor arguments) per prompt

    def apply_chat_template(self, messages, processor_kwargs, **kwargs):
        content = messages[0]["content"]
        sizes = [part["image"].size for part in content if part["type"] == "image"]
        self.prompts.append(
            ([part["text"] for part in content if part["type"] == "text"], sizes, processor_kwargs)
        )
        widths = torch.tensor([[float(width)] for width, _ in sizes])
        return Inputs(input_ids=torch.zeros(1, 5), pixel_values=widths, image_position_ids=widths)

    def decode(self, tokens, skip_special_tokens):
        return f" reply of {len(tokens)} tokens "


class Inputs(dict):
    def to(self, device):
        return self


class Model:
    device = "cpu"

    def __init__(self):
        self.encoded, self.generated = [], []  # still numbers encoded; arguments of each generate

    def get_image_features(self, pixel_values, image_position_ids, return_dict):
        self.encoded += pixel_values.flatten().tolist()
        # Two tokens, two values wide, all holding the still's number.
        return type("Features", (), {"pooler_output": [row.repeat(2, 2) for row in pixel_values]})

    def generate(self, input_ids, max_new_tokens, do_sample, **given):
        self.generated.append(given)
        return torch.zeros(1, input_ids.shape[-1] + 2)


@pytest.fixture
def gemma(monkeypatch):
    model, processor = Model(), Processor()
    monkeypatch.setattr(ask, "gemma", lambda: (model, processor))
    monkeypatch.delenv("GEMMA_URL", raising=False)
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    return model, processor


@pytest.fixture
def served(monkeypatch):
    """Gemma served by an API at GEMMA_URL: the bodies posted to it; it always replies "a reply"."""
    bodies = []

    def urlopen(request, timeout=None):
        assert request.full_url == "http://gemma.test/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer dummy-token"
        bodies.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "a reply"}}]}).encode())

    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ask, "gemma", lambda: pytest.fail("the test loaded Gemma"))
    monkeypatch.setenv("GEMMA_URL", "http://gemma.test")
    monkeypatch.setenv("GEMMA_API_KEY", "dummy-token")
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    return bodies


def stills(folder, n):
    """n keyframes, still i being i + 1 pixels wide, and the seconds each was seen at."""
    (folder / "keyframes").mkdir()
    paths = [folder / "keyframes" / f"{i:05d}.jpg" for i in range(n)]
    for i, path in enumerate(paths):
        Image.new("RGB", (i + 1, 2)).save(path)
    return paths, [float(i) for i in range(n)]


def given_vectors(model):
    """The still numbers of the stored vectors the last generate was given, and no pixels with
    them."""
    given = model.generated[-1]
    assert not set(given) & set(ask.PIXEL_KEYS)
    return [int(v[0, 0].item()) - 1 for v in given["mm_encoder_outputs"]["image"].pooler_output]


def test_summarise_gives_the_vectors_of_48_evenly_spaced_stills(gemma, tmp_path):
    model, processor = gemma

    assert ask.summarise(*stills(tmp_path, 100)) == "reply of 2 tokens"

    numbers = given_vectors(model)
    gaps = {b - a for a, b in zip(numbers, numbers[1:], strict=False)}
    assert len(numbers) == 48 and numbers[0] == 0 and gaps == {2, 3}
    texts, sizes, arguments = processor.prompts[-1]
    assert (
        texts[0] == ask.INTRO
        and texts[2] == f"Still 2, seen at {numbers[1]} s:"
        and texts[-1] == ask.SUMMARY
    )
    # The prompt holds blank images of the stills' sizes, at the tokens the vectors were stored at.
    assert sizes == [(n + 1, 2) for n in numbers] and arguments == {"max_soft_tokens": 70}


def test_summarise_gives_every_still_of_a_short_batch(gemma, tmp_path):
    ask.summarise(*stills(tmp_path, 5))

    assert given_vectors(gemma[0]) == [0, 1, 2, 3, 4]


def test_vectors_are_stored_beside_the_keyframes_and_encoded_once(gemma, tmp_path):
    model, _ = gemma
    paths, times = stills(tmp_path, 3)

    ask.store(paths[:2])
    assert sorted(p.name for p in (tmp_path / "vectors").iterdir()) == ["00000.pt", "00001.pt"]
    assert torch.load(tmp_path / "vectors" / "00001.pt")["size"] == (2, 2)

    ask.ask("where are my keys?", paths, times, vectors=True)
    ask.ask("where are my keys?", paths, times, vectors=True)
    assert model.encoded == [1.0, 2.0, 3.0] and given_vectors(model) == [0, 1, 2]


def test_pooled_is_the_mean_of_each_stills_vectors_over_its_tokens(gemma, tmp_path):
    paths, _ = stills(tmp_path, 3)
    (tmp_path / "vectors").mkdir()
    torch.save(
        {"vectors": torch.tensor([[1.0, 2.0], [3.0, 6.0]]), "size": (1, 2)},
        tmp_path / "vectors" / "00000.pt",
    )

    rows = ask.pooled(paths)

    # Still 0 had vectors of two tokens; the other two were stored first, by the fake encoder.
    assert rows.dtype == "float32" and rows.tolist() == [[2.0, 4.0], [2.0, 2.0], [3.0, 3.0]]
    assert gemma[0].encoded == [2.0, 3.0]


def test_ask_reads_the_stills_at_default_tokens_unless_told_to_read_vectors(gemma, tmp_path):
    model, processor = gemma

    assert ask.ask("where are my keys?", *stills(tmp_path, 3)) == "reply of 2 tokens"

    texts, sizes, arguments = processor.prompts[-1]
    assert (
        texts[-1] == "where are my keys?" and sizes == [(1, 2), (2, 2), (3, 2)] and arguments == {}
    )
    assert set(model.generated[-1]) == set(ask.PIXEL_KEYS)
    assert model.encoded == [] and not (tmp_path / "vectors").exists()


def test_an_api_at_gemma_url_is_sent_the_stills_and_stores_no_vectors(served, tmp_path):
    paths, times = stills(tmp_path, 100)

    ask.store(paths)
    assert (
        ask.summarise(paths, times) == "a reply"
        and ask.ask("where are my keys?", paths[:3], times[:3]) == "a reply"
    )

    summary, answer = served
    images = [part for part in summary["messages"][0]["content"] if part["type"] == "image_url"]
    assert len(images) == 48 and summary["mm_processor_kwargs"] == {"max_soft_tokens": 70}
    assert images[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert summary["messages"][0]["content"][-1] == {"type": "text", "text": ask.SUMMARY}
    assert "mm_processor_kwargs" not in answer and answer["model"] == "google/gemma-4-31B-it"
    assert [part["type"] for part in answer["messages"][0]["content"]] == ["text"] + [
        "text",
        "image_url",
    ] * 3 + ["text"]
    assert not (tmp_path / "vectors").exists()
    with pytest.raises(ValueError, match="stills only"):
        ask.ask("where are my keys?", paths[:3], times[:3], vectors=True)
