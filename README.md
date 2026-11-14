# Junction 2026

Our team's project for [Junction 2026](https://2026.hackjunction.com/) (13–15 November 2026, Espoo). The challenge is not chosen yet.

## Getting started

You need [uv](https://docs.astral.sh/uv/), Node.js 24 and npm, Docker, `make`, and the GitHub CLI (`gh`).

```sh
make dev     # http://localhost:3000
make check   # what CI runs
```

Commands, deployment and production tasks are in [AGENTS.md](AGENTS.md), which every coding agent here reads too. Codex users: mark the project as trusted, or Codex ignores `AGENTS.md`.

To get SSH access to the server, add an SSH key to your GitHub account (Settings → SSH and GPG keys) and ask someone with access to run `make prod-import-keys`.

## What's in this repo

- `backend/` and `frontend/`: the web app and its API, run with `make dev`.
- `my-glasses-app/`: a web app for Meta Ray-Ban Display glasses (see below).
- `glasses-android/`: an Android app that streams the Meta glasses camera; see [its README](glasses-android/README.md).
- `memory/`: the visual memory for the glasses; see [its README](memory/README.md).

## my-glasses-app

A web app for Meta Ray-Ban Display glasses, built with React, Vite and Meta's
UI Toolkit (`@wearables-ui-toolkit/mrbd`). It is currently a small demo, "Pinch
Counter", that shows how D-pad focus and pinch (select) work.

### Run it locally

Needs Node.js 20.19+ or 22.12+.

```sh
cd my-glasses-app
npm install
npm run dev
```

Open the printed address (usually http://localhost:5173) in Chrome or Brave.

### Test without glasses

Install the [Meta Ray-Ban Display Simulator](https://chromewebstore.google.com/detail/meta-ray-ban-display-simu/jpjlmmodokemlepklkdbimceggpbjcll)
Chrome extension, open the app, and click the extension icon to turn the
simulator on. Click inside the display, then use the arrow keys to move focus
and Enter to pinch.

### Other scripts

- `npm run typecheck`: TypeScript check
- `npm run build`: production build into `dist/`
- `npm run preview`: serve the production build

## Built before the event

Junction's rules require pre-existing work to be disclosed. Everything up to the git tag `pre-event-scaffold` was written before the event and contains no challenge-specific code:

- FastAPI backend with SQLModel on SQLite, a health endpoint and an example "notes" feature
- Next.js frontend with Tailwind and shadcn/ui, and a TypeScript client generated from the backend's OpenAPI schema
- Docker Compose stack with Caddy, a GitHub Actions workflow that checks and deploys to a Hetzner VM, and the VM's cloud-init setup
- Instructions and skills for coding agents (`AGENTS.md`, `.agents/skills/`)

Compare with `git diff pre-event-scaffold..HEAD` to see what was built during the event.
