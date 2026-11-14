"""The screens the glasses show. Each returns the full reply for one intent.

Design for a 600x600 see-through display: black is transparent, so keep layouts sparse, text
short and contrast high, with at most three buttons in a row.
"""

from app.glasses import photos
from app.glasses.layout import Button, FlexBox, GlassesReply, Icon, Image, Text

PAD = 32


def _screen(*children, justify="start") -> FlexBox:
    return FlexBox(padding=PAD, gap=20, justify=justify, children=list(children))


def _buttons(*buttons: Button) -> FlexBox:
    return FlexBox(direction="row", gap=12, justify="center", children=list(buttons))


def _title(icon: str, text: str) -> FlexBox:
    return FlexBox(
        direction="row",
        gap=12,
        align="center",
        children=[Icon(name=icon), Text(value=text, style="HEADING")],
    )


def home() -> GlassesReply:
    return GlassesReply(
        intent="home",
        screen=_screen(
            _title("glasses", "Glass Memory"),
            Text(value="Say “take a photo”, or ask “where did I leave my keys?”"),
            _buttons(
                Button(label="Photo", action="take_photo"),
                Button(label="Photos", action="gallery"),
                Button(label="Help", action="help"),
            ),
            justify="center",
        ),
    )


def help_() -> GlassesReply:
    return GlassesReply(
        intent="help",
        screen=_screen(
            _title("help", "You can say"),
            Text(value="“Take a photo”"),
            Text(value="“Show my photos”"),
            Text(value="“Where did I leave my keys?”"),
            Text(value="“Home”"),
            _buttons(Button(label="Home", action="home")),
        ),
        speech="Say take a photo, show my photos, or ask me where something is.",
    )


def capture() -> GlassesReply:
    return GlassesReply(
        intent="take_photo",
        screen=_screen(_title("camera", "Taking a photo…"), justify="center"),
        client_action="capture_photo",
    )


def photo_saved(name: str, note: str | None = None) -> GlassesReply:
    """A photo kept on the backend only: memory is not set up, or `note` says what failed."""
    caption = photos.taken_at(name).strftime("%H:%M:%S UTC")
    return GlassesReply(
        intent="photo_saved",
        screen=_screen(
            _title("check", "Photo saved"),
            Image(src=photos.url(name), alt="The photo just taken", width=536, height=302),
            Text(value=f"{caption} · {note}" if note else caption, style="CAPTION"),
            _buttons(
                Button(label="Another", action="take_photo"),
                Button(label="Home", action="home"),
            ),
        ),
        speech="Photo saved.",
    )


def photo_described(name: str, description: str, note: str | None = None) -> GlassesReply:
    """A photo with the chat model's description; `note` says whether it reached memory."""
    children = [
        _title("brain", "What I see"),
        Image(src=photos.url(name), alt="The photo just taken", width=320, height=180),
        # The model writes a sentence or two; smaller text keeps a long one on the display.
        Text(value=description, style="BODY" if len(description) <= 160 else "CAPTION"),
    ]
    if note:
        children.append(Text(value=note.capitalize(), style="CAPTION"))
    children.append(
        _buttons(
            Button(label="Another", action="take_photo"),
            Button(label="Home", action="home"),
        )
    )
    return GlassesReply(intent="photo_described", screen=_screen(*children), speech=description)


def gallery() -> GlassesReply:
    names = photos.latest(4)
    if not names:
        return GlassesReply(
            intent="gallery",
            screen=_screen(
                _title("image", "No photos yet"),
                Text(value="Say “take a photo” to take one."),
                _buttons(
                    Button(label="Photo", action="take_photo"), Button(label="Home", action="home")
                ),
                justify="center",
            ),
            speech="No photos yet.",
        )
    thumbs = [Image(src=photos.url(n), alt=f"Photo {n}", width=260, height=146) for n in names]
    rows = [FlexBox(direction="row", gap=16, children=thumbs[i : i + 2]) for i in (0, 2)]
    return GlassesReply(
        intent="gallery",
        screen=_screen(
            _title("image", f"Latest {len(names)} photos"),
            *[row for row in rows if row.children],
            _buttons(Button(label="Home", action="home")),
        ),
    )


def memory_answer(question: str, answer: str, images: list[str]) -> GlassesReply:
    thumbs = [Image(src=src, alt="A memory", width=168, height=94) for src in images[:3]]
    children = [
        Text(value=question, style="CAPTION"),
        Text(value=answer),
    ]
    if thumbs:
        children.append(FlexBox(direction="row", gap=16, children=thumbs))
    return GlassesReply(
        intent="ask_memory",
        screen=_screen(
            _title("brain", "Memory"), *children, _buttons(Button(label="Home", action="home"))
        ),
        speech=answer,
    )


def memory_problem(question: str, problem: str) -> GlassesReply:
    return GlassesReply(
        intent="ask_memory",
        screen=_screen(
            _title("alert", "Memory"),
            Text(value=question, style="CAPTION"),
            Text(value=problem),
            _buttons(Button(label="Home", action="home")),
        ),
        speech=problem,
    )


def unknown(heard: str) -> GlassesReply:
    return GlassesReply(
        intent="unknown",
        screen=_screen(
            _title("mic", "Didn’t catch that"),
            Text(value=f"Heard: “{heard}”" if heard else "Heard nothing.", style="CAPTION"),
            Text(value="Try “take a photo”, or ask a question."),
            _buttons(Button(label="Help", action="help"), Button(label="Home", action="home")),
        ),
        speech="Sorry, I didn't catch that.",
    )
