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

- `backend/` and `frontend/`: the web app and its API, run with `make dev`. The glasses harness lives here too: http://localhost:3000/glasses (see below).
- `my-glasses-app/`: a web app for Meta Ray-Ban Display glasses (see below).
- `glasses-android/`: an Android app that streams the Meta glasses camera; see [its README](glasses-android/README.md).
- `memory/`: the visual memory for the glasses; see [its README](memory/README.md).

## Glasses harness

http://localhost:3000/glasses (after `make dev`) stands in for the glasses and the Android app, so the glasses UI can be built without them: the webcam is the camera, the laptop mic is the microphone (hold Space or the talk button; Chrome only, and Chrome sends the audio to Google to transcribe), the speakers read replies aloud, and the arrow keys and Enter stand in for the Neural Band. You can also type what the wearer says.

On a phone, open the HTTPS site at `/glasses`, rotate to landscape, and tap **Start camera**. The
feed comes from that phone, preferring the rear camera; **Switch camera** requests the other lens.
If the device has only one camera, the browser uses it. The feed appears behind the display by
default, and captures and reminder frames use the same stream. The display stays in view while the
controls scroll beside it. Portrait and desktop layouts are supported too. Use **Turn off** to
release the camera, or **Cancel camera** if the permission request is still waiting. Camera access
on a real phone requires HTTPS; an ordinary HTTP LAN address will not work.

The login form has a small settings icon for the password-protected `/admin` form.

It calls the same `/api/glasses/*` endpoints the Android app will. The backend decides what to show, as layouts made of the SDK's display components (FlexBox, Text, Image, Icon, Button) in `backend/app/glasses/screens.py`, and the harness draws them on a 600×600 display. "See-through" blends the display over the webcam the way the glasses' display adds light to the world.

With `LLM_URL` set, a photo is described by the chat model ("what am I looking at?" takes one); with `EMBED_URL` too, it becomes an image memory captioned with that description, so it shows in the Memory lab and questions such as "where did I leave my keys?" find it through the same loop as `/api/ask`. Without them the harness still takes and shows photos.

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
