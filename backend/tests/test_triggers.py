import json

import pytest

from app.glasses import triggers
from app.main import app
from app.routes.glasses import optional_chat


def answering(content):
    """A fake chat model that answers `content`, and keeps the messages it was sent."""
    seen = []

    async def chat(messages, tools):
        seen.append(messages)
        return {"role": "assistant", "content": content}

    chat.seen = seen
    return chat


@pytest.fixture
def no_triggers(client):
    for trigger in client.get("/api/glasses/triggers").json():  # the seeded example
        client.delete(f"/api/glasses/triggers/{trigger['id']}")
    yield
    app.dependency_overrides.clear()


def generate(client, goal="Tell me when my bus comes", **kwargs):
    return client.post("/api/glasses/triggers/generate", json={"goal": goal}, **kwargs)


def test_gemma_writes_triggers_from_a_goal_and_they_are_saved(client, no_triggers):
    chat = answering(
        "```json\n"
        + json.dumps(
            {
                "triggers": [
                    {"name": "Bus", "watch_for": "a city bus"},
                    {"name": "Bus stop", "watch_for": "a bus stop sign"},
                ]
            }
        )
        + "\n```"
    )
    app.dependency_overrides[optional_chat] = lambda: chat

    made = generate(client)

    assert made.status_code == 201, made.text
    assert [(t["name"], t["watch_for"]) for t in made.json()] == [
        ("Bus", "a city bus"),
        ("Bus stop", "a bus stop sign"),
    ]
    assert all(t["enabled"] and t["sigmas"] == 3 and t["level"] is None for t in made.json())
    assert all("Tell me when my bus comes" in t["instruction"] for t in made.json())
    assert client.get("/api/glasses/triggers").json() == made.json()
    system, user = chat.seen[0]
    assert "Cast wide, then filter" in system["content"]  # the skill text is in the prompt
    assert user == {"role": "user", "content": "Tell me when my bus comes"}


def test_unusable_triggers_are_left_out(client, no_triggers):
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

    made = generate(client).json()

    assert made[0]["name"] == "a city bus"
    assert [t["watch_for"] for t in made[1:3]] == ["thing 0", "thing 1"]
    assert len(made) == triggers.MOST


@pytest.mark.parametrize("content", ["I cannot help with that.", '{"triggers": []}', "{", None])
def test_an_answer_without_triggers_is_a_502_and_saves_nothing(client, no_triggers, content):
    app.dependency_overrides[optional_chat] = lambda: answering(content)

    assert generate(client).status_code == 502
    assert client.get("/api/glasses/triggers").json() == []


def test_generating_needs_a_chat_model_and_a_goal(client, no_triggers):
    app.dependency_overrides[optional_chat] = lambda: None
    assert generate(client).status_code == 503

    app.dependency_overrides[optional_chat] = lambda: answering("{}")
    assert generate(client, goal="").status_code == 422


def test_generated_triggers_belong_to_whoever_asked(client, guest, no_triggers):
    aino, ville = guest("aino"), guest("ville")
    app.dependency_overrides[optional_chat] = lambda: answering(
        '{"triggers": [{"name": "Bus", "watch_for": "a city bus"}]}'
    )

    assert generate(client, headers=aino).status_code == 201

    assert len(client.get("/api/glasses/triggers", headers=aino).json()) == 1
    assert client.get("/api/glasses/triggers", headers=ville).json() == []
