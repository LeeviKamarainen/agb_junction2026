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
    # Who added it: a User id, or DEMO_OWNER (0) for the demo memories everyone can see. Copied
    # into memory_embedding as `owner`, so searches (and so the chat model) only reach what the
    # asking user may see; app/auth.py.
    owner_id: int = Field(default=0, index=True)


class MemoryPublic(SQLModel):
    id: int
    kind: str
    created_at: datetime
    text: str
    image_url: str | None  # /api/memories/{id}/image, for the browser
    collection: str
    demo: bool  # shared with every user
    owner: str | None = None  # the uploader's username; None for demo memories


class CollectionCount(SQLModel):
    name: str
    count: int


class MemoryVector(MemoryPublic):
    embedding: str  # base64 of the little-endian float32 vector, 768 numbers


class TextMemoryCreate(SQLModel):
    text: str = Field(min_length=1, max_length=2000)
    created_at: datetime | None = None  # defaults to now; without an offset it is taken as UTC
    collection: str = Field(default="manual", max_length=40)
    demo: bool = False  # admin only: share it with every user


# Things the wearer asked to be warned about ("remind me not to trip over the stool"). While the
# glasses watch, each frame is checked for every reminder's `target`; app/glasses/reminders.py.


class Reminder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(index=True)  # the User who set it
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


# Accounts: one admin, who sees everything, and temporary guests, who see the demo memories and
# their own. A guest has no password and expires after 30 minutes, with everything it added.
# app/auth.py hashes passwords, checks sessions and deletes expired guests.


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)  # lower case
    password_hash: str
    role: str = "guest"  # "admin" or "guest"
    created_at: datetime
    expires_at: datetime | None = Field(default=None, index=True)  # guests only; None: never


class AuthSession(SQLModel, table=True):
    # sha256 of the token, so a leaked database cannot be used to log in
    token_hash: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    expires_at: datetime


class UserPublic(SQLModel):
    id: int
    username: str
    role: str
    expires_at: datetime | None = None  # when a guest account and its memories are deleted


class Credentials(SQLModel):
    """The admin's login; guests have no password."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class LoginResult(UserPublic):
    # The same session as the cookie, for clients without cookies (the glasses' phone app): send
    # it as `Authorization: Bearer <token>`.
    token: str
