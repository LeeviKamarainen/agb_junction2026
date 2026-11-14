# Disposable database, rebuilt when the models change

We use SQLite without migrations: on startup the backend compares a fingerprint of the models and `seed.py` with the one stored in the database, and wipes and re-seeds the database when they differ. For a 48-hour hackathon with a schema that changes hourly, this beats writing migrations, at the cost of losing data entered by hand, so demo data lives in `seed.py`. This was chosen before we knew the challenge; revisit it if the project ends up needing to keep real user data.
