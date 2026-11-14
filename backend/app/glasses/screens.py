"""The screens the glasses show. Each returns the full reply for one intent.

The display is a 600x600 see-through screen where black is transparent, so it stays empty while
nothing is happening: no buttons. The wearer acts through the glasses' capture button and the
Neural Band (double index-finger tap to ask), which arrive as actions. A reply that shows
something sets `dismiss_after_s`, and the client goes back to the empty screen after it. While
the microphone records, the client draws its own red light in a corner.
"""

import re

from app.glasses import photos
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


def help_() -> GlassesReply:
    text = "Press the capture button for a photo. Double tap to ask about your photos."
    return GlassesReply(
        intent="help",
        screen=_screen(
            _title("help", "Help"),
            Text(value="Capture button: take a photo"),
            Text(value="Double tap: ask about your photos"),
        ),
        speech=text,
        dismiss_after_s=10,
    )


def capture() -> GlassesReply:
    return GlassesReply(intent="take_photo", screen=FlexBox(), client_action="capture_photo")


def photo_saved(name: str, note: str | None = None) -> GlassesReply:
    """A photo kept on the backend only: memory is not set up, or `note` says what failed."""
    return GlassesReply(
        intent="photo_saved",
        screen=_screen(
            _title("check", "Photo saved"),
            *([Text(value=note.capitalize(), style="CAPTION")] if note else []),
        ),
        speech="Photo saved.",
        dismiss_after_s=3,
    )


def photo_described(name: str, description: str, note: str | None = None) -> GlassesReply:
    """A photo with the chat model's description; `note` says whether it reached memory."""
    children = [
        _title("check", "Photo saved"),
        # The model writes a sentence or two; smaller text keeps a long one on the display.
        Text(value=description, style="BODY" if len(description) <= 160 else "CAPTION"),
    ]
    if note:
        children.append(Text(value=note.capitalize(), style="CAPTION"))
    return GlassesReply(
        intent="photo_described",
        screen=_screen(*children),
        speech=description,
        dismiss_after_s=_seconds_to_read(description),
    )


def gallery() -> GlassesReply:
    names = photos.latest(4)
    if not names:
        return GlassesReply(
            intent="gallery",
            screen=_screen(_title("image", "No photos yet")),
            speech="No photos yet.",
            dismiss_after_s=3,
        )
    thumbs = [Image(src=photos.url(n), alt=f"Photo {n}", width=260, height=146) for n in names]
    rows = [FlexBox(direction="row", gap=16, children=thumbs[i : i + 2]) for i in (0, 2)]
    return GlassesReply(
        intent="gallery",
        screen=_screen(
            _title("image", f"Latest {len(names)} photos"),
            *[row for row in rows if row.children],
            justify="center",
        ),
        dismiss_after_s=8,
    )


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


def unknown(heard: str) -> GlassesReply:
    return GlassesReply(
        intent="unknown",
        screen=_screen(
            _title("mic", "Didn’t catch that"),
            Text(value=f"Heard: “{heard}”" if heard else "Heard nothing.", style="CAPTION"),
        ),
        speech="Sorry, I didn't catch that.",
        dismiss_after_s=4,
    )
