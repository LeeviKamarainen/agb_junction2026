from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


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
    # What made it, when something other than a person did: "clip:<id>:<digest>" for a frame of a
    # published demo clip (app/demo.py), which replaces it when the clip changes.
    source: str = Field(default="", index=True)


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
# their own. A guest has no password and expires after 60 minutes, with everything it added.
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


class GuestLogin(SQLModel):
    """Reopen an unexpired temporary account using its generated username."""

    username: str = Field(min_length=1, max_length=64)


class Credentials(SQLModel):
    """The admin's login; guests have no password."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class LoginResult(UserPublic):
    # The same session as the cookie, for clients without cookies (the glasses' phone app): send
    # it as `Authorization: Bearer <token>`.
    token: str


# Goals and their triggers: what the glasses watch for in the frames they stream
# (app/glasses/frames.py). A goal is what the wearer asked for, in their own words ("tell me when
# my bus comes"); Gemma writes its triggers, the things a frame can be matched against
# (app/glasses/triggers.py). The triggers of a goal work as one: they are switched on and off
# and removed together, and whichever of them fires, the chat model is asked about the goal.
# Both belong to one account and are seen and used by nobody else, the admin included.


class Goal(SQLModel, table=True):
    owner_id: int = Field(index=True)
    id: int | None = Field(default=None, primary_key=True)
    text: str = Field(min_length=1, max_length=500)  # what the wearer asked for
    enabled: bool = True  # off: none of its triggers is checked
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TriggerBase(SQLModel):
    name: str = Field(min_length=1, max_length=60)  # shown as the title on the glasses
    # What to look for, as a short caption of one visible thing; compared with every frame.
    watch_for: str = Field(min_length=1, max_length=300)
    # Fires on a stream frame whose score reaches the trigger's level. The level is measured from
    # the trigger's own noise in the stream, this many spreads above it: 3 is quiet, 2 misses
    # less. See app/glasses/scores.py.
    sigmas: float = Field(default=3, ge=0, le=10)


# Each trigger has one embedding of `watch_for` in the trigger_embedding vector table, same id,
# made on first use.
class Trigger(TriggerBase, table=True):
    owner_id: int = Field(index=True)  # its goal's
    goal_id: int = Field(index=True)
    id: int | None = Field(default=None, primary_key=True)
    last_fired_at: datetime | None = None  # when it last woke the chat model, message or not
    # The score a frame must reach, measured on the first stream that has enough frames; None
    # until then.
    level: float | None = None


class TriggerDraft(TriggerBase):
    """A trigger as it is written or edited."""


class TriggerPublic(TriggerBase):
    id: int
    goal_id: int
    last_fired_at: datetime | None
    level: float | None


class ShowcaseFields(SQLModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    kind: str = "images"
    duration_s: float = Field(default=30, gt=0, le=3600)
    image_seconds: float = Field(default=3, ge=0.25, le=60)
    position: int = Field(default=0, ge=0, le=10000)
    published: bool = False


class Showcase(ShowcaseFields, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # Only the upload endpoint writes this list of random file names and original labels.
    assets_json: str = "[]"


class GoalCreate(SQLModel):
    text: str = Field(min_length=1, max_length=500)
    # Given: these are the goal's triggers and Gemma is not asked. Left out: Gemma writes them.
    triggers: list[TriggerDraft] | None = Field(default=None, min_length=1, max_length=10)


class GoalUpdate(SQLModel):
    enabled: bool


class GoalPublic(SQLModel):
    id: int
    text: str
    enabled: bool
    created_at: datetime
    triggers: list[TriggerPublic]
