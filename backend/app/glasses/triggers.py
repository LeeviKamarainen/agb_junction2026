"""Gemma writes the triggers: the wearer says what they want the glasses to watch out for, and the
chat model turns that into things the frames can be matched against.

What makes a good trigger was worked out on recorded footage (memory/notebooks/TRIG-1) and is
written down for the model in trigger_skill.md, which goes into its prompt.
"""

import json
from pathlib import Path

from pydantic import ValidationError

from app.llm import Chat
from app.models import TriggerCreate

SKILL = (Path(__file__).parent / "trigger_skill.md").read_text()
MOST = 10  # triggers kept from one answer
SYSTEM = f"""You set up a person's smart glasses for something they asked you to watch out for. \
You cannot watch the camera yourself: you write triggers, and the glasses wake an assistant with \
the still when the camera sees one.

{SKILL}

Now you only write the first triggers. Their levels are set for you, from each trigger's own \
noise in the camera stream.

Answer with JSON only: {{"triggers": [{{"name": a title of one to three words for the glasses' \
display, "watch_for": the trigger}}, ...]}}."""
INSTRUCTION = (
    "The wearer asked: “{goal}” If the still shows what they asked about, tell them what they "
    "need to know now."
)


async def write(chat: Chat, goal: str) -> list[TriggerCreate]:
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
            triggers.append(
                TriggerCreate(
                    name=(str(item.get("name") or "").strip() or watch_for)[:60],
                    watch_for=watch_for,
                    instruction=INSTRUCTION.format(goal=goal.strip()),
                )
            )
        except ValidationError:
            continue  # too long a caption, say: leave that one out
    if not triggers:
        raise ValueError("the chat model did not answer with triggers")
    return triggers[:MOST]
