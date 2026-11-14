"""Warnings the wearer sets up by voice: "remind me not to trip over the stool in the kitchen".

A reminder names one object (its `target`). While the glasses watch, the client posts a frame
every few seconds; `detect` asks the chat model which targets are in view. A target in view gives
a warning, at most once a minute per reminder.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.glasses import memory
from app.llm import Chat, UpstreamError
from app.models import Reminder

COOLDOWN = timedelta(minutes=1)

_REMIND = re.compile(r"^(please |can you |could you )?(remind|warn|alert|tell|notify) me\b")
_ARTICLE = r"(?:a|an|the|my|any|some|that|this)"
# Words that end the object's name: "a stool in the kitchen" -> "stool".
_STOP = {
    "in", "on", "at", "near", "by", "under", "when", "if", "while", "so", "because", "before",
    "after", "again", "please", "there", "here", "it", "and", "or", "to", "from", "of", "with",
}  # fmt: skip
_LEAD = re.compile(rf"\b(?:{_ARTICLE}|over|about|for|of|see|spot|avoid)\s+")


def _words(text: str) -> str:
    text = re.sub(r"[^\w\s'-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def is_reminder(text: str) -> bool:
    return bool(_REMIND.match(_words(text)))


def target_from(text: str) -> str | None:
    """The object a reminder is about, by rule: the first noun phrase after an article or a
    word like "over"/"about", up to two words."""
    words = _words(_REMIND.sub("", _words(text)))
    for lead in _LEAD.finditer(words):
        name: list[str] = []
        for word in words[lead.end() :].split():
            if word in _STOP or re.fullmatch(_ARTICLE, word):
                break
            name.append(word)
            if len(name) == 2:
                break
        if name:
            return " ".join(name)
    return None


TARGET_PROMPT = (
    "The wearer of smart glasses asked: “{text}”. Which physical object should the glasses watch "
    "for? Answer with only that object, in one to three lowercase words."
)


async def target_of(text: str, chat: Chat | None) -> str | None:
    """The chat model's reading of the object, falling back to the rule."""
    if chat is not None:
        try:
            reply = await chat([{"role": "user", "content": TARGET_PROMPT.format(text=text)}], None)
            answer = _words(reply.get("content") or "")
            if answer and len(answer.split()) <= 3:
                return answer
        except UpstreamError:
            pass
    return target_from(text)


async def add(session: AsyncSession, text: str, chat: Chat | None, owner: int) -> Reminder | None:
    """Stores `owner`'s reminder, or returns None when no object could be found in `text`."""
    target = await target_of(text, chat)
    if not target:
        return None
    reminder = Reminder(
        text=text.strip(), target=target, created_at=datetime.now(UTC), owner_id=owner
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def active(session: AsyncSession, owner: int | None) -> list[Reminder]:
    """`owner`'s reminders, oldest first; None for everyone's."""
    query = select(Reminder).order_by(Reminder.id)  # type: ignore[arg-type]
    if owner is not None:
        query = query.where(Reminder.owner_id == owner)
    return list(await session.exec(query))


DETECT_PROMPT = (
    "This photo was taken just now through the wearer's smart glasses. Which of these objects "
    "can be seen in it?\n{items}\nAnswer with the numbers of the ones you can see, separated by "
    "commas, or the word none."
)


class Unavailable(Exception):
    """The chat model is not set up or cannot be reached, so nothing looks at the frame."""


async def detect(jpeg: bytes, targets: list[str], chat: Chat | None) -> tuple[list[int], str]:
    """Indexes into `targets` of the objects in view, and how that was decided."""
    if not targets:
        return [], "no reminders"
    if chat is None:
        raise Unavailable("no vision model is set up (LLM_URL)")
    items = "\n".join(f"{i}. {t}" for i, t in enumerate(targets, start=1))
    try:
        reply = await chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DETECT_PROMPT.format(items=items)},
                        {"type": "image_url", "image_url": {"url": memory.data_url(jpeg)}},
                    ],
                }
            ],
            None,
        )
    except UpstreamError:
        raise Unavailable("the chat model is unreachable") from None
    numbers = {int(n) for n in re.findall(r"\d+", reply.get("content") or "")}
    return [i for i in range(len(targets)) if i + 1 in numbers], "chat model"


def due(reminder: Reminder, now: datetime) -> bool:
    last = reminder.last_warned_at
    if last is None:
        return True
    if last.tzinfo is None:  # SQLite hands datetimes back without their offset
        last = last.replace(tzinfo=UTC)
    return now - last >= COOLDOWN
