# Tests for the example Note feature: delete together with it.


def test_created_note_is_listed(client):
    created = client.post("/api/notes", json={"text": "hello"})

    assert created.status_code == 201
    assert created.json()["text"] == "hello"
    assert "hello" in [note["text"] for note in client.get("/api/notes").json()]


def test_empty_note_is_rejected(client):
    assert client.post("/api/notes", json={"text": ""}).status_code == 422
