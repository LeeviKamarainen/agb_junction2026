"""Goals and the triggers Gemma writes for them.

The wearer says what they want the glasses to watch out for; that is a Goal. The chat model turns
it into triggers, the things the frames can be matched against. What makes a good trigger was
worked out on recorded footage (memory/notebooks/TRIG-1) and is written down for the model in
trigger_skill.md, which goes into its prompt.

A goal and its triggers belong to one account. They are made, switched off and removed together:
in that account's own panel, or by the chat model when its owner asks it to stop (app/agent.py).
"""

import json
import re
from pathlib import Path

from pydantic import ValidationError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.glasses import frames
from app.llm import Chat
from app.models import Goal, GoalPublic, Trigger, TriggerDraft, TriggerPublic

SKILL = (Path(__file__).parent / "trigger_skill.md").read_text()
# What the wearer says to set up a goal on the glasses: "warn me when…", "remind me to…".
_GOAL = re.compile(r"^(please |can you |could you )?(remind|warn|alert|tell|notify) me\b", re.I)


def is_goal(text: str) -> bool:
    """Whether what the wearer said asks to be warned or reminded: a goal, not a question."""
    return bool(_GOAL.match(text.strip()))


MOST = 10  # triggers kept from one answer
SYSTEM = f"""You set up a person's smart glasses for something they asked you to watch out for. \
You cannot watch the camera yourself: you write triggers, and the glasses wake an assistant with \
the still when the camera sees one.

{SKILL}

Now you only write the first triggers. Their levels are set for you, from each trigger's own \
noise in the camera stream.

Answer with JSON only: {{"triggers": [{{"name": a title of one to three words for the glasses' \
display, "watch_for": the trigger}}, ...]}}."""


async def write(chat: Chat, goal: str) -> list[TriggerDraft]:
    """The triggers for what the wearer asked, not saved yet.

    Raises UpstreamError when the chat model fails, and ValueError when its answer holds no
    trigger that can be used.
    """
    reply = await chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": goal}], None
    )
    text = (reply.get("content") or "") if isinstance(reply, dict) else ""
    try:
        # The JSON, with whatever the model wrapped around it (a code fence, a sentence) cut off.
        written = json.loads(text[text.find("{") : text.rfind("}") + 1])["triggers"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("the chat model did not answer with triggers") from error
    triggers, seen = [], set()
    for item in written if isinstance(written, list) else []:
        watch_for = str(item.get("watch_for") or "").strip() if isinstance(item, dict) else ""
        if not watch_for or watch_for.lower() in seen:
            continue
        seen.add(watch_for.lower())
        try:
            name = (str(item.get("name") or "").strip() or watch_for)[:60]
            triggers.append(TriggerDraft(name=name, watch_for=watch_for))
        except ValidationError:
            continue  # too long a caption, say: leave that one out
    if not triggers:
        raise ValueError("the chat model did not answer with triggers")
    return triggers[:MOST]


async def add(session: AsyncSession, owner_id: int, text: str, drafts: list[TriggerDraft]) -> Goal:
    """Save a goal of `owner_id` with its triggers, enabled."""
    goal = Goal(owner_id=owner_id, text=text.strip())
    session.add(goal)
    await session.flush()
    assert goal.id is not None
    session.add_all(
        Trigger.model_validate(draft, update={"owner_id": owner_id, "goal_id": goal.id})
        for draft in drafts
    )
    await session.commit()
    await session.refresh(goal)
    return goal


async def of(session: AsyncSession, owner_id: int) -> list[GoalPublic]:
    """The account's goals, oldest first, each with its triggers."""
    goals = (
        await session.exec(select(Goal).where(Goal.owner_id == owner_id).order_by(col(Goal.id)))
    ).all()
    triggers = (
        await session.exec(
            select(Trigger).where(Trigger.owner_id == owner_id).order_by(col(Trigger.id))
        )
    ).all()
    return [
        GoalPublic(
            **goal.model_dump(),
            triggers=[
                TriggerPublic.model_validate(trigger)
                for trigger in triggers
                if trigger.goal_id == goal.id
            ],
        )
        for goal in goals
    ]


async def remove(session: AsyncSession, goal: Goal) -> None:
    """Delete a goal and every trigger of it."""
    for trigger in await session.exec(select(Trigger).where(Trigger.goal_id == goal.id)):
        assert trigger.id is not None
        await frames.forget_trigger_vector(session, trigger.id)
        await session.delete(trigger)
    await session.delete(goal)
    await session.commit()


async def remove_of(session: AsyncSession, owner_id: int) -> None:
    """Delete every goal and trigger of an account, without committing (the account is going)."""
    for trigger in await session.exec(select(Trigger).where(Trigger.owner_id == owner_id)):
        assert trigger.id is not None
        await frames.forget_trigger_vector(session, trigger.id)
        await session.delete(trigger)
    for goal in await session.exec(select(Goal).where(Goal.owner_id == owner_id)):
        await session.delete(goal)
