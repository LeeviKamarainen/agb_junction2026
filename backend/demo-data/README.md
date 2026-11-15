# Recorded demos

One folder per demo, named by its tag (the memory collection it was recorded under): `manifest.json` and the
stills `0001.jpg`, `0002.jpg`, ... Every start of the backend adds the bundles that are not in the database yet,
as demo memories every user sees (`backend/app/demo_bundles.py`), so a rebuilt database keeps them.

1. Record: in the glasses app, type a tag in **Demo recording** and press **Record demo**, then **Stop demo**.
2. Download into this folder: `make demo-pull TAG=<tag> API=https://<host>` (asks for the admin password).
   The same zip is at `GET /api/memories/export?collection=<tag>`.
3. Commit the folder in a pull request. A bundle made with another embedding model than the configured one is
   skipped with a warning: record it again.

The stills have their faces blurred already (and again when loaded).
