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
