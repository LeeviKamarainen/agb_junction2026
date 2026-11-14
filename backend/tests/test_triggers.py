import json

import pytest

from app.glasses import frames, triggers
from app.llm import get_chat, get_embedder
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder
from tests.test_frames import Scene, add_trigger, send, warm_up
from tests.test_search import FakeGemma, fake_embed


def answering(content):
    """A fake chat model that answers `content`, and keeps the messages it was sent."""
    seen = []

    async def chat(messages, tools):
        seen.append(messages)
        return {"role": "assistant", "content": content}

    chat.seen = seen
    return chat


def written(*pairs):
    return json.dumps({"triggers": [{"name": n, "watch_for": w} for n, w in pairs]})


@pytest.fixture(autouse=True)
def overrides():
    yield
    app.dependency_overrides.clear()


def ask_for(client, text="Tell me when my bus comes", **kwargs):
    return client.post("/api/glasses/goals", json={"text": text}, **kwargs)


def test_gemma_writes_a_goals_triggers_and_they_are_saved_together(client):
    chat = answering(
        "```json\n" + written(("Bus", "a city bus"), ("Bus stop", "a bus stop sign")) + "\n```"
    )
    app.dependency_overrides[optional_chat] = lambda: chat

    made = ask_for(client)

    assert made.status_code == 201, made.text
    goal = made.json()
    assert goal["text"] == "Tell me when my bus comes" and goal["enabled"]
    assert [(t["name"], t["watch_for"]) for t in goal["triggers"]] == [
        ("Bus", "a city bus"),
        ("Bus stop", "a bus stop sign"),
    ]
    assert all(
        t["goal_id"] == goal["id"] and t["sigmas"] == 3 and t["level"] is None
        for t in goal["triggers"]
    )
    assert client.get("/api/glasses/goals").json() == [goal]
    assert client.get("/api/glasses/triggers").json() == goal["triggers"]
    system, user = chat.seen[0]
    assert "Cast wide, then filter" in system["content"]  # the skill text is in the prompt
    assert user == {"role": "user", "content": "Tell me when my bus comes"}


def test_unusable_triggers_are_left_out(client):
    chat = answering(
        json.dumps(
            {
                "triggers": [
                    {"watch_for": "a city bus"},  # no name: the caption is the title
                    {"name": "Again", "watch_for": "A City Bus"},
                    {"name": "Empty", "watch_for": " "},
                    {"name": "Long", "watch_for": "x" * 301},
                    "not an object",
                    *({"name": f"T{i}", "watch_for": f"thing {i}"} for i in range(20)),
                ]
            }
        )
    )
    app.dependency_overrides[optional_chat] = lambda: chat

    made = ask_for(client).json()["triggers"]

    assert made[0]["name"] == "a city bus"
    assert [t["watch_for"] for t in made[1:3]] == ["thing 0", "thing 1"]
    assert len(made) == triggers.MOST


@pytest.mark.parametrize("content", ["I cannot help with that.", '{"triggers": []}', "{", None])
def test_an_answer_without_triggers_is_a_502_and_saves_nothing(client, content):
    app.dependency_overrides[optional_chat] = lambda: answering(content)

    assert ask_for(client).status_code == 502
    assert client.get("/api/glasses/goals").json() == []


def test_a_goal_needs_a_chat_model_or_its_triggers_and_a_text(client):
    app.dependency_overrides[optional_chat] = lambda: None
    assert ask_for(client).status_code == 503
    given = client.post(
        "/api/glasses/goals",
        json={"text": "Keys", "triggers": [{"name": "Keys", "watch_for": "a set of keys"}]},
    )
    assert given.status_code == 201  # Gemma is not needed when the triggers are given

    app.dependency_overrides[optional_chat] = lambda: answering("{}")
    assert ask_for(client, text="").status_code == 422
    assert client.post("/api/glasses/goals", json={"text": "x", "triggers": []}).status_code == 422


def test_a_goal_is_switched_off_and_removed_with_all_its_triggers(client):
    app.dependency_overrides[optional_chat] = lambda: answering(
        written(("Bus", "a city bus"), ("Bus stop", "a bus stop sign"))
    )
    bus = ask_for(client).json()
    keys = add_trigger(client)

    off = client.put(f"/api/glasses/goals/{bus['id']}", json={"enabled": False})
    assert off.status_code == 200 and not off.json()["enabled"] and len(off.json()["triggers"]) == 2

    assert client.delete(f"/api/glasses/goals/{bus['id']}").status_code == 204
    assert [g["text"] for g in client.get("/api/glasses/goals").json()] == [
        "Say where the keys are."
    ]
    assert client.get("/api/glasses/triggers").json() == [keys]
    assert client.delete(f"/api/glasses/goals/{bus['id']}").status_code == 404
    assert client.put("/api/glasses/goals/999", json={"enabled": True}).status_code == 404


