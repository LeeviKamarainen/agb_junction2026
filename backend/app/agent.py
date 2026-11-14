"""Answer a question with the chat model, which searches the memories with vector_search first.

The loop: the model gets the question and the tool, calls vector_search as often as it likes (up
to MAX_ROUNDS rounds), sees each result list as text and the images that came back as pictures,
then answers. On the last round the tool is withheld, so it has to answer.

run() yields a Step for everything that happens, so the page can show the process as it goes;
answer() collects them into one Answer.

The server must have tool calling on: for vLLM, `--enable-auto-tool-choice` and the tool call
parser that matches the model (memory/docs/backend-requirements.md).
"""

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

from app import media
from app.llm import Chat, Embedder
from app.models import Memory
from app.search import VECTOR_SEARCH_TOOL, SearchResult, VectorSearchArgs, for_model, vector_search

MAX_ROUNDS = 4  # model calls per question, the last one without the tool
MAX_IMAGES = 16  # stills shown to the model per question; each costs a few hundred tokens

SYSTEM = """You answer questions about what the user has seen, done and been told, from the \
memories their smart glasses recorded: stills from the camera and text notes, each with the time \
it was recorded.

Always call vector_search before answering. Describe what would be in view in user_query, give \
other wordings in alternative_queries, put names and exact words in keywords, and set timespan \
when the question names a time ("this morning", "yesterday"). If the results do not answer the \
question, search again with other wording or a wider timespan.

Answer from the memories only, briefly, and say when you saw it. If nothing relevant was found, \
say so instead of guessing.

The current time is {now} (UTC). Write times in tool calls as ISO 8601 in UTC."""


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class Step(BaseModel):
    """One thing that happened while answering, in order."""

    type: Literal[
        "model_call", "model_text", "tool_call", "tool_result", "tool_error", "answer", "error"
    ]
    round: int  # which model call it belongs to, from 1
    elapsed_ms: int  # since the question arrived
    content: str | None = None  # model_text, answer: what the model wrote; *error: what failed
    tools_offered: bool | None = None  # model_call: false on the last round, which must answer
    images_sent: int | None = None  # model_call: stills attached to this call
    call_id: str | None = None  # tool_*: which call it belongs to
    name: str | None = None  # tool_call: the tool the model called
    arguments: Any = None  # tool_call: the arguments as the model sent them
    results: list[SearchResult] | None = None  # tool_result


class Answer(BaseModel):
    answer: str
    searches: list[VectorSearchArgs]  # every vector_search call the model made, in order
    sources: list[SearchResult]  # every memory those calls returned, first seen first
    steps: list[Step]


def _raw_arguments(call: dict[str, Any]) -> Any:
    raw = call["function"].get("arguments") or "{}"
    if isinstance(raw, str):  # usually a JSON string; some servers send the object itself
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def error_line(message: str) -> str:
    """The last line of a stream that failed: a model server is down, for example."""
    return Step(type="error", round=0, elapsed_ms=0, content=message).model_dump_json() + "\n"


def run(
    session: Session, question: str, chat: Chat, embed: Embedder, now: datetime | None = None
) -> Iterator[Step]:
    """Answer `question`, yielding each step; the last one is the answer.

    Raises UpstreamError when a model server fails.
    """
    started = time.monotonic()
    now = now or datetime.now(UTC)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM.format(now=now.isoformat(timespec="seconds"))},
        {"role": "user", "content": question},
    ]
    shown: set[int] = set()
    images_pending = 0

    def step(type, round, **fields) -> Step:
        elapsed = int((time.monotonic() - started) * 1000)
        return Step(type=type, round=round, elapsed_ms=elapsed, **fields)

    for round in range(1, MAX_ROUNDS + 1):
        last = round == MAX_ROUNDS
        yield step("model_call", round, tools_offered=not last, images_sent=images_pending)
        message = chat(messages, None if last else [VECTOR_SEARCH_TOOL])
        calls = message.get("tool_calls") or []
        text = (message.get("content") or "").strip()
        if not calls or last:
            yield step("answer", round, content=text)
            return

        if text:
            yield step("model_text", round, content=text)
        messages.append({"role": "assistant", "content": text, "tool_calls": calls})
        images: list[SearchResult] = []
        for call in calls:
            call_id, name = call.get("id", ""), call["function"]["name"]
            arguments = _raw_arguments(call)
            yield step("tool_call", round, call_id=call_id, name=name, arguments=arguments)
            if name != VECTOR_SEARCH_TOOL["function"]["name"]:
                error = f"No tool named {name}"
                content = json.dumps({"error": error})
                yield step("tool_error", round, call_id=call_id, content=error)
            else:
                try:
                    args = VectorSearchArgs.model_validate(arguments)
                except ValidationError as error:  # let the model see what to fix and retry
                    problems = error.errors(include_url=False, include_context=False)
                    content = json.dumps({"error": problems}, default=str)
                    yield step("tool_error", round, call_id=call_id, content=content)
                else:
                    results = vector_search(session, args, embed)
                    yield step("tool_result", round, call_id=call_id, results=results)
                    for result in results:
                        if result.image_url and result.id not in shown:
                            if len(shown) < MAX_IMAGES:
                                shown.add(result.id)
                                images.append(result)
                    content = for_model(results)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

        # Tool messages are text only, so the stills follow as a user message.
        images_pending = 0
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": "The images found:"}]
            for result in images:
                memory = session.get(Memory, result.id)
                if memory is None or memory.image_file is None:
                    continue
                seen = result.created_at.isoformat(timespec="seconds")
                parts.append({"type": "text", "text": f"Memory {result.id}, seen at {seen}:"})
                url = media.data_url(memory.image_file)
                parts.append({"type": "image_url", "image_url": {"url": url}})
                images_pending += 1
            messages.append({"role": "user", "content": parts})


def answer(
    session: Session, question: str, chat: Chat, embed: Embedder, now: datetime | None = None
) -> Answer:
    steps = list(run(session, question, chat, embed, now))
    searches: list[VectorSearchArgs] = []
    sources: dict[int, SearchResult] = {}
    for step in steps:
        if step.type == "tool_call" and step.name == VECTOR_SEARCH_TOOL["function"]["name"]:
            try:
                searches.append(VectorSearchArgs.model_validate(step.arguments))
            except ValidationError:
                pass
        for result in step.results or []:
            sources.setdefault(result.id, result)
    final = (steps[-1].content or "") if steps and steps[-1].type == "answer" else ""
    return Answer(answer=final, searches=searches, sources=list(sources.values()), steps=steps)
