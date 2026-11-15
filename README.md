# Junction 2026: memory for Meta glasses

Our project for [Junction 2026](https://2026.hackjunction.com/) (13–15 November 2026, Espoo).

**We give smart glasses a memory.** What the Meta Ray-Ban Display glasses see is embedded into a vector database, so the wearer can ask about their own past, and the glasses can speak up on their own. It works in two ways:

- **Reactive:** you ask, the glasses answer. Double-tap the Neural Band (or hold the capture button) and say "where did I leave my keys?". The question is embedded, the closest stills are retrieved, and a Gemma model answers from those stills and when they were seen. You can also take a photo on purpose, which is stored as a memory.
- **Proactive:** the glasses speak first. While you wear them, frames stream to the backend (2 per second), and each is compared with things you asked to be warned about. Say "remind me to take my keys" or "warn me about the stool in the kitchen" and the glasses flash a warning when that object comes into view. You can also say what to watch out for in the web app ("tell me when my bus comes"): that is a *goal*, Gemma writes its *triggers*, and when a frame newly looks like one of them Gemma checks it and words the message for the display, or stays quiet on a false alarm. Goals are yours alone; ask Gemma to stop watching for one and it is removed.

Everything runs on our own servers with open models (EmbeddingGemma 2 for the embeddings, Gemma for the answers), so the footage never goes to a third party.

```
Meta glasses ──► Android app ──WebSocket──► backend (FastAPI + SQLite + sqlite-vec) ──► Memory API / Gemma server
 camera, mic,     (glasses-android)           memories, triggers, reminders, accounts     EmbeddingGemma 2 + Gemma (vLLM)
 Neural Band,                                           ▲
 display  ◄───────── messages and warnings ─────────────┤
                                        Next.js web app: live view, demos, Memory lab
```

## What's in this repo

| Folder | What it is |
|---|---|
| `backend/` | FastAPI backend (SQLModel on SQLite, vector search with sqlite-vec). Stores memories, runs the ask loop, checks frames against triggers and reminders, decides what the glasses display. All routes are under `/api`. |
| `frontend/` | Next.js web app: the **live view** (`/`), **Demos** (`/demos`), and for the admin the **Memory lab** (`/memory`) and the **glasses harness** (`/glasses`), plus the login pages. |
| `glasses-android/` | The real glasses client: an Android app that streams the Meta glasses' camera and sends frames, photos and voice to the backend; see [its README](glasses-android/README.md). |
| `memory/` | The visual memory service that runs on the Gemma server: embeds frames, serves the embeddings and chat, and draws the memory map; see [its README](memory/README.md). Its own uv project (about 6 GB with torch). |
| `deploy/`, `compose.yaml` | Production: Docker Compose with Caddy on a Hetzner VM. |

## Getting started

You need [uv](https://docs.astral.sh/uv/), Node.js 24 and npm, Docker, `make`, and the GitHub CLI (`gh`).

```sh
make dev     # backend on :8000, frontend on :3000
make check   # what CI runs
```

Open http://localhost:3000. API docs are at http://localhost:3000/api/docs.

Without any configuration the app runs and you can add memories and browse them, but asking questions needs the models. Put these in `backend/.env` (ask a teammate for the values):

```sh
LLM_URL=...          # chat model, any OpenAI-style server with tool calling (Gemma)
LLM_API_KEY=...
EMBED_URL=...        # embeddings server: must be EmbeddingGemma 2, the model the memories use
EMBED_API_KEY=...
ADMIN_PASSWORD=...   # optional: otherwise a random admin password is printed in the backend log
```

`LLM_URL` and `EMBED_URL` can both point to the memory API on the Gemma server (`https://<vm2-ip-with-dashes>.sslip.io`) with its token. `make reset-db` deletes the local database; it is rebuilt and re-seeded on the next start.

## How to use it

### Log in

Open http://localhost:3000 and start a **guest**: a generated name, no password, valid for 60 minutes, which sees the demo memories and the ones it adds. The admin sees every memory and logs in at `/admin` with `ADMIN_PASSWORD`, reached through the small settings icon on the login form. See [AGENTS.md](AGENTS.md) for how accounts and scoping work.

### The live view and curated demos

The home page is what the glasses see: it plays the published demo clips, or your own camera (on a phone, front or back). **Stream** sends 2 frames a second, as the glasses do, and each is checked against your triggers; a trigger's message appears on the view. Beside it, ask about the day by typing or holding **Talk**: answers are spoken and shown on the view too. Ask Gemma to warn you about something ("warn me when I see a bicycle") and the triggers for it appear below. **Fullscreen** fills the screen with the view and its controls (on an iPhone, the window). Double-tap the view to start or stop the stream.

Nobody needs an account: the first question or stream starts a 60-minute guest, which sees the demo memories and its own. Demo clips are only checked against the triggers; camera frames are also stored as the guest's memories.

Watch `/demos` without logging in. Published videos and ordered image sequences share one timeline, with chapter navigation, seeking, pause and mute controls.

At `/admin`, add a draft, upload one MP4/WebM video (up to 200 MB) or a batch of images (up to 100, 15 MB each, 200 MB per upload), then publish. Adjust image dwell time, reorder images and move demos earlier or later. Video length is detected in the browser and can be adjusted. Published media is public; drafts are visible only to the admin. Publishing a demo also makes it searchable: the backend stores a video's frames at 2 fps (skipping near-duplicates) and each image of a sequence as demo memories that everyone can ask about, faces blurred, and replaces or removes them when the demo changes or is unpublished. That needs the embedding model (`EMBED_URL`) and takes a while for a long video.

At `/admin/streams` the admin sees every client streaming right now (the live page, the glasses harness, the glasses app), each with its newest frame refreshing about twice a second and, under it, a log of the questions its user asked, the triggers and reminders they set, and what fired. Click a stream to open it in full, with fullscreen and a verbose backend log: every frame's result and trigger scores, Gemma's searches and answers, and errors, each with its JSON.

Media files live in `showcase-media/` beside the database. Schema or seed changes rebuild the disposable database and remove demo configurations; old files are not reassigned to new demos. The database starts empty; upload your own memories and curated media after a rebuild.

### Memory lab (`/memory`)

- **Library:** add memories by uploading images, taking a webcam picture, or writing a note; group them into collections. Browse stable thumbnails in pages of 24, search captions/collections, and tap a memory to inspect the full image.
- **Ask tab:** ask Gemma a question and watch it search the memories step by step.
- **Map:** every stored vector projected to 2D and 3D; the memories found by the last question are ringed.
- **Memory tab:** configure camera streaming, microphone questions, reminders and triggers in the shared glasses emulator. Say a goal and Gemma writes its triggers. Open the fullscreen emulator to use the same experience across the phone display.

### Glasses harness (`/glasses`)

Stands in for the glasses and the Android app, so the glasses UI can be built and tried without them: the webcam is the camera, the laptop mic is the microphone (hold Space or the talk button; Chrome only, and Chrome sends the audio to Google to transcribe), the speakers read replies aloud, and the arrow keys and Enter stand in for the Neural Band. You can also type what the wearer says.

It calls the same `/api/glasses/*` endpoints and WebSocket the Android app uses. The backend decides what to show, as layouts made of the glasses SDK's display components in `backend/app/glasses/screens.py`, and the harness draws them on a 600×600 display. "See-through" blends the display over the webcam the way the real display adds light to the world.

On a phone, open the HTTPS site at `/glasses`, rotate to landscape, and tap **Start camera**. The
feed comes from that phone, preferring the rear camera; **Switch camera** requests the other lens.
If the device has only one camera, the browser uses it. The feed appears behind the display by
default, and memory and reminder frames use the same stream. In landscape the POV fills the first viewport; scroll down for controls and account navigation.
Tap **Fullscreen** to hide browser chrome where supported, and **Controls** to jump below the POV. Portrait and desktop layouts are supported too. Use **Turn off** to
release the camera, or **Cancel camera** if the permission request is still waiting. Camera access
on a real phone requires HTTPS; an ordinary HTTP LAN address will not work.

Things to try: ask "where did I leave my keys?", say "remind me about the stool" and point the webcam at one.


### The real glasses

1. Pair the glasses in the Meta AI app, turn on Developer Mode, and build `glasses-android` in Android Studio (setup is in [its README](glasses-android/README.md)).
2. Copy `local.properties.example` to `local.properties` and set `glasses.api` to the backend's address (without `/api`). Without `glasses.token` the app starts a guest account by itself.
3. In the app press **Register**, then **Arm memory mode**. A double tap or double press of the capture button starts and stops the 2 fps stream; one tap or short press sends one photo; holding the capture button records a voice message, which is turned into text on the phone and sent to Gemma as a question.
4. Triggers and reminders now warn you on the display. Memories show up in the Memory lab.

The memory service itself (embedding, the map page, deploying to the Gemma server) is described in [memory/README.md](memory/README.md).

### Deploying

Every change goes through a pull request; what lands on `main` is deployed to the VM by GitHub Actions. Commands, the Git workflow, deployment and production tasks are in [AGENTS.md](AGENTS.md), which every coding agent here reads too. Codex users: mark the project as trusted, or Codex ignores `AGENTS.md`.

To get SSH access to the server, add an SSH key to your GitHub account (Settings → SSH and GPG keys) and ask someone with access to run `make prod-import-keys`.

## Live experience

The Memory tab in `/memory` configures the live experience: stream the camera at 2 fps, ask questions with the microphone, and manage goals and triggers. `/glasses` uses the same emulator in fullscreen, with Start memory stream and Ask with microphone controls over the view. Triggers configured in Memory belong to the logged-in account and run on its streamed frames. Camera and microphone access require HTTPS (or localhost); speech recognition depends on browser support.
