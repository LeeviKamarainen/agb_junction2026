# Junction 2026

FastAPI backend (`backend/`: uv, SQLModel on SQLite) and Next.js frontend (`frontend/`), run with Docker Compose (`compose.yaml`) on one Hetzner VM behind Caddy (`deploy/Caddyfile`). All backend routes live under `/api`. The visual memory for the glasses (`memory/`) runs on the Gemma server; see "Memory API" below.

## Commands

- `make dev`: backend on :8000 and frontend on :3000, both auto-reloading. Open http://localhost:3000; API docs at http://localhost:3000/api/docs.
- `make check`: the same checks CI runs (ruff, pytest, ESLint, typecheck).
- `make api-types`: regenerate `frontend/src/lib/api/schema.d.ts` after changing backend models or routes, and commit the result.
- `make up`: the production stack in Docker at http://localhost.
- `make reset-db`: delete the local database.

## Git workflow

`main` is production: whatever lands on it deploys. Every change goes through a branch and a pull request.

1. Branch from an up-to-date `main`: `git switch main && git pull && git switch -c <short-topic>`.
2. Commit one concern at a time, with an imperative subject line that says what changed and a body that says why when it isn't obvious.
3. Before pushing, `make check` passes, `make api-types` has been rerun if backend models or routes changed, and the `review` skill reports nothing outstanding.
4. Push the branch and open a pull request with `gh pr create`, saying what changed, why, and how it was tested. CI runs the checks on it.
5. Merge with `gh pr merge <number> --squash --delete-branch` once the checks are green and a person on the team has explicitly approved it.

Anything that changes production needs that same explicit approval from a person: merging, pushing to `main`, `gh workflow run`, `make prod-reset-db` and `make prod-import-keys`. Keep published history intact: bring a branch up to date with `git merge origin/main`, undo published commits with `git revert`, and force-push only your own unmerged branch, with `--force-with-lease`.

## Database

The SQLite database is disposable. When the models or `backend/app/seed.py` change, the backend wipes it and re-seeds on start, in production too, so demo data belongs in `seed.py`. The exception is the demo dataset in `backend/app/demo.py`: it needs the embedding model, so the backend stores it as demo memories (collection `demo`) in the background after each start, replacing stale copies, and `GET /api/demo/timeline` serves it to anyone. Reasoning: `docs/adr/0001-disposable-database.md`.

Database access is async (`backend/app/db.py`: SQLAlchemy on aiosqlite, `AsyncSession` from SQLModel): routes are `async def`, take `SessionDep` and await every query (`await session.exec(...)`, `await session.get(...)`, `await session.commit()`); outside a request, open one with `async with new_session() as session:`. Sessions keep their objects after commit, since an async session cannot reload an attribute lazily. The model clients (`backend/app/llm.py`) are async httpx too, so never call blocking I/O from a route; run CPU or file work such as image resizing or password hashing with `anyio.to_thread.run_sync`. SQLite still takes one writer at a time (WAL, `busy_timeout` 5 s). Tests stay synchronous: `TestClient` for routes, and the `db` fixture (`backend/tests/conftest.py`) to run `await work(session)` on the app's event loop; model fakes are `async def`.

Vector search lives in the same file, through the sqlite-vec extension (`backend/app/vectors.py`): declare a table in `VECTOR_TABLES`, then `await vectors.upsert(...)` and `await vectors.nearest(...)` with `await session.connection()` or a connection from `get_engine()`. Every row has a `created_at` timestamp (timezone-aware, defaults to now) that `nearest` can filter with `since` and `until`; extra metadata columns are declared per table and filtered with `where`. The vector tables count toward the fingerprint, so changing one wipes and re-seeds like any schema change. The `memory/` API does not use it (see `memory/docs/decisions.md`).

## Accounts

One admin, who logs in with a password, sees every memory. Everyone else starts a temporary guest on `/guest` or the login page (`POST /api/auth/guest`): a generated name, no password, 60 minutes (`GUEST_MINUTES`), seeing the demo memories and the ones it added. An unexpired guest can return by username with `POST /api/auth/guest/login`, keeping its original expiry. The login page (`/login`) only shows guest options; the admin logs in with a password on the unlinked `/admin`. An expired guest is deleted with its logins, memories and their image files, and reminders (`purge_expired_guests`: at startup, every 5 minutes, and whenever a guest starts or an expired login is used). Code: `backend/app/auth.py`, routes in `backend/app/routes/auth.py`. The admin is created, or its password reset, on every start from `ADMIN_USERNAME` (default `admin`) and `ADMIN_PASSWORD`; without a password a random one is printed in the backend log. In production set it with `gh secret set APP_ADMIN_PASSWORD`. A login is a token in an HttpOnly `session` cookie, or `Authorization: Bearer <token>` for the phone app (`/login` and `/guest` return it).

