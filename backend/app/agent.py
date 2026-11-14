"""Answer a question with the chat model, which searches the memories with vector_search first.

The loop: the model gets the question and the tool, calls vector_search as often as it likes (up
to MAX_ROUNDS rounds), sees each result list as text and the images that came back as pictures,
then answers. On the last round the tool is withheld, so it has to answer.

run() yields a Step for everything that happens, so the page can show the process as it goes;
answer() collects them into one Answer.

The model is also told what the user's glasses are watching out for (their goals,
app/glasses/triggers.py) and gets a second tool, stop_watching, to remove one when they ask.

The server must have tool calling on: for vLLM, `--enable-auto-tool-choice` and the tool call
parser that matches the model (memory/docs/backend-requirements.md).
"""

import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

import anyio
from pydantic import BaseModel, Field, ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from app import media
from app.glasses import feeds, triggers
from app.llm import Chat, Embedder
from app.models import Goal, Memory
from app.search import (
    LOCAL_TIMEZONE,
    VECTOR_SEARCH_TOOL,
    SearchResult,
    VectorSearchArgs,
    for_model,
    local,
    vector_search,
)

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

The current time is {now}, the user's local time ({zone}). Every time you read or write is \
local time: write times in tool calls as ISO 8601 with the offset, like the current \
time.{watching}"""

WATCHING = """

The user's glasses are also watching out for these, because the user asked for them:
{goals}
When the user tells you to stop watching for one of them, or that they no longer need it, do not \
search: call stop_watching with that goal's number, then confirm in one short sentence. Never \
stop one they did not ask you to stop."""

CAN_WATCH = """

The user's glasses can watch out for things. When the user asks you to watch out for something, \
or to warn, alert or tell them when something comes into view, do not search: call \
start_watching with what they asked, in their own words, then confirm in one short sentence."""

START_WATCHING_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "start_watching",
        "description": (
            "Have the user's glasses watch out for something from now on: triggers are written "
            "for it and checked against every frame of the camera stream. Call it only when the "
            "user asks you to watch out for, warn about or tell them about something."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What the user asked for, in their words, e.g. 'I'm "
                    "allergic to nuts, warn me when I pick up food that might have them'.",
                }
            },
            "required": ["goal"],
        },
    },
}

STOP_WATCHING_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "stop_watching",
        "description": (
            "Stop the user's glasses watching out for one of the things the user asked for: the "
            "goal is removed with all its triggers. Call it only when the user asks you to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "The goal's number, as listed."}
            },
            "required": ["goal_id"],
        },
    },
}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Search only these collections, whatever the model asks for. Empty: the model chooses.
    collections: list[str] = Field(default_factory=list, max_length=10)


def _pinned(args: VectorSearchArgs, collections: list[str] | None) -> VectorSearchArgs:
    return args.model_copy(update={"collections": list(collections)}) if collections else args


class Step(BaseModel):
    """One thing that happened while answering, in order."""

    type: Literal[
        "model_call", "model_text", "tool_call", "tool_result", "tool_error", "answer", "error"
    ]
    round: int  # which model call it belongs to, from 1
    elapsed_ms: int  # since the question arrived
    # model_text, answer: what the model wrote; *error: what failed; tool_result of
    # stop_watching: what was removed
    content: str | None = None
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


