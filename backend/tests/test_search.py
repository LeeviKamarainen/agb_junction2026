import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlmodel import Session

from app import media, vectors
from app.db import get_engine
from app.llm import UpstreamError, get_chat, get_embedder
from app.main import app
from app.search import (
    TABLE,
    VECTOR_SEARCH_TOOL,
    Timespan,
    VectorSearchArgs,
    add_memory,
    vector_search,
)

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions
NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def unit(*axes: int) -> list[float]:
    """A vector pointing between the given axes."""
    return [1.0 if i in axes else 0.0 for i in range(WIDTH)]


# The fake embedder: each known phrase points along its own axis; anything else is far away.
PHRASES = {"keys": unit(0), "keys on a table": unit(0), "dog": unit(1), "coffee": unit(2)}


def fake_embed(texts):
    """Images (data: URLs) all embed like "keys"."""
    return [
        unit(0) if t.startswith("data:image/") else PHRASES.get(t, unit(WIDTH - 1)) for t in texts
    ]


def jpeg(colour="red", size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def memories(client):
    """Four memories, an hour apart, the newest at noon."""
    with Session(get_engine()) as session:
        rows = [
            add_memory(
                session, "image", unit(0), NOON - timedelta(hours=3), "", media.save_image(jpeg())
            ),
            add_memory(session, "image", unit(1), NOON - timedelta(hours=2), "A dog called Rex"),
            add_memory(session, "text", unit(2), NOON - timedelta(hours=1), "Coffee with Aino"),
            add_memory(session, "image", unit(0, 1), NOON, "Rex sniffing the keys"),
        ]
        session.commit()
        ids = [row.id for row in rows]
    yield ids


@pytest.fixture
def session(memories):
    with Session(get_engine()) as session:
        yield session


def test_tool_definition_matches_the_arguments_model():
    properties = VECTOR_SEARCH_TOOL["function"]["parameters"]["properties"]
    assert properties.keys() == VectorSearchArgs.model_fields.keys()
    assert VECTOR_SEARCH_TOOL["function"]["parameters"]["required"] == ["user_query"]


def test_closest_memory_comes_first(memories, session):
    results = vector_search(session, VectorSearchArgs(user_query="keys"), fake_embed)

    assert results[0].id == memories[0]
    assert results[0].distance == pytest.approx(0.0, abs=1e-6)
    assert results[0].image_url == f"/api/memories/{memories[0]}/image"
    assert results[0].created_at == NOON - timedelta(hours=3)


def test_queries_and_keywords_that_agree_win(memories, session):
    args = VectorSearchArgs(user_query="keys", alternative_queries=["dog"], keywords=["rex"])

    results = vector_search(session, args, fake_embed)

    # The last memory is second for both queries and a keyword match: it beats each query's top.
    assert results[0].id == memories[3]
    assert results[0].matched_keywords == ["rex"]
    by_id = {result.id: result for result in results}
    assert by_id[memories[1]].matched_keywords == ["rex"]
    assert by_id[memories[0]].matched_keywords == []


def test_keyword_only_hits_are_returned(memories, session):
    args = VectorSearchArgs(user_query="something unknown", keywords=["aino"], limit=1)

    (result,) = vector_search(session, args, fake_embed)

    assert result.id == memories[2]
    assert result.matched_keywords == ["aino"]


def test_timespan_and_kinds_filter(memories, session):
    window = Timespan(start=NOON - timedelta(hours=2), end=NOON)
    in_window = vector_search(
        session, VectorSearchArgs(user_query="keys", keywords=["rex"], timespan=window), fake_embed
    )
    texts = vector_search(session, VectorSearchArgs(user_query="keys", kinds=["text"]), fake_embed)

    assert {result.id for result in in_window} == {memories[1], memories[2]}  # end is exclusive
    assert [result.id for result in texts] == [memories[2]]


def test_naive_timespan_is_utc():
    assert Timespan(start=datetime(2026, 11, 14, 9)).start == datetime(2026, 11, 14, 9, tzinfo=UTC)


def test_add_memory_rejects_bad_input(client):
    with Session(get_engine()) as session:
        with pytest.raises(ValueError, match="timezone-aware"):
            add_memory(session, "image", unit(0), datetime(2026, 11, 14))
        with pytest.raises(ValueError, match="kind"):
            add_memory(session, "video", unit(0))  # type: ignore[arg-type]


def test_search_endpoint(client, memories):
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    try:
        response = client.post("/api/search", json={"user_query": "coffee", "limit": 2})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [hit["id"] for hit in response.json()][0] == memories[2]


def test_endpoints_need_the_models_configured(client):
    assert client.post("/api/search", json={"user_query": "keys"}).status_code == 503
    assert client.post("/api/ask", json={"question": "Where are my keys?"}).status_code == 503


class FakeGemma:
    """Replies from a script, and records what it was sent."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages, tools):
        self.calls.append((json.loads(json.dumps(messages)), tools))
        return self.replies.pop(0)


def tool_call(id, **arguments):
    return {
        "id": id,
        "type": "function",
        "function": {"name": "vector_search", "arguments": json.dumps(arguments)},
    }


def test_ask_searches_then_answers_from_the_results(client, memories):
    gemma = FakeGemma(
        {"content": None, "tool_calls": [tool_call("a", user_query="keys", limit=1)]},
        {"content": "On the table, at 09:00."},
    )
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: gemma
    try:
        response = client.post("/api/ask", json={"question": "Where are my keys?"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "On the table, at 09:00."
    assert body["searches"][0]["user_query"] == "keys"
    assert [source["id"] for source in body["sources"]] == [memories[0]]

    messages, tools = gemma.calls[1]
    assert tools == [VECTOR_SEARCH_TOOL]
    tool_message, images = messages[-2], messages[-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "a"
    assert json.loads(tool_message["content"])[0]["id"] == memories[0]
    assert images["role"] == "user"
    assert images["content"][-1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert [step["type"] for step in body["steps"]] == [
        "model_call",
        "tool_call",
        "tool_result",
        "model_call",
        "answer",
    ]
    assert body["steps"][3]["images_sent"] == 1


def test_ask_reports_bad_arguments_and_forces_an_answer(client, memories):
    bad = {"content": None, "tool_calls": [tool_call("a", limit=3)]}  # no user_query
    gemma = FakeGemma(bad, bad, bad, {"content": "I could not find it."})
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: gemma
    try:
        response = client.post("/api/ask", json={"question": "Where are my keys?"})
    finally:
        app.dependency_overrides.clear()

    assert response.json()["answer"] == "I could not find it."
    messages, tools = gemma.calls[-1]
    assert tools is None  # the last round withholds the tool
    assert "user_query" in messages[-1]["content"]  # the validation error went back to the model


def test_ask_stream_sends_one_step_per_line(client, memories):
    gemma = FakeGemma(
        {"content": "Looking.", "tool_calls": [tool_call("a", user_query="coffee", limit=1)]},
        {"content": "With Aino at 11:00."},
    )
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: gemma
    try:
        response = client.post("/api/ask/stream", json={"question": "Who did I have coffee with?"})
    finally:
        app.dependency_overrides.clear()

    steps = [json.loads(line) for line in response.text.splitlines()]
    assert [step["type"] for step in steps] == [
        "model_call",
        "model_text",
        "tool_call",
        "tool_result",
        "model_call",
        "answer",
    ]
    assert steps[2]["arguments"] == {"user_query": "coffee", "limit": 1}
    assert steps[3]["results"][0]["id"] == memories[2]
    assert steps[-1]["content"] == "With Aino at 11:00."


def test_ask_stream_reports_a_failing_model_server(client, memories):
    def broken(messages, tools):
        raise UpstreamError("The chat model answered 400: tool choice not enabled")

    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: broken
    try:
        response = client.post("/api/ask/stream", json={"question": "Where are my keys?"})
    finally:
        app.dependency_overrides.clear()

    last = json.loads(response.text.splitlines()[-1])
    assert last["type"] == "error" and "tool choice" in last["content"]


def test_upload_list_fetch_and_delete_an_image(client):
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    try:
        response = client.post(
            "/api/memories/image",
            files={"file": ("big.png", jpeg(size=(3000, 1500)), "image/png")},
            data={"text": "Keys on the hall table", "created_at": "2026-11-14T08:30:00Z"},
        )
        found = client.post("/api/search", json={"user_query": "keys"}).json()
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201, response.text
    memory = response.json()
    assert memory["kind"] == "image" and memory["text"] == "Keys on the hall table"
    assert memory["created_at"].startswith("2026-11-14T08:30:00")
    assert found[0]["id"] == memory["id"]
    assert [m["id"] for m in client.get("/api/memories").json()] == [memory["id"]]

    image = client.get(memory["image_url"])
    assert image.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(image.content)).size) == media.MAX_SIDE

    assert client.delete(f"/api/memories/{memory['id']}").status_code == 204
    assert client.get("/api/memories").json() == []
    assert client.get(memory["image_url"]).status_code == 404
    assert list(media.media_dir().iterdir()) == []


def test_upload_rejects_what_is_not_an_image_and_keeps_no_file(client):
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    try:
        response = client.post(
            "/api/memories/image", files={"file": ("notes.txt", b"hello", "text/plain")}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert list(media.media_dir().iterdir()) == []


def test_failed_embedding_keeps_no_memory(client):
    def broken(texts):
        raise UpstreamError("Could not reach the embedding model")

    app.dependency_overrides[get_embedder] = lambda: broken
    try:
        response = client.post(
            "/api/memories/image", files={"file": ("a.png", jpeg(), "image/png")}
        )
        text = client.post("/api/memories/text", json={"text": "Coffee with Aino"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502 and "embedding model" in response.json()["detail"]
    assert text.status_code == 502
    assert client.get("/api/memories").json() == []
    assert list(media.media_dir().iterdir()) == []


def test_add_a_text_memory(client):
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    try:
        response = client.post("/api/memories/text", json={"text": "coffee"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["kind"] == "text" and response.json()["image_url"] is None