def test_dropping_a_goals_last_trigger_removes_the_goal(client):
    app.dependency_overrides[optional_chat] = lambda: answering(
        written(("Bus", "a city bus"), ("Bus stop", "a bus stop sign"))
    )
    first, second = ask_for(client).json()["triggers"]

    assert client.delete(f"/api/glasses/triggers/{first['id']}").status_code == 204
    assert len(client.get("/api/glasses/goals").json()[0]["triggers"]) == 1
    assert client.delete(f"/api/glasses/triggers/{second['id']}").status_code == 204
    assert client.get("/api/glasses/goals").json() == []


@pytest.fixture
def scene():
    frames.reset_streams()
    fake = Scene()
    app.dependency_overrides[optional_embedder] = lambda: fake
    app.dependency_overrides[optional_chat] = lambda: fake.chat
    return fake


def test_any_trigger_of_a_goal_fires_for_the_goal(client, scene):
    seen = []

    async def chat(messages, tools):
        seen.append(messages[-1]["content"][0]["text"])
        return {"role": "assistant", "content": '{"say": "Your keys are here."}'}

    app.dependency_overrides[optional_chat] = lambda: chat
    goal = client.post(
        "/api/glasses/goals",
        json={
            "text": "Remind me to take my keys",
            "triggers": [
                {"name": "Door", "watch_for": "a dog"},  # stands in for a second look
                {"name": "Keys", "watch_for": "a set of keys"},
            ],
        },
    ).json()
    warm_up(client, scene)

    body = send(client, "stream", "s1").json()

    assert body["message"]["title"] == "Keys" and body["message"]["goal_id"] == goal["id"]
    assert {m["goal_id"] for m in body["matches"]} == {goal["id"]}
    assert "Remind me to take my keys" in seen[0] and "a set of keys" in seen[0]


def test_a_goal_that_is_off_is_not_checked(client, scene):
    add_trigger(client, enabled=False)
    warm_up(client, scene)

    body = send(client, "stream", "s1").json()

    assert body["message"] is None and body["matches"] == []


def test_goals_live_in_one_account_only_the_admin_included(client, guest, scene):
    aino, ville = guest("aino"), guest("ville")
    hers = add_trigger(client, headers=aino)
    admins = add_trigger(client, name="Admin's")  # `client` is logged in as the admin

    # Nobody sees, edits or removes another account's goals, and the admin sees only its own.
    assert client.get("/api/glasses/goals", headers=ville).json() == []
    assert [g["id"] for g in client.get("/api/glasses/goals").json()] == [admins["goal_id"]]
    assert [t["id"] for t in client.get("/api/glasses/triggers").json()] == [admins["id"]]
    for other in ({"headers": ville}, {}):
        assert client.delete(f"/api/glasses/goals/{hers['goal_id']}", **other).status_code == 404
        assert (
            client.put(
                f"/api/glasses/goals/{hers['goal_id']}", json={"enabled": False}, **other
            ).status_code
            == 404
        )
        assert client.delete(f"/api/glasses/triggers/{hers['id']}", **other).status_code == 404

    # And a stream is checked against its own account's triggers only.
    warm_up(client, scene, "s1", headers=ville)
    assert send(client, "stream", "s1", headers=ville).json()["matches"] == []
    warm_up(client, scene, "s1")
    mine = send(client, "stream", "s1").json()
    assert [m["trigger_id"] for m in mine["matches"]] == [admins["id"]]
    assert mine["message"]["trigger_id"] == admins["id"]


# --- Talking to Gemma: it knows the goals of whoever asks and can remove one ---


def stop_call(goal_id, id="a"):
    return {
        "id": id,
        "type": "function",
        "function": {"name": "stop_watching", "arguments": json.dumps({"goal_id": goal_id})},
    }


def talking(client, gemma):
    app.dependency_overrides[get_chat] = lambda: gemma
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[optional_chat] = lambda: gemma
    app.dependency_overrides[optional_embedder] = lambda: fake_embed


def test_gemma_removes_a_goal_when_its_owner_asks_in_the_chat(client):
    bus, keys = add_trigger(client, instruction="Tell me when my bus comes"), add_trigger(client)
    gemma = FakeGemma(
        {"content": None, "tool_calls": [stop_call(bus["goal_id"])]},
        {"content": "I've stopped watching for your bus."},
    )
    talking(client, gemma)

    body = client.post("/api/ask", json={"question": "Stop watching for my bus"}).json()

    assert body["answer"] == "I've stopped watching for your bus."
    assert [g["id"] for g in client.get("/api/glasses/goals").json()] == [keys["goal_id"]]
    assert [t["id"] for t in client.get("/api/glasses/triggers").json()] == [keys["id"]]
    # The model was told both goals and was offered the tool; the page is told what went.
    messages, tools = gemma.calls[0]
    assert f"goal {bus['goal_id']}: “Tell me when my bus comes”" in messages[0]["content"]
    assert f"goal {keys['goal_id']}: “Say where the keys are.”" in messages[0]["content"]
    assert [tool["function"]["name"] for tool in tools] == [
        "vector_search",
        "start_watching",
        "stop_watching",
    ]
    result = next(step for step in body["steps"] if step["type"] == "tool_result")
    assert result["content"] == "Stopped watching: Tell me when my bus comes"
    assert json.loads(gemma.calls[1][0][-1]["content"]) == {
        "stopped_watching": "Tell me when my bus comes"
    }


