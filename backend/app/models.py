from datetime import datetime

from sqlmodel import Field, SQLModel

# Example feature: delete Note (route, seed rows, page, tests too) once a real feature exists.


class NoteBase(SQLModel):
    text: str = Field(min_length=1, max_length=500)


class Note(NoteBase, table=True):
    id: int | None = Field(default=None, primary_key=True)


class NoteCreate(NoteBase):
    pass


class NotePublic(NoteBase):
    id: int


# What the glasses remember. Each memory has one embedding in the memory_embedding vector table
# (app/vectors.py) under the same id; add both with app.search.add_memory.


class Memory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # "image" (a still from the glasses) or "text"
    created_at: datetime = Field(index=True)  # when it was seen or heard; timezone-aware
    # A caption, transcript or note. Keyword search matches here, so captions make images findable
    # by keyword too.
    text: str = ""
    image_file: str | None = None  # the JPEG's name in the media folder (app/media.py)
    # A keyword grouping memories by where they came from, e.g. "manual" (added on the page) or
    # "glasses". Searches can be limited to some collections; app.search.clean_collection says
    # what a name may be.
    collection: str = Field(default="manual", index=True)


class MemoryPublic(SQLModel):
    id: int
    kind: str
    created_at: datetime
    text: str
    image_url: str | None  # /api/memories/{id}/image, for the browser
    collection: str


class CollectionCount(SQLModel):
    name: str
    count: int


class MemoryVector(MemoryPublic):
    embedding: str  # base64 of the little-endian float32 vector, 768 numbers


class TextMemoryCreate(SQLModel):
    text: str = Field(min_length=1, max_length=2000)
    created_at: datetime | None = None  # defaults to now; without an offset it is taken as UTC
    collection: str = Field(default="manual", max_length=40)


# Things the wearer asked to be warned about ("remind me not to trip over the stool"). While the
# glasses watch, each frame is checked for every reminder's `target`; app/glasses/reminders.py.


class Reminder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    text: str  # what the wearer said
    target: str  # the object to look for, e.g. "stool"
    created_at: datetime
    last_warned_at: datetime | None = None  # warnings repeat at most once a minute


class ReminderPublic(SQLModel):
    id: int
    text: str
    target: str
    created_at: datetime
    last_warned_at: datetime | None


class ReminderCreate(SQLModel):
    text: str = Field(min_length=1, max_length=500)


# Triggers: things the glasses watch for in the frames they send (app/glasses/frames.py). Each has
# one embedding of `watch_for` in the trigger_embedding vector table, same id, made on first use.


class TriggerBase(SQLModel):
    name: str = Field(min_length=1, max_length=60)  # shown as the title on the glasses
    # What to look for, as a short description of the scene; compared with every frame.
    watch_for: str = Field(min_length=1, max_length=300)
    # What the AI model should tell the wearer when it fires, e.g. "Remind them to take the keys".
    instruction: str = Field(min_length=1, max_length=1000)
    # Fires when a frame is at most this cosine distance from watch_for (0 identical, 1 unrelated).
    max_distance: float = Field(default=0.75, ge=0, le=2)
    cooldown_s: int = Field(default=120, ge=0, le=86_400)  # at most once per this many seconds
    enabled: bool = True


class Trigger(TriggerBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    last_fired_at: datetime | None = None


class TriggerCreate(TriggerBase):
    pass


class TriggerPublic(TriggerBase):
    id: int
    last_fired_at: datetime | None
