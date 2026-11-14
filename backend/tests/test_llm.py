"""The model clients' requests, with the HTTP call itself replaced."""

from types import SimpleNamespace

import anyio

from app import llm


def test_the_embedder_says_whether_texts_are_queries_or_documents(monkeypatch):
    sent = []

    async def post(what, url, body, headers):
        sent.append((url, body))
        return {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}

    settings = SimpleNamespace(
        embed_url="https://memory.example/", embed_model="m", embed_api_key=None
    )
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "_post", post)
    embed = llm.get_embedder()

    assert anyio.run(embed, ["a", "b"]) == [[1.0], [2.0]]  # in input order, whatever the reply's
    anyio.run(embed, ["a note"], "document")

    assert [body["input_type"] for _, body in sent] == ["query", "document"]
    assert sent[0][0] == "https://memory.example/v1/embeddings"