Every memory has an `owner_id` (0 = demo, which only the admin adds, with `demo=true`), copied into `memory_embedding` as `owner`. Anything that reads memories takes the viewer's scope (`Viewer.owners`, `None` for the admin): listing, images, `vector_search` and its keyword search, and `agent.run`, so the chat model only ever receives the asker's memories and stills. Reminders are per user. `frontend/src/proxy.ts` only redirects pages to `/login` without a cookie; the backend decides access. Since the database is disposable, a model change logs everyone out, and the admin has to upload the demo pictures again. Tests log in through the `client` (admin) and `guest` fixtures in `backend/tests/conftest.py`.

## Frontend

Read `frontend/AGENTS.md` before writing frontend code. The home page (`frontend/src/app/page.tsx`, its parts in `frontend/src/app/_home/`) is the demo: a timeline of the demo dataset, asking about it, and placeholders for the trigger demo and the logged-in user's own space. Its navigation shows only the login menu, plus the Memory lab for the admin; link other pages from the Memory lab or related pages.

## Deploying

Each change that lands on `main` runs the checks, then deploys: the code is copied to the VM, `docker compose up --build` runs there, and the job waits for `https://<vm-ip-with-dashes>.sslip.io/api/health`. Follow it with `gh run watch`. A failed build keeps the previous version running. Roll back by merging a pull request that `git revert`s the bad commit. `gh workflow run ci-cd.yml --ref <branch>` deploys another branch until the next change lands on `main`.

## Production

- `make prod-logs`; `make prod-ssh` (the app is in `/opt/junction2026`, run `docker compose` there).
- `make prod-reset-db`: wipe production data and re-seed.
- `make prod-import-keys`: give everyone in `TEAM_GITHUB_USERS` (Makefile) SSH access with the keys on their GitHub accounts.
- Secrets: `gh secret set APP_<NAME>` (only the repo owner can) becomes `<NAME>` in the backend's environment on the next deploy; `gh variable set APP_<NAME>` for non-secret settings. Locally, put `<NAME>=...` in `backend/.env`.

## Memory API

`memory/` is the visual memory for the glasses: frames are posted to its API, embedded with EmbeddingGemma 2 and stored as memories, shown as a 2D map, and Gemma answers questions from the stills a question retrieves. Read `memory/README.md` before working there; what is left is in `memory/docs/todo.md` and the choices already made in `memory/docs/decisions.md`. It is its own uv project (Python 3.14 with torch, about 6 GB), so `make check` and CI leave it out: run `make check-memory`.

The API runs on the Gemma server (called VM2 in `memory/`), beside vLLM, behind Caddy at `https://<vm2-ip-with-dashes>.sslip.io`. Nothing deploys there automatically, so `make memory-deploy` and `make memory-setup` need the same approval as other production changes. Leave the `gemma` container and port 8000 alone.

- `make memory-deploy`: copy `memory/` to `/opt/glass-memory`, install it and restart the `glass-memory` systemd service. `make memory-setup`: once on a fresh server, after the first deploy.
- `make memory-logs`; `make memory-ssh`.
- `make memory-link`: the map page's address with the token in it, to share.
- `make memory-token`: the token API clients need (works for whoever `make memory-ssh` works for).
- `make memory-send FRAMES=<folder>`: post a folder of JPEG stills as one batch, the folder given from the repo root (needs `uv sync` in `memory/` first).
- The server's address is `VM2_IP` in `memory/.env`, which is not in git. Its secrets live only in `/opt/glass-memory/.env`: `GLASS_API_TOKEN` and `GEMMA_API_KEY`.

Calling it: every route except `GET /` and `GET /health` needs `Authorization: Bearer <GLASS_API_TOKEN>`, else 401. That is the only token this API takes: `GEMMA_API_KEY` is vLLM's (port 8000) and `SEGMENTER_API_TOKEN` is the segmenter's, and neither works here. Never commit the token; read it from the environment. Every route and its answer is in `memory/src/glass_memory/README.md` (HTTP API); the common ones:

