"""Clients for the OpenAI-style APIs we host ourselves: chat (Gemma on vLLM, Verda) and embeddings.

Routes get them through the get_chat and get_embedder dependencies, so tests can swap in fakes
with app.dependency_overrides.
"""

from collections.abc import Callable, Sequence
from typing import Any

import httpx
from fastapi import HTTPException

from app.settings import get_settings

# messages and tools in the OpenAI format -> the assistant message (content and/or tool_calls)
Chat = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], dict[str, Any]]
# texts -> one vector per text, in order. An image goes in as a data:image/...;base64 URL.
Embedder = Callable[[Sequence[str]], list[list[float]]]

TIMEOUT = httpx.Timeout(120, connect=10)


class UpstreamError(Exception):
    """A model server failed or could not be reached; the message says which and why."""


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _post(what: str, url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as error:
        raise UpstreamError(f"Could not reach the {what} at {url}: {error}") from error
    if response.is_error:
        raise UpstreamError(f"The {what} answered {response.status_code}: {response.text[:500]}")
    return response.json()


def get_chat() -> Chat:
    settings = get_settings()
    if not settings.llm_url:
        raise HTTPException(503, "No chat model configured: set LLM_URL (and LLM_API_KEY)")
    url, model = settings.llm_url.rstrip("/") + "/v1/chat/completions", settings.llm_model
    headers = _headers(settings.llm_api_key)

    def chat(messages, tools):
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 800,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        return _post("chat model", url, body, headers)["choices"][0]["message"]

    return chat


def get_embedder() -> Embedder:
    settings = get_settings()
    if not settings.embed_url:
        raise HTTPException(503, "No embedding model configured: set EMBED_URL")
    url, model = settings.embed_url.rstrip("/") + "/v1/embeddings", settings.embed_model
    headers = _headers(settings.embed_api_key)

    def embed(texts):
        body = {"model": model, "input": list(texts)}
        rows = sorted(
            _post("embedding model", url, body, headers)["data"], key=lambda r: r["index"]
        )
        return [row["embedding"] for row in rows]

    return embed