async def _own_goal(session: AsyncSession, arguments: Any, owner_id: int | None) -> Goal | None:
    """The goal a stop_watching call names, when it is the asking account's own."""
    try:
        goal_id = int(arguments["goal_id"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not 0 < goal_id < 2**63:  # what the database can hold
        return None
    goal = await session.get(Goal, goal_id)
    return goal if goal is not None and goal.owner_id == owner_id else None


def error_line(message: str) -> str:
    """The last line of a stream that failed: a model server is down, for example."""
    return Step(type="error", round=0, elapsed_ms=0, content=message).model_dump_json() + "\n"


def _log_step(owner_id: int, step: Step) -> None:
    """The steps worth seeing on the admin's stream wall (app/glasses/feeds.py): the searches,
    what they found, the answer and what failed."""
    if step.type == "tool_call":
        args = step.arguments if isinstance(step.arguments, dict) else {}
        what = args.get("user_query") or args.get("text") or args.get("goal_id") or ""
        detail = {"name": step.name, "arguments": step.arguments, "round": step.round}
        feeds.note(owner_id, "search", f"{step.name}({what})", "chat", detail, step.elapsed_ms)
    elif step.type == "tool_result":
        found = [
            {"id": r.id, "kind": r.kind, "text": r.text[:120], "score": round(r.score, 4)}
            for r in step.results or []
        ]
        text = (
            f"{len(found)} result{'' if len(found) == 1 else 's'}"
            if step.results is not None
            else (step.content or "")
        )
        feeds.note(owner_id, "search", f"→ {text}", "chat", {"results": found}, step.elapsed_ms)
    elif step.type == "answer":
        feeds.note(
            owner_id, "answer", step.content or "(empty answer)", "chat", None, step.elapsed_ms
        )
    elif step.type in ("error", "tool_error"):
        feeds.note(owner_id, "error", step.content or step.type, "chat", None, step.elapsed_ms)


async def run(
    session: AsyncSession,
    question: str,
    chat: Chat,
    embed: Embedder,
    owners: list[int] | None,
    now: datetime | None = None,
    collections: list[str] | None = None,
    owner_id: int | None = None,
) -> AsyncIterator[Step]:
    """Answer `question`, yielding each step; the last one is the answer. Each step worth seeing
    also goes to the asker's log on the admin's stream wall."""
    async for step in _run(session, question, chat, embed, owners, now, collections, owner_id):
        if owner_id is not None:
            _log_step(owner_id, step)
        yield step


async def _run(
    session: AsyncSession,
    question: str,
    chat: Chat,
    embed: Embedder,
    owners: list[int] | None,
    now: datetime | None = None,
    collections: list[str] | None = None,
    owner_id: int | None = None,
) -> AsyncIterator[Step]:
    """Answer `question`, yielding each step; the last one is the answer.

    `owners` is whose memories the model may reach (app.auth.Viewer.owners; None for all): every
    vector_search it makes is limited to them, and only their stills are attached. With
    `collections`, every search is limited to them too, whatever the model asks for. `owner_id`
    is the account asking: the model is told that account's goals, may remove one of them, and
    may add one, for which it writes the triggers (app/glasses/triggers.py).

    Raises UpstreamError when a model server fails.
    """
    started = time.monotonic()
    now = now or datetime.now(UTC)
    watched = await triggers.of(session, owner_id) if owner_id is not None else []
    watching = WATCHING.format(goals="\n".join(f"- goal {g.id}: “{g.text}”" for g in watched))
    tools = [VECTOR_SEARCH_TOOL]
    if owner_id is not None:
        tools.append(START_WATCHING_TOOL)
    if watched:
        tools.append(STOP_WATCHING_TOOL)
    about_watching = (CAN_WATCH if owner_id is not None else "") + (watching if watched else "")
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM.format(
                now=local(now), zone=LOCAL_TIMEZONE.key, watching=about_watching
            ),
        },
        {"role": "user", "content": question},
    ]
    shown: set[int] = set()
    stopped: set[str] = set()  # the goals stop_watching removed during this answer
    started_goals: set[str] = set()  # what start_watching added during this answer
    images_pending = 0

    def step(type, round, **fields) -> Step:
        elapsed = int((time.monotonic() - started) * 1000)
        return Step(type=type, round=round, elapsed_ms=elapsed, **fields)

    for round in range(1, MAX_ROUNDS + 1):
        last = round == MAX_ROUNDS
        yield step("model_call", round, tools_offered=not last, images_sent=images_pending)
        message = await chat(messages, None if last else tools)
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
            if name == STOP_WATCHING_TOOL["function"]["name"] and watched:
                goal = await _own_goal(session, arguments, owner_id)
                again = isinstance(arguments, dict) and str(arguments.get("goal_id")) in stopped
                if goal is None and again:
                    # The list in the prompt is from before this answer began.
                    content = json.dumps({"already_stopped": True})
                    yield step("tool_result", round, call_id=call_id, content="Already stopped")
                elif goal is None:
                    error = "No such goal of this user; use a goal number from the list"
                    content = json.dumps({"error": error})
                    yield step("tool_error", round, call_id=call_id, content=error)
                else:
                    stopped.add(str(goal.id))
                    await triggers.remove(session, goal)
                    content = json.dumps({"stopped_watching": goal.text})
                    done = f"Stopped watching: {goal.text}"
                    yield step("tool_result", round, call_id=call_id, content=done)
            elif name == START_WATCHING_TOOL["function"]["name"] and owner_id is not None:
                goal_text = arguments.get("goal") if isinstance(arguments, dict) else None
                goal_text = goal_text.strip()[:500] if isinstance(goal_text, str) else ""
                if goal_text.lower() in started_goals:
                    content = json.dumps({"already_watching": True})
                    yield step("tool_result", round, call_id=call_id, content="Already watching")
                else:
                    try:
                        if not goal_text:
                            raise ValueError("say what to watch out for in `goal`")
                        drafts = await triggers.write(chat, goal_text)
                    except ValueError as error:  # no goal, or no usable trigger was written
                        content = json.dumps({"error": str(error)})
                        yield step("tool_error", round, call_id=call_id, content=str(error))
                    else:
                        await triggers.add(session, owner_id, goal_text, drafts)
                        feeds.note(owner_id, "goal", goal_text, "chat")
                        started_goals.add(goal_text.lower())
                        names = [draft.name for draft in drafts]
                        content = json.dumps({"now_watching": goal_text, "triggers": names})
                        done = f"Now watching: {goal_text} ({', '.join(names)})"
                        yield step("tool_result", round, call_id=call_id, content=done)
            elif name != VECTOR_SEARCH_TOOL["function"]["name"]:
                error = f"No tool named {name}"
                content = json.dumps({"error": error})
                yield step("tool_error", round, call_id=call_id, content=error)
            else:
                try:
                    args = _pinned(VectorSearchArgs.model_validate(arguments), collections)
                except ValidationError as error:  # let the model see what to fix and retry
                    problems = error.errors(include_url=False, include_context=False)
                    content = json.dumps({"error": problems}, default=str)
                    yield step("tool_error", round, call_id=call_id, content=content)
                else:
                    results = await vector_search(session, args, embed, owners)
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
                memory = await session.get(Memory, result.id)
                if memory is None or memory.image_file is None:
                    continue
                if owners is not None and memory.owner_id not in owners:
                    continue  # never happens through vector_search; checked again all the same
                seen = local(result.created_at)
                parts.append({"type": "text", "text": f"Memory {result.id}, seen at {seen}:"})
                url = await anyio.to_thread.run_sync(media.data_url, memory.image_file)
                parts.append({"type": "image_url", "image_url": {"url": url}})
                images_pending += 1
            messages.append({"role": "user", "content": parts})


async def answer(
    session: AsyncSession,
    question: str,
    chat: Chat,
    embed: Embedder,
    owners: list[int] | None,
    now: datetime | None = None,
    collections: list[str] | None = None,
    owner_id: int | None = None,
) -> Answer:
    steps = [
        s async for s in run(session, question, chat, embed, owners, now, collections, owner_id)
    ]
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