```bash
URL=https://<vm2-ip-with-dashes>.sslip.io
AUTH="Authorization: Bearer $GLASS_API_TOKEN"
curl -H "$AUTH" -F frames=@0001.jpg -F frames=@0002.jpg -F every_s=1 $URL/batches   # {"batch", "memories", "summary"}
curl -H "$AUTH" $URL/batches                                                          # newest first
curl -H "$AUTH" -H "Content-Type: application/json" -d '{"question": "where are my keys?"}' $URL/batches/<batch>/ask
curl -H "$AUTH" -H "Content-Type: application/json" -d '{"model": "google/embeddinggemma-2", "input": ["keys"]}' $URL/v1/embeddings
```

`/v1/chat/completions` passes OpenAI-style chat, tool calls included, to vLLM on the same server (`GEMMA_URL`, `GEMMA_API_KEY`), so the backend reaches Gemma with this API's token: `LLM_URL` is the memory API's address and `LLM_API_KEY` its `GLASS_API_TOKEN`. It needs a `make memory-deploy` to go live.

`/v1/embeddings` serves EmbeddingGemma 2 (`google/embeddinggemma-2`), 768 numbers per input, in the same space as the stored stills; an input that starts with `data:image/` (a base64 data URL) is embedded as an image, any other as text: as a search query, or with `"input_type": "document"` as a text to be found (the backend embeds its notes that way). It replaced CLIP (`ViT-B-32`, 512 numbers) on 2026-11-14: that name now gets a 400, and vectors made with it cannot be compared with new ones.

## Glasses

`/glasses` in the frontend is a harness standing in for the glasses (webcam, mic, speakers, a 600×600 display); `glasses-android` is the real client. Both call `/api/glasses/*` (`backend/app/routes/glasses.py`) and draw the `GlassesReply` they get: the screens live in `backend/app/glasses/screens.py`, built only from the SDK's display components in `layout.py`, so a screen that works in the harness can be sent to the glasses. What the wearer says arrives as text in a `question` and goes to the memory as it is: nothing matches it to commands first, and Gemma answers through `agent.answer`, as `/api/ask` does (it needs the chat and embedding models, `LLM_URL` and `EMBED_URL`). Tests replace `optional_chat` and `optional_embedder` through `app.dependency_overrides`.

The display has no buttons and stays empty while idle; a reply that shows something sets `dismiss_after_s`, after which the client clears it. The wearer acts through the Neural Band's double index-finger tap (action `double_tap`, the same as `ask`), which replies with `client_action: "listen_question"`: the client opens its mic, shows only a blinking red light in a corner, and sends the words back as `{"question": ...}`. A question that starts "remind me…" or "warn me…" becomes a reminder instead (`glasses/reminders.py`): while the client watches, it sends a frame every second, the chat model says which reminders' objects are in view, and the backend sends a warning screen, at most once a minute per reminder.

The live traffic runs over one WebSocket per client, `/api/glasses/ws` (`backend/app/glasses/channel.py`, which documents the protocol): JSON text messages with a `type`, and a frame as a small JSON header followed by the JPEG as a binary message. The client sends `command` (as `POST /command`), `watch` on/off, `frame` and `ping`; the server answers `reply`, `watch_result`, `error` and `pong`, and pushes a warning as a `reply` without an `id`. Frames go latest-wins: while one is being checked only the newest waits, so a slow vision model never builds a queue. Each message runs as a coroutine with its own session, at most 4 at a time per client, and the socket and the HTTP routes share `glasses/actions.py`, so `/command` and `/watch` keep working (the harness still uses HTTP for the home screen and reminders). The socket needs a login like the HTTP routes (see Accounts): the browser's `session` cookie, `Authorization: Bearer <token>`, or a first `{"type": "auth", "token": ...}` message with the token `/api/auth/login` returned (never a query parameter: uvicorn logs URLs), else it closes with 1008. Everything it does is scoped to that user. The harness connects to the same origin: Caddy passes the upgrade in production, and `next dev`'s `/api` rewrite proxies it locally. Tests use `TestClient.websocket_connect`; its frontend types are hand-written in `frontend/src/app/glasses/channel.ts`, since a WebSocket is not in the OpenAPI schema.

## Agent skills

Skills live in `.agents/skills/` (`.claude/skills` is a symlink to it; edit the real path): `add-feature`, `ship`, `review`, `submission`.
