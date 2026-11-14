"""ask.ask and ask.summarise against a fake urlopen: no request leaves the test."""

import base64
import io
import json

import pytest

from glass_memory import ask


@pytest.fixture
def sent(monkeypatch):
    """Requests the code made, as (request, timeout) pairs; Gemma always replies "a reply"."""
    requests = []

    def urlopen(request, timeout=None):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "a reply"}}]}).encode())

    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("GEMMA_API_KEY", "dummy-token")
    monkeypatch.delenv("GEMMA_URL", raising=False)
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    return requests


def stills(folder, n):
    """n files whose content is their own number, and the seconds each was seen at."""
    paths = [folder / f"{i:05d}.jpg" for i in range(n)]
    for i, path in enumerate(paths):
        path.write_bytes(str(i).encode())
    return paths, [float(i) for i in range(n)]


def stills_sent(body):
    """The numbers of the stills in a request body, and the text label before each."""
    content = body["messages"][0]["content"]
    images = [i for i, part in enumerate(content) if part["type"] == "image_url"]
    numbers = [int(base64.b64decode(content[i]["image_url"]["url"].split(",")[1])) for i in images]
    return numbers, [content[i - 1]["text"] for i in images]


def test_summarise_sends_48_evenly_spaced_stills_at_70_tokens(sent, tmp_path):
    assert ask.summarise(*stills(tmp_path, 100)) == "a reply"

    ((request, _),) = sent
    body = json.loads(request.data)
    numbers, labels = stills_sent(body)
    gaps = {b - a for a, b in zip(numbers, numbers[1:], strict=False)}
    assert len(numbers) == 48 and numbers[0] == 0 and gaps == {2, 3}
    assert labels[1] == f"Still 2, seen at {numbers[1]} s:"
    assert body["mm_processor_kwargs"] == {"max_soft_tokens": 70}
    assert body["messages"][0]["content"][-1] == {"type": "text", "text": ask.SUMMARY}
    assert request.get_header("Authorization") == "Bearer dummy-token"


def test_summarise_sends_every_still_of_a_short_batch(sent, tmp_path):
    ask.summarise(*stills(tmp_path, 5))

    assert stills_sent(json.loads(sent[0][0].data))[0] == [0, 1, 2, 3, 4]


def test_ask_sends_the_stills_then_the_question_at_default_tokens(sent, tmp_path):
    assert ask.ask("where are my keys?", *stills(tmp_path, 3)) == "a reply"

    ((request, _),) = sent
    body = json.loads(request.data)
    assert request.full_url == "http://localhost:8000/v1/chat/completions"
    assert stills_sent(body)[0] == [0, 1, 2]
    assert body["messages"][0]["content"][-1]["text"] == "where are my keys?"
    assert "mm_processor_kwargs" not in body and body["model"] == "google/gemma-4-31B-it"
