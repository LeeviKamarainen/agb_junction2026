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

The SQLite database is disposable. When the models or `backend/app/seed.py` change, the backend wipes it and re-seeds on start, in production too, so demo data belongs in `seed.py`. Reasoning: `docs/adr/0001-disposable-database.md`.

Vector search lives in the same file, through the sqlite-vec extension (`backend/app/vectors.py`): declare a table in `VECTOR_TABLES`, then use `vectors.upsert` and `vectors.nearest` with a connection from `get_engine()`. Every row has a `created_at` timestamp (timezone-aware, defaults to now) that `nearest` can filter with `since` and `until`; extra metadata columns are declared per table and filtered with `where`. The vector tables count toward the fingerprint, so changing one wipes and re-seeds like any schema change. The `memory/` API does not use it (see `memory/docs/decisions.md`).

## Frontend

Read `frontend/AGENTS.md` before writing frontend code.

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

`/v1/embeddings` serves EmbeddingGemma 2 (`google/embeddinggemma-2`), 768 numbers per input, in the same space as the stored stills; an input that starts with `data:image/` (a base64 data URL) is embedded as an image, any other as text. It replaced CLIP (`ViT-B-32`, 512 numbers) on 2026-11-14: that name now gets a 400, and vectors made with it cannot be compared with new ones.

## Agent skills

Skills live in `.agents/skills/` (`.claude/skills` is a symlink to it; edit the real path): `add-feature`, `ship`, `review`, `submission`.
