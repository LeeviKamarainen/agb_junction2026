# Rows are carried over when the schema changes

This replaces [0001](0001-disposable-database.md).

## Context

The backend wiped its SQLite database whenever the models changed (0001). The schema changed about ten times in the first two days, so most deploys wiped production: the admin's demo clips, every recorded memory and its embedding, goals and triggers, logins. The seed data that made a wipe cheap was gone by then: demo data lives in the demo clips and in the demo bundles committed under `backend/demo-data/`.

## Decision

Still no migrations. On start, `init_db` (`backend/app/db.py`) compares a fingerprint of the model tables and the vector tables (`backend/app/vectors.py`) with the one the database keeps in `PRAGMA user_version`. When they differ, it builds a database from the current models in `app.db.new`, beside the old file, and copies the old rows into it in one transaction:

- every table both schemas have, for the columns both have, ids included, so references between rows, memory ids and the image files they name stay valid;
- a column only the new schema has gets the model's fixed default (`= ...`, or a `server_default`), or NULL when it is optional (`| None`); a table or column only the old schema has is left behind;
- the vector tables with their embeddings, timestamps and the metadata columns both have.

Only when all of that worked does the new file take the old one's place. The old file first gets a second name, `app.db.<UTC time>.<old fingerprint>.bak`, then one rename puts the new file at `app.db`, so that name always holds a whole database. The newest 5 backups are kept (`BACKUPS_KEPT`): a carry-over that lost something should be noticed within a few schema changes, and at about ten in two days, five reach back about a day. One warning in the log says the rows were carried over, with the rows per table, what was dropped and where the backup is. A new database is built the same way and moved into place empty, after removing any `-wal`, `-shm` or `-journal` a deleted database left behind, which SQLite would otherwise replay onto it.

The old file is attached read-only and immutable, so the copy cannot change it and SQLite puts no `-wal` or `-shm` beside it. That needs its WAL emptied first: `init_db` checkpoints it and closes its own connections, and stops if a `-wal` or `-shm` is still there, as then another process has the database open. It checks that again just before the swap, along with the file not having been replaced meanwhile. That matters because SQLite finds a database's WAL by its name: a process that kept the old file open across the swap would mix the old file's pages into the new one. So nothing else may open the database while the backend starts (one backend container, and no one-off commands on `/data/app.db` during a deploy). A process that has opened the file without reading it yet, or opens it in the moment between the last check and the rename, is not caught.

sqlite-vec's vec0 tables can be read and copied across the attach once the extension is loaded on the connection. `PRAGMA table_info` gives no column types for a virtual table, so an old vector table's width is read from its `CREATE VIRTUAL TABLE` statement.

## What stops the start

Whatever cannot be carried over stops the start with a message that names it and says what to do. Nothing is half done: the half-built `app.db.new` is removed, no backup is made, and the old database stays where it was, as it was, so the previous version still finds its data after a revert. The start stops for:

- a new column that is NOT NULL without a fixed default, in a table with rows. A `default_factory` does not count, as it would invent a value for every old row. Give the column a default or make it optional.
- a column whose type changed (a length aside), in a table with rows: SQLite would keep the old values as they are. Keep the type, or give the column a new name so it starts from its default.
- a vector table whose width changed, with rows: the vectors come from another embedding model and cannot be compared with new ones. Keep the old width, or give up the data to start over with the new model (below); the published demo clips come back and are embedded again.
- a new metadata column in a vector table with rows, since vec0 cannot leave it NULL.
- rows that break a constraint of the new schema (NOT NULL, UNIQUE), or refer to rows that do not exist (checked after the copy, so the message counts them per table).
- another process using the database, before the build or by the time of the swap, or the file replaced meanwhile.

A table without rows needs none of this: there is nothing to fill in.

In production the backend then restarts with the same message (`make prod-logs`) until a fix is deployed or the change reverted. To give up the data on purpose, `make prod-reset-db` still wipes the database. That deletes the current data for good, as only a carry-over that succeeded leaves a backup: copy `app.db` first if it may be needed. Earlier backups stay.

## Consequences

- Demo clips, memories and their embeddings, goals and triggers, and logins survive a schema change.
- A model change has to be carry-over friendly: a default or `| None` on a new column, and a new name rather than a new type. A renamed column starts from its default; its old values stay in the backups while they are kept.
- The start that follows a schema change copies the whole database. Measured on a desktop CPU: 20,000 memories with their embeddings (70 MB) took 6 seconds. The disk needs room for one more copy beside the backups.
- To restore a backup: stop the backend, move `app.db` aside (it may be needed again) and remove `app.db-wal` and `app.db-shm` if they are there (a stale WAL would be replayed onto the restored file), copy the backup to `app.db` and start. If the code's schema differs from the backup's, the start carries it over again.
- Once this change has run in production, do not undo it with a revert: the code before it wipes any database whose fingerprint it does not know, and it does not know the one this code writes. If that code must run again, first put in place the first backup this code made, which has that code's fingerprint (and lacks what was written since).
