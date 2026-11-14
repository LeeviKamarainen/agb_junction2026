# glass-memory

Visual memory for smart glasses: everything the glasses see is embedded on our own GPU, shown as a
live memory map, and can be asked about through Gemma. A pivot from the AR "aim at the centre
object" lookup, whose aiming never felt good; a memory of the whole scene needs no aiming.

## Idea

1. **Embed.** Frames from the glasses go to VM2 (the Gemma server). One keyframe per second is
   saved as a still and embedded with CLIP, with its timestamp. Later there is one memory per
   tracked object instead of per frame.
2. **Memory map.** UMAP projects the embeddings to 2D. Each point is a memory, clusters form by
   place and kind of thing, colour is time. New points appear live, and a question lights up the
   memories it retrieves.
3. **Ask Gemma.** A question ("where did I leave my keys?") is embedded with the CLIP text
   encoder, the closest keyframes are retrieved, and those stills plus when they were seen go to
   Gemma, which answers. Gemma cannot read the vectors; they only pick the stills.
4. **Two-phase Gemma.** Gemma is split in two: its vision encoder turns each keyframe into
   the image vectors Gemma's language model reads, and only those are stored; later the
   language model answers from the stored vectors. No still is kept, and the number of
   vectors per still is a dial between detail and how many memories fit in one question.
5. **Proactive memory.** The glasses speak first. Leaving a place while something that was in
   your hand stays behind gives a nudge; re-entering a place reports what is missing, moved or
   new. No object list is hard-coded: "was in a hand" defines your things, and Gemma judges
   whether it is worth interrupting.

What is built and what is left: [docs/todo.md](docs/todo.md). Why it is built this way:
[docs/decisions.md](docs/decisions.md).

## Why it is worth showing

- Always-on video indexing is the workload that needs a GPU VM and not a phone.
- Meta's glasses ship explicit "remember this" and short Live AI sessions; the passive always-on
  memory was still a prototype in reports from July 2026.
- Everything runs on our own machines with an open model, so the footage never leaves them.

## Later ideas

- **Collective memory:** several wearers feed one memory ("has anyone seen the projector
  remote?"); their paths cross on the map.
- **Sleep consolidation:** an offline pass merges near-duplicate memories, Gemma writes
  summaries, unvisited memories fade; the map visibly compresses.

## Running

Everything here runs from this folder (`memory/`), except the `make` targets, which run from
the repo root. `uv sync` creates `.venv` (Python 3.14, torch and CLIP: about 6 GB).

- Checks: `make check-memory` (ruff and pytest; CLIP and Gemma are faked in the tests). CI does
  not run it, because it needs torch.
- Video to memories: `.venv/bin/python -m glass_memory.embed VIDEO` writes keyframes, embeddings
  and an index to `res/items/EMB-1/<video name>/`.
- Memory map notebook: `uv run --env-file .env marimo run notebooks/VIZ-1/map.py` (`.env` holds
  `GEMMA_API_KEY`; `GEMMA_URL` and `GEMMA_MODEL` are optional, see `src/glass_memory/ask.py`).
- Deploying the API to VM2: the `make memory-*` targets, listed in the header of
  `deploy/deploy.sh`. They read `VM2_IP` from `memory/.env`.
- Recorded clips and everything made from them go under `res/` (ignored by git). The folder
  names `EMB-1`, `API-1` and `VIZ-1` are kept from an earlier roadmap; the API's stored batches
  on VM2 live under `res/items/API-1/`.

Terms are in [GLOSSARY.md](GLOSSARY.md), the package's contracts in
[src/glass_memory/README.md](src/glass_memory/README.md), notebook rules in
[notebooks/README.md](notebooks/README.md).
