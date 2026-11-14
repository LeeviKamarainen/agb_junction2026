from fastapi import APIRouter
from sqlmodel import select

from app.db import SessionDep
from app.models import Note, NoteCreate, NotePublic

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("", response_model=list[NotePublic])
def list_notes(session: SessionDep):
    return session.exec(select(Note).order_by(Note.id)).all()


@router.post("", response_model=NotePublic, status_code=201)
def create_note(note: NoteCreate, session: SessionDep):
    db_note = Note.model_validate(note)
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return db_note