def test_gemma_removes_a_goal_asked_by_voice_on_the_glasses(client):
    bus = add_trigger(client, instruction="Tell me when my bus comes")
    gemma = FakeGemma(
        {"content": None, "tool_calls": [stop_call(bus["goal_id"])]},
        {"content": "Okay, no more bus alerts."},
    )
    talking(client, gemma)

    reply = client.post("/api/glasses/command", json={"question": "stop watching for my bus"})

    assert reply.status_code == 200 and "no more bus alerts" in json.dumps(reply.json())
    assert client.get("/api/glasses/goals").json() == []


def test_gemma_cannot_remove_another_accounts_goal(client, guest):
    aino = guest("aino")
    hers = add_trigger(client, headers=aino)
    mine = add_trigger(client)
    gemma = FakeGemma(
        {"content": None, "tool_calls": [stop_call(hers["goal_id"])]},
        {"content": None, "tool_calls": [stop_call("not a number", "b")]},
        {"content": "I can't find that one."},
    )
    talking(client, gemma)

    body = client.post("/api/ask", json={"question": "Stop watching for aino's keys"}).json()

    assert body["answer"] == "I can't find that one."
    assert [step["type"] for step in body["steps"]].count("tool_error") == 2
    assert len(client.get("/api/glasses/goals", headers=aino).json()) == 1
    assert [g["id"] for g in client.get("/api/glasses/goals").json()] == [mine["goal_id"]]
    assert "aino" not in gemma.calls[0][0][0]["content"]
    assert f"goal {hers['goal_id']}:" not in gemma.calls[0][0][0]["content"]


def test_without_goals_the_chat_cannot_stop_watching(client):
    gemma = FakeGemma({"content": "Nothing to stop."})
    talking(client, gemma)

    client.post("/api/ask", json={"question": "Stop watching for my bus"})

    messages, tools = gemma.calls[0]
    assert [tool["function"]["name"] for tool in tools] == ["vector_search", "start_watching"]
    assert "watching out for" not in messages[0]["content"]


def start_call(goal, id="w"):
    return {
        "id": id,
        "type": "function",
        "function": {"name": "start_watching", "arguments": json.dumps({"goal": goal})},
    }


def test_gemma_starts_watching_when_asked_in_the_chat(client):
    asked = "Tell me when my bus comes"
    gemma = FakeGemma(
        {"content": None, "tool_calls": [start_call(asked), start_call(asked, "again")]},
        {"content": json.dumps({"triggers": [{"name": "Bus", "watch_for": "a city bus"}]})},
        {"content": "I'll tell you when your bus comes."},
    )
    talking(client, gemma)

    body = client.post("/api/ask", json={"question": asked}).json()

    assert body["answer"] == "I'll tell you when your bus comes."
    (goal,) = client.get("/api/glasses/goals").json()  # the second call added nothing
    assert goal["text"] == asked and goal["enabled"]
    assert [t["watch_for"] for t in goal["triggers"]] == ["a city bus"]
    results = [step["content"] for step in body["steps"] if step["type"] == "tool_result"]
    assert results == [f"Now watching: {asked} (Bus)", "Already watching"]
    assert json.loads(gemma.calls[2][0][-2]["content"]) == {
        "now_watching": asked,
        "triggers": ["Bus"],
    }


def test_gemma_is_told_when_no_trigger_could_be_written(client):
    gemma = FakeGemma(
        {"content": None, "tool_calls": [start_call("Tell me when my bus comes")]},
        {"content": "I would rather not."},  # not the triggers asked for
        {"content": "Sorry, I could not set that up."},
    )
    talking(client, gemma)

    body = client.post("/api/ask", json={"question": "Tell me when my bus comes"}).json()

    assert body["answer"] == "Sorry, I could not set that up."
    assert [step["type"] for step in body["steps"]].count("tool_error") == 1
    assert client.get("/api/glasses/goals").json() == []


def test_stopping_the_same_goal_twice_in_one_answer_is_not_an_error(client):
    bus = add_trigger(client, instruction="Tell me when my bus comes")
    gemma = FakeGemma(
        {
            "content": None,
            "tool_calls": [stop_call(bus["goal_id"]), stop_call(bus["goal_id"], "b")],
        },
        {"content": None, "tool_calls": [stop_call(10**30, "c")]},
        {"content": "Done."},
    )
    talking(client, gemma)

    body = client.post("/api/ask", json={"question": "Stop watching for my bus"}).json()

    assert body["answer"] == "Done."
    assert [(s["type"], s["content"]) for s in body["steps"] if s["type"].startswith("tool_")][
        1::2
    ] == [
        ("tool_result", "Stopped watching: Tell me when my bus comes"),
        ("tool_result", "Already stopped"),
        ("tool_error", "No such goal of this user; use a goal number from the list"),
    ]
    assert client.get("/api/glasses/goals").json() == []
