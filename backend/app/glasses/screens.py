"""The screens the glasses show. Each returns the full reply for one intent.

The display is a 600x600 see-through screen where black is transparent, so it stays empty while
nothing is happening: no buttons. The wearer acts through the Neural Band: a double index-finger
tap opens the microphone for a question, and arrives as an action. A reply that shows
something sets `dismiss_after_s`, and the client goes back to the empty screen after it. While
the microphone records, the client draws its own red light in a corner.
"""

import re

from app.glasses.dejavu import Recall
from app.glasses.layout import FlexBox, GlassesReply, Icon, Image, Text

PAD = 32


def _screen(*children, justify="end", align="stretch") -> FlexBox:
    # Bottom of the view by default: the middle is where the wearer is looking.
    return FlexBox(padding=PAD, gap=16, justify=justify, align=align, children=list(children))


def _title(icon: str, text: str) -> FlexBox:
    return FlexBox(
        direction="row",
        gap=12,
        align="center",
        children=[Icon(name=icon), Text(value=text, style="HEADING")],
    )


def _seconds_to_read(text: str) -> float:
    """Long enough to read `text` once at a glance, between 4 and 20 seconds."""
    return min(20.0, max(4.0, 2.0 + len(text.split()) * 0.4))


def home() -> GlassesReply:
    """Nothing: the see-through display stays clear."""
    return GlassesReply(intent="home", screen=FlexBox())


def listen() -> GlassesReply:
    """The microphone is open: the client sends what it hears as `{"question": ...}`. The
    screen stays clear; the client's red recording light is all the wearer sees."""
    return GlassesReply(intent="ask", screen=FlexBox(), client_action="listen_question")


def memory_answer(question: str, answer: str) -> GlassesReply:
    """The answer as text only: the stills it came from stay off the display."""
    said = plain(answer)
    return GlassesReply(
        intent="ask_memory",
        screen=_screen(
            Text(value=question, style="CAPTION"),
            # The model may answer with Markdown; the display shows plain text.
            Text(value=said, style="BODY" if len(said) <= 160 else "CAPTION"),
        ),
        speech=said,
        dismiss_after_s=_seconds_to_read(question + " " + said),
    )


def recall(found: Recall) -> GlassesReply:
    """Déjà vu: the earlier stills most like what the wearer is looking at, and whether the chat
    model finds any of them the same thing. Three across fill the display's width."""
    said = plain(found.say)
    title = f"Seen {found.last_seen}" if found.last_seen else "Not seen before"
    stills = [
        FlexBox(
            gap=4,
            children=[
                Image(
                    src=f"/api/memories/{still.memory_id}/image",
                    alt=f"Seen {still.ago}",
                    width=168,
                    height=95,
                ),
                Text(value=("Same, " if still.same else "") + still.ago, style="CAPTION"),
            ],
        )
        for still in found.stills
    ]
    return GlassesReply(
        intent="recall",
        screen=_screen(
            _title("check" if found.last_seen else "alert", title),
            *([FlexBox(direction="row", gap=16, children=stills)] if stills else []),
            Text(value=said, style="BODY" if len(said) <= 160 else "CAPTION"),
        ),
        speech=f"You last saw this {found.last_seen}. {said}" if found.last_seen else said,
        dismiss_after_s=max(8.0, _seconds_to_read(said)),
    )


def recall_needs_still() -> GlassesReply:
    text = "Send a photo of it first, then ask again."
    return GlassesReply(
        intent="recall",
        screen=_screen(_title("camera", "I need to see it"), Text(value=text, style="CAPTION")),
        speech=text,
        dismiss_after_s=5,
    )


def plain(text: str) -> str:
    """Markdown emphasis and bullets turned into one plain sentence: "a: * b * c" -> "a: b, c"."""
    text = re.sub(r"(^|\n|\s)[-*•]\s+", "\n", text)  # every bullet starts a line
    text = re.sub(r"[*_`#]+", "", text)
    items = [line.strip().rstrip(",;") for line in text.splitlines() if line.strip()]
    text = ", ".join(items).replace(":,", ":")
    return re.sub(r"\s+", " ", text).strip()


def reminder_added(target: str, text: str) -> GlassesReply:
    return GlassesReply(
        intent="remind",
        screen=_screen(
            _title("check", f"Watching for: {target}"),
            Text(value=text, style="CAPTION"),
        ),
        speech=f"Okay, I’ll warn you when I see the {target}.",
        dismiss_after_s=4,
    )


def reminder_unclear(text: str) -> GlassesReply:
    return GlassesReply(
        intent="remind",
        screen=_screen(
            _title("alert", "What should I watch for?"),
            Text(value=text, style="CAPTION"),
        ),
        speech="Sorry, what should I watch for? Name the thing, like the stool.",
        dismiss_after_s=5,
    )


def warning(target: str, text: str) -> GlassesReply:
    """An object from a reminder is in view. Short and loud: the wearer may be walking."""
    return GlassesReply(
        intent="warning",
        screen=_screen(
            Icon(name="warning"),
            Text(value=f"{target.capitalize()} ahead", style="HEADING"),
            Text(value=text, style="CAPTION"),
            justify="center",
            align="center",
        ),
        speech=f"Careful: {target} ahead.",
        dismiss_after_s=6,
    )


def memory_problem(question: str, problem: str) -> GlassesReply:
    return GlassesReply(
        intent="ask_memory",
        screen=_screen(
            Text(value=question, style="CAPTION"),
            _title("alert", problem),
        ),
        speech=problem,
        dismiss_after_s=5,
    )
