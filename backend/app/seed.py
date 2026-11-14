from sqlmodel import Session

from app.models import Note, Showcase, Trigger

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
            # An example for the glasses' frames; edit or delete it in the Memory lab.
            Trigger(
                name="Keys",
                watch_for="a set of keys",
                instruction="Tell the wearer you noticed their keys and where they are, so they "
                "can find them later.",
            ),
        ]
    )
