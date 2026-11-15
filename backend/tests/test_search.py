import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from app import agent, demo, media, vectors
from app.llm import UpstreamError, get_chat, get_embedder
from app.main import app
from app.models import Showcase
from app.search import (
    CANDIDATES_PER_LIST,
    DEMO_OWNER,
    TABLE,
    VECTOR_SEARCH_TOOL,
    Timespan,
    VectorSearchArgs,
    _keyword_ranking,
    add_memory,
    local,
    vector_search,
)

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions
NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def unit(*axes: int) -> list[float]:
    """A vector pointing between the given axes."""
    return [1.0 if i in axes else 0.0 for i in range(WIDTH)]


# The fake embedder: each known phrase points along its own axis; anything else is far away.
PHRASES = {"keys": unit(0), "keys on a table": unit(0), "dog": unit(1), "coffee": unit(2)}


async def fake_embed(texts, input_type="query"):
    """Images (data: URLs) all embed like "keys"."""
    return [
        unit(0) if t.startswith("data:image/") else PHRASES.get(t, unit(WIDTH - 1)) for t in texts
    ]


def jpeg(colour="red", size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def memories(client, db):
    """Four memories, an hour apart, the newest at noon."""

    async def add(session):
        image = media.save_image(jpeg())
        rows = [
            await add_memory(session, "image", unit(0), NOON - timedelta(hours=3), "", image),
            await add_memory(
                session, "image", unit(1), NOON - timedelta(hours=2), "A dog called Rex"
            ),
            await add_memory(
                session, "text", unit(2), NOON - timedelta(hours=1), "Coffee with Aino"
            ),
            await add_memory(session, "image", unit(0, 1), NOON, "Rex sniffing the keys"),
        ]
        await session.commit()
        return [row.id for row in rows]

    return db(add)


@pytest.fixture
def search(db):
    """vector_search with the fake embedder over every memory (owners None), in a session of its
    own."""
    return lambda args: db(lambda session: vector_search(session, args, fake_embed, None))


def test_tool_definition_matches_the_arguments_model():
    properties = VECTOR_SEARCH_TOOL["function"]["parameters"]["properties"]
    assert properties.keys() == VectorSearchArgs.model_fields.keys()
    assert VECTOR_SEARCH_TOOL["function"]["parameters"]["required"] == ["user_query"]


def test_closest_memory_comes_first(memories, search):
    results = search(VectorSearchArgs(user_query="keys"))

    assert results[0].id == memories[0]
    assert results[0].distance == pytest.approx(0.0, abs=1e-6)
    assert results[0].image_url.startswith(f"/api/memories/{memories[0]}/image?v=")
    assert results[0].created_at == NOON - timedelta(hours=3)


def test_queries_and_keywords_that_agree_win(memories, search):
    args = VectorSearchArgs(user_query="keys", alternative_queries=["dog"], keywords=["rex"])

    results = search(args)

    # The last memory is second for both queries and a keyword match: it beats each query's top.
    assert results[0].id == memories[3]
    assert results[0].matched_keywords == ["rex"]
    by_id = {result.id: result for result in results}
    assert by_id[memories[1]].matched_keywords == ["rex"]
    assert by_id[memories[0]].matched_keywords == []


def test_keyword_only_hits_are_returned(memories, search):
    args = VectorSearchArgs(user_query="something unknown", keywords=["aino"], limit=1)

    (result,) = search(args)

    assert result.id == memories[2]
    assert result.matched_keywords == ["aino"]


def test_timespan_and_kinds_filter(memories, search):
    window = Timespan(start=NOON - timedelta(hours=2), end=NOON)
    in_window = search(VectorSearchArgs(user_query="keys", keywords=["rex"], timespan=window))
    texts = search(VectorSearchArgs(user_query="keys", kinds=["text"]))

    assert {result.id for result in in_window} == {memories[1], memories[2]}  # end is exclusive
    assert [result.id for result in texts] == [memories[2]]


def test_naive_timespan_is_the_wearers_local_time():
    # 09:00 in Helsinki in November is 07:00 UTC.
    assert Timespan(start=datetime(2026, 11, 14, 9)).start == datetime(2026, 11, 14, 7, tzinfo=UTC)


def test_a_local_timespan_selects_by_the_wearers_clock(memories, search, db):
    # The memories are at 11:00, 12:00, 13:00 and 14:00 Helsinki time (09:00 to 12:00 UTC).
    window = Timespan(start=datetime(2026, 11, 14, 12), end=datetime(2026, 11, 14, 14))
    args = VectorSearchArgs(user_query="keys", keywords=["rex", "aino"], timespan=window)

    # The vector table and the keyword search filter times apart, so check both.
    keyword_ids, _ = db(lambda session: _keyword_ranking(session, args, 10, None))

    assert {result.id for result in search(args)} == {memories[1], memories[2]}
    assert set(keyword_ids) == {memories[1], memories[2]}


def test_the_model_reads_and_writes_local_time(db, memories):
    gemma = FakeGemma(
        {"content": None, "tool_calls": [tool_call("a", user_query="keys", limit=1)]},
        {"content": "On the table, at 11:00."},
    )

    async def ask(session):
        steps = agent.run(session, "Where are my keys?", gemma, fake_embed, None, now=NOON)
        return [step async for step in steps]

    db(ask)
    system = gemma.calls[0][0][0]["content"]
    tool_message, images = gemma.calls[1][0][-2:]
    assert "The current time is 2026-11-14T14:00:00+02:00" in system  # noon UTC
    assert json.loads(tool_message["content"])[0]["seen_at"] == "2026-11-14T11:00:00+02:00"
    assert images["content"][1]["text"].endswith("seen at 2026-11-14T11:00:00+02:00:")


def test_add_memory_rejects_bad_input(db):
    async def bad(session):
        with pytest.raises(ValueError, match="timezone-aware"):
            await add_memory(session, "image", unit(0), datetime(2026, 11, 14))
        with pytest.raises(ValueError, match="kind"):
            await add_memory(session, "video", unit(0))  # type: ignore[arg-type]

    db(bad)


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

    async def __call__(self, messages, tools):
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
    assert tools == [VECTOR_SEARCH_TOOL, agent.START_WATCHING_TOOL]
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


def asking(client, gemma, path="/api/ask", headers=None, **body) -> list[dict]:
    """Ask through `path` with the fake models; the steps, from /api/ask or /api/ask/stream."""
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: gemma
    try:
        reply = client.post(
            path, json={"question": "Where are my keys?", **body}, headers=headers or {}
        )
    finally:
        app.dependency_overrides.clear()
    assert reply.status_code == 200, reply.text
    if path == "/api/ask/stream":
        return [json.loads(line) for line in reply.text.splitlines()]
    return reply.json()["steps"]


@pytest.mark.parametrize(
    ("said", "text", "cited"),
    [
        # The usual: the best one at the end.
        ("On the table, at 09:00. [Memory {keys}]", "On the table, at 09:00.", ["keys"]),
        # Any case and spacing, several, mid-sentence: in the order cited, no space left over.
        (
            "Rex [memory{rex}] sniffed the keys [ MEMORY {keys} ].",
            "Rex sniffed the keys.",
            ["rex", "keys"],
        ),
        # A memory the searches did not return (the dog), or none at all: no evidence.
        ("Rex was there. [Memory {dog}] [Memory 999]", "Rex was there.", []),
        ("On the table, at 09:00.", "On the table, at 09:00.", []),
    ],
)
def test_an_answer_cites_only_memories_its_searches_returned(client, memories, said, text, cited):
    ids = {"keys": memories[0], "dog": memories[1], "rex": memories[3]}
    gemma = FakeGemma(
        # Returns the keys and Rex sniffing them, not the dog.
        {"content": None, "tool_calls": [tool_call("a", user_query="keys", limit=2)]},
        {"content": said.format(**ids)},
    )
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: gemma
    try:
        body = client.post("/api/ask", json={"question": "Where are my keys?"}).json()
    finally:
        app.dependency_overrides.clear()

    assert [r["id"] for r in body["steps"][2]["results"]] == [ids["keys"], ids["rex"]]
    answer = body["steps"][-1]
    assert body["answer"] == answer["content"] == text
    assert [m["id"] for m in body["evidence"]] == [ids[name] for name in cited]
    assert answer["evidence"] == body["evidence"]


def demo_clip(
    db, title: str, times: list[float], end: datetime | None = None, published: bool = True
) -> tuple[int, list[int]]:
    """A demo clip with its frames as app/demo.py stores them: one per time into the clip, the
    last one at `end` (now when not given), stills captioned with the title, all pointing like
    "keys". Its id and the frames' ids, in order."""

    async def add(session):
        clip = Showcase(title=title, published=published)
        session.add(clip)
        await session.flush()
        last = end or datetime.now(UTC)
        ids = []
        for at in times:
            memory = await add_memory(
                session,
                "image",
                unit(0),
                last - timedelta(seconds=times[-1] - at),
                title,
                media.save_image(jpeg()),
                collection="demo",
                owner_id=DEMO_OWNER,
            )
            memory.source = f"{demo.clip_prefix(clip.id)}abc"
            session.add(memory)
            ids.append(memory.id)
        await session.commit()
        return clip.id, ids

    return db(add)


def own_memories(db) -> tuple[int, int]:
    """The admin's own note and camera still, both matching "keys": its glasses and manual."""

    async def add(session):
        still = await add_memory(
            session, "image", unit(0), NOON, "keys", media.save_image(jpeg()), "glasses", 1
        )
        note = await add_memory(session, "text", unit(0), NOON, "keys on the hook", owner_id=1)
        await session.commit()
        return still.id, note.id

    return db(add)


def searched(steps: list[dict]) -> list[int]:
    """The ids the (first) search returned, in its order."""
    return [r["id"] for r in next(s for s in steps if s["type"] == "tool_result")["results"]]


def test_a_search_limited_to_some_memories_finds_only_those(client, db, memories):
    _keys, dog, coffee, _rex = memories

    async def decoys(session):
        # Nearer "keys" than the dog, and holding both keywords where it holds one.
        for _ in range(10):
            await add_memory(session, "text", unit(0), NOON, "keys and Rex")
        await session.commit()

    db(decoys)
    # With limit 2 each ranking takes 8 candidates, all of them decoys when nothing is left out
    # first: restricting them afterwards would leave nothing. No keywords in the vector search,
    # so only its vector ranking can find the dog. One allowed id as well as several: SQLite
    # writes a list of one differently (app/vectors.py: nearest).
    near = VectorSearchArgs(user_query="keys", limit=2)
    worded = VectorSearchArgs(user_query="keys", keywords=["keys", "rex"], limit=2)
    candidates = worded.limit * CANDIDATES_PER_LIST

    def found(ids: set[int]) -> set[int]:
        hits = db(lambda session: vector_search(session, near, fake_embed, None, ids))
        return {hit.id for hit in hits}

    by_keyword, _ = db(
        lambda session: _keyword_ranking(session, worded, candidates, None, {dog, coffee})
    )

    assert found({dog}) == {dog}
    assert found({dog, coffee}) == {dog, coffee}
    assert by_keyword == [dog]


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask/stream"])
def test_a_question_on_a_playing_clip_searches_only_that_clip_so_far(client, db, path):
    walk, (start, middle, _later) = demo_clip(db, "Walk", [0, 3, 6])
    demo_clip(db, "Kitchen", [0, 6])
    own_memories(db)  # the asker's, the admin's
    # Both the query and the keywords match every memory there is.
    search = tool_call("a", user_query="keys", keywords=["walk", "kitchen", "keys"], limit=20)
    gemma = FakeGemma({"content": None, "tool_calls": [search]}, {"content": "Not yet."})

    steps = asking(client, gemma, path, demo_clip_id=walk, demo_at_s=3)

    assert sorted(searched(steps)) == [start, middle]
    messages, _tools = gemma.calls[1]
    told, images = messages[-2], messages[-1]
    assert sorted(m["id"] for m in json.loads(told["content"])) == [start, middle]
    labels = [part["text"] for part in images["content"] if part["type"] == "text"]
    assert sorted(int(label.split()[1].rstrip(",")) for label in labels[1:]) == [start, middle]


def test_a_question_on_a_playing_clip_is_asked_at_the_clips_time(client, db):
    walk, _frames = demo_clip(db, "Walk", [0, 3, 6], end=NOON)  # it starts 6 s before noon
    gemma = FakeGemma({"content": "Not yet."})

    asking(client, gemma, demo_clip_id=walk, demo_at_s=4.5)

    system = gemma.calls[0][0][0]["content"]
    assert f"The current time is {local(NOON - timedelta(seconds=1.5))}," in system


def test_pinning_the_glasses_collection_keeps_the_rest_out_of_both_rankings(client, db):
    demo_clip(db, "Walk to the keys", [0, 3])
    still, _note = own_memories(db)
    search = tool_call("a", user_query="keys", keywords=["keys", "walk"], limit=20)
    gemma = FakeGemma({"content": None, "tool_calls": [search]}, {"content": "Here."})

    steps = asking(client, gemma, collections=["glasses"])

    assert searched(steps) == [still]


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask/stream"])
def test_a_question_on_a_clip_needs_it_published_and_stored(client, db, path):
    stored, _ = demo_clip(db, "Walk", [0, 3])
    draft, _ = demo_clip(db, "Draft", [0, 3], published=False)
    empty, _ = demo_clip(db, "Not stored yet", [])
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: FakeGemma({"content": "Not yet."})
    try:
        statuses = {
            name: client.post(
                path, json={"question": "What next?", "demo_clip_id": clip, "demo_at_s": 1}
            ).status_code
            for name, clip in (("stored", stored), ("draft", draft), ("gone", 99), ("empty", empty))
        }
    finally:
        app.dependency_overrides.clear()

    assert statuses == {"stored": 200, "draft": 404, "gone": 404, "empty": 409}


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask/stream"])
@pytest.mark.parametrize(
    "view",
    [
        {"demo_clip_id": 1},
        {"demo_at_s": 3},
        {"demo_clip_id": 1, "demo_at_s": -1},
        {"demo_clip_id": 1, "demo_at_s": 3, "collections": ["glasses"]},
    ],
)
def test_a_demo_view_is_a_clip_and_a_time_into_it_alone(client, path, view):
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: FakeGemma({"content": "Unreachable."})
    try:
        reply = client.post(path, json={"question": "What happens next?", **view})
    finally:
        app.dependency_overrides.clear()

    assert reply.status_code == 422


def test_ask_stream_reports_a_failing_model_server(client, memories):
    async def broken(messages, tools):
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
    # Versioned by file name, so it can be cached for good; a bare id is asked about every time,
    # since a wiped database hands the same id to a different image.
    assert "immutable" in image.headers["cache-control"]
    bare = client.get(f"/api/memories/{memory['id']}/image")
    assert bare.headers["cache-control"] == "private, no-cache"

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
    async def broken(texts, input_type="query"):
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


def test_a_note_is_embedded_as_a_document_and_a_search_as_a_query(client):
    calls = []

    async def recording(texts, input_type="query"):
        calls.append((list(texts), input_type))
        return await fake_embed(texts)

    app.dependency_overrides[get_embedder] = lambda: recording
    try:
        client.post("/api/memories/text", json={"text": "The keys are in the hall drawer."})
        client.post("/api/search", json={"user_query": "keys"})
    finally:
        app.dependency_overrides.clear()

    assert calls == [(["The keys are in the hall drawer."], "document"), (["keys"], "query")]


def test_a_model_of_another_width_is_reported(client, memories):
    async def too_narrow(texts, input_type="query"):
        return [[0.1] * 512 for _ in texts]

    app.dependency_overrides[get_embedder] = lambda: too_narrow
    try:
        search = client.post("/api/search", json={"user_query": "keys"})
        note = client.post("/api/memories/text", json={"text": "coffee"})
    finally:
        app.dependency_overrides.clear()

    for response in (search, note):
        assert response.status_code == 502
        assert (
            f"gives 512 numbers per input, but {TABLE} holds {WIDTH}" in response.json()["detail"]
        )


def test_collections_group_memories_and_filter_searches(client, db, search):
    async def add(session):
        glasses = await add_memory(session, "image", unit(0), NOON, "keys", collection=" Glasses ")
        manual = await add_memory(session, "image", unit(0), NOON, "keys")
        await session.commit()
        assert glasses.collection == "glasses" and manual.collection == "manual"
        with pytest.raises(ValueError, match="Collection"):
            await add_memory(session, "text", unit(2), NOON, "x", collection="not ok!")
        return glasses.id, manual.id

    glasses_id, manual_id = db(add)
    only_glasses = search(
        VectorSearchArgs(user_query="keys", keywords=["keys"], collections=["GLASSES"])
    )
    nowhere = search(VectorSearchArgs(user_query="keys", collections=["no such"]))

    assert [result.id for result in only_glasses] == [glasses_id]
    assert only_glasses[0].collection == "glasses"
    assert nowhere == []

    assert client.get("/api/memories/collections").json() == [
        {"name": "glasses", "count": 1},
        {"name": "manual", "count": 1},
    ]
    listed = client.get("/api/memories", params={"collection": "manual"}).json()
    assert [m["id"] for m in listed] == [manual_id]


NEAR_KEYS = [1.0, 0.05] + [0.0] * (WIDTH - 2)  # about 0.001 from unit(0), the "keys" axis


def test_a_near_copy_from_one_glance_waits_behind_distinct_results(client, db, search):
    """A still a second after a better one that looks the same shows nothing new, so its slot
    goes to the next memory. The same scene hours earlier, in a note or in another collection
    is a memory of its own: the latest sighting of something must never be dropped."""
    second, morning_at = timedelta(seconds=1), NOON - timedelta(hours=6)

    async def add(session):
        rows = [
            await add_memory(session, "image", unit(0), NOON, collection="glasses"),
            await add_memory(session, "image", NEAR_KEYS, NOON + second, collection="glasses"),
            await add_memory(session, "image", NEAR_KEYS, morning_at, collection="glasses"),
            await add_memory(session, "text", unit(0), NOON + second, "keys", collection="glasses"),
            await add_memory(session, "image", unit(0), NOON, collection="manual"),
            await add_memory(session, "image", unit(0, 1), NOON, collection="glasses"),
        ]
        await session.commit()
        return [row.id for row in rows]

    first, copy, morning, note, manual, other = db(add)

    found = [hit.id for hit in search(VectorSearchArgs(user_query="keys", limit=5))]

    assert copy not in found
    assert sorted(found) == sorted([first, morning, note, manual, other])


def test_near_copies_still_fill_the_slots_left(client, db, search):
    async def add(session):
        rows = [
            await add_memory(session, "image", unit(0), NOON, collection="glasses"),
            await add_memory(
                session, "image", NEAR_KEYS, NOON + timedelta(seconds=1), collection="glasses"
            ),
        ]
        await session.commit()
        return [row.id for row in rows]

    first, copy = db(add)

    assert [hit.id for hit in search(VectorSearchArgs(user_query="keys", limit=3))] == [first, copy]


def test_uploads_take_a_collection_and_reject_a_bad_one(client):
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    try:
        image = client.post(
            "/api/memories/image",
            files={"file": ("a.png", jpeg(), "image/png")},
            data={"collection": "glasses"},
        )
        note = client.post("/api/memories/text", json={"text": "coffee"})
        bad = client.post("/api/memories/text", json={"text": "coffee", "collection": "a b"})
    finally:
        app.dependency_overrides.clear()

    assert image.status_code == 201 and image.json()["collection"] == "glasses"
    assert note.status_code == 201 and note.json()["collection"] == "manual"
    assert bad.status_code == 422 and "Collection" in bad.json()["detail"]


def test_vectors_endpoint_sends_the_stored_embeddings(client, memories):
    import base64
    import struct

    body = client.get("/api/memories/vectors").json()
    texts = client.get("/api/memories/vectors", params={"collection": "nothing"}).json()

    assert [m["id"] for m in body] == list(reversed(memories))  # newest first
    vector = struct.unpack(f"<{WIDTH}f", base64.b64decode(body[-1]["embedding"]))
    assert list(vector) == pytest.approx(unit(0))
    assert texts == []


def test_vectors_endpoint_sends_only_memories_stored_after_one(client, db, memories):
    async def add(session):
        # Stored after all the others, though seen before any of them.
        memory = await add_memory(session, "text", unit(2), NOON - timedelta(days=1), "Late")
        await session.commit()
        return memory.id

    late = db(add)

    body = client.get("/api/memories/vectors", params={"after_id": memories[1]}).json()

    assert [m["id"] for m in body] == [memories[3], memories[2], late]  # newest seen first


def test_ask_can_pin_the_collections_searched(client, memories):
    gemma = FakeGemma(
        {"content": None, "tool_calls": [tool_call("a", user_query="keys")]},
        {"content": "Nothing from the glasses."},
    )
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[get_chat] = lambda: gemma
    try:
        response = client.post(
            "/api/ask", json={"question": "Where are my keys?", "collections": ["glasses"]}
        )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert body["sources"] == []  # every memory in the fixture is "manual"
    tool_step = next(step for step in body["steps"] if step["type"] == "tool_result")
    assert tool_step["results"] == []
