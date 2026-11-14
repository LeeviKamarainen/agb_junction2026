from sqlmodel import Session

from app.models import Note, Showcase

# Demo data. Editing this file rebuilds the database on the next start (see app/db.py).


def seed(session: Session) -> None:
    session.add_all(
        [
            Note(text="Welcome to Junction 2026"),
            Note(text="Edit backend/app/seed.py to change the demo data"),
            Showcase(
                title="Junction replay",
                description="Upload an ordered set of images to curate this demo.",
                kind="images",
            ),
        ]
    )
