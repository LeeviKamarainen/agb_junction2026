---
name: add-feature
description: Build a feature end-to-end in this repo, from data model through /api endpoint to the page that uses it. Use when adding or changing a model, an API endpoint, or a page that shows backend data.
---

# Add a feature

The example Note feature is the pattern to copy: `backend/app/models.py`, `backend/app/routes/notes.py`, `frontend/src/app/page.tsx`, `frontend/src/app/note-form.tsx`. Once the first real feature exists, delete Note everywhere: model, route, seed rows, page, form, `backend/tests/test_notes.py`.

1. **Model**: in `backend/app/models.py`, add a table model plus `<Name>Create` and `<Name>Public` schemas, as Note does. Put demo rows in `backend/app/seed.py`. The next backend start rebuilds the database because the models changed.
2. **Endpoint**: add `backend/app/routes/<feature>.py` with an `APIRouter` (paths are relative to `/api`) and register it in `backend/app/main.py` with `api.include_router(...)`.
3. **Test**: add `backend/tests/test_<feature>.py` using the `client` fixture, covering the behaviour the demo relies on. Done when `make check-backend` passes.
4. **Types**: run `make api-types`, then confirm the new paths appear in `frontend/src/lib/api/schema.d.ts`.
5. **Page**: read `frontend/AGENTS.md` first. Server Components fetch through `@/lib/api/server` inside `<Suspense>`; Client Components call `@/lib/api/browser` from event handlers, then `router.refresh()`.

Done when `make check` passes and the feature works end-to-end under `make dev`.
