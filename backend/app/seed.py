from sqlmodel import Session

from app.models import Note

# Demo data. Editing this file rebuilds the database on the next start (see app/db.py).


def seed(session: Session) -> None:
    session.add_all(
        [
            Note(text="Welcome to Junction 2026"),
            Note(text="Edit backend/app/seed.py to change the demo data"),
        ]
    )
