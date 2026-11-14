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


class MemoryPublic(SQLModel):
    id: int
    kind: str
    created_at: datetime
    text: str
    image_url: str | None  # /api/memories/{id}/image, for the browser


class TextMemoryCreate(SQLModel):
    text: str = Field(min_length=1, max_length=2000)
    created_at: datetime | None = None  # defaults to now; without an offset it is taken as UTC
