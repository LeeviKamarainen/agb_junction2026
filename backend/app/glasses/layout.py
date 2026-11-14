"""Display layouts, shaped like the Device Access Toolkit's display components.

The glasses render 600x600 pixels. A layout is a tree whose root is a `FlexBox`, as on the SDK,
so the Android app can turn it into `display.sendContent { ... }` calls one component at a time,
and the browser harness draws the same tree as HTML. Keep to what the SDK has: FlexBox, Text,
Image, Icon and Button.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Text(BaseModel):
    type: Literal["text"] = "text"
    value: str
    style: Literal["HEADING", "BODY", "CAPTION"] = "BODY"


class Icon(BaseModel):
    type: Literal["icon"] = "icon"
    # A plain name such as "camera"; each client maps it to its own icon set.
    name: str


class Image(BaseModel):
    type: Literal["image"] = "image"
    # A URL the client can fetch, usually a relative /api/... path.
    src: str
    alt: str = ""
    # Display pixels. Keep images small: the SDK sends them over Bluetooth.
    width: int | None = None
    height: int | None = None


class Button(BaseModel):
    type: Literal["button"] = "button"
    label: str
    # Sent back as `{"action": ...}` to POST /api/glasses/command when the wearer presses it.
    action: str


class FlexBox(BaseModel):
    type: Literal["flexbox"] = "flexbox"
    direction: Literal["column", "row"] = "column"
    gap: int = 16
    padding: int = 0
    align: Literal["start", "center", "end", "stretch"] = "stretch"
    justify: Literal["start", "center", "end", "space-between"] = "start"
    children: list["Component"] = []


Component = Annotated[FlexBox | Text | Icon | Image | Button, Field(discriminator="type")]

FlexBox.model_rebuild()


class GlassesReply(BaseModel):
    """What a client does after any request: draw `screen`, speak `speech`, run `client_action`."""

    # What the backend understood, e.g. "take_photo"; shown in the harness log.
    intent: str
    screen: FlexBox
    # Said through the glasses' speakers when set.
    speech: str | None = None
    # Hardware the client should use next. "capture_photo": take a still and POST it to
    # /api/glasses/photos.
    client_action: Literal["capture_photo"] | None = None
