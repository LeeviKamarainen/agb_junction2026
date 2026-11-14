# To do

What is left, most urgent first. Each entry says where it stands, the next step and the check
that closes it. Why things are built the way they are: [decisions.md](decisions.md). The API's
contract: `src/glass_memory/README.md`. Terms: `GLOSSARY.md`. Paths are relative to the
project folder (`memory/`).

Machines: **VM2** is the Gemma server (vLLM on port 8000, shared with the team) and also runs
this project's API. **VM1** is the GPU server that runs the segmenter (SAM 3).

## What works

All checked on VM2 on 2026-11-14, on one 92.4 s test video.

- **Video to memories.** `python -m glass_memory.embed VIDEO` gave 91 memories (91 × 512
  float32, unit norm, with map coordinates) in 69 s, the weights download included. A second run
  gave a byte-identical `embeddings.npy`.
- **Gemma on stills.** `ask.ask` described three chosen keyframes correctly in 2.5 s.
  `ask.summarise` gave a fitting paragraph from 48 stills at 70 tokens each (3,870 prompt
  tokens) in 4.1 s.
- **The API as a service.** It runs under systemd as user `glass` behind Caddy with HTTPS;
  `/health` answers from outside and a missing or wrong token gives 401. The 91 keyframes posted
  as one batch came back as 91 memories with a summary in 9.9 s (16.2 s as the service's first
  request, which loads CLIP). The process holds 1,460 MiB of GPU memory beside vLLM.
- **A retrieved question.** "Where is the green watering can?" retrieved four stills and the
  three that were looked at all showed it; the answer fitted, in 1.6 s. This is one question on
  one video, not a measure of retrieval quality.

## Next

1. **Open the map page with the real batch** (owner). `make memory-link` prints its
   address with the token. So far the page has only been opened against faked CLIP and Gemma
   (30 synthetic stills in three colours). Done when: hovering three points shows stills that
   fit where the points sit, a question about something visible in the video highlights stills
   that show it, and the link opens on a phone.

2. **Frames from the glasses.** Nothing sends frames from the glasses yet. The only client is
   `scripts/send_batch.py`, which posts a folder of stills as one batch, and the API takes one
   whole batch per request (1000 frames at most). Next step: decide how the glasses app sends
   (one batch per recording, or frames added to a running batch). For a map that grows live, fit
   UMAP once on the first minutes and `transform` new points, because a refit reshuffles the
   whole map. Done when: a recording made on the glasses shows up on the page without copying
   files by hand.

3. **Memory map notebook** (`notebooks/VIZ-1/map.py`). Never run. It fits its own UMAP, so its
   map differs from the page's, and it lists only `res/items/EMB-1/`, not the API's batches in
   `res/items/API-1/`. Next step: read `x` and `y` from the index (`embed.load`), list both
   folders, then run it on VM2. Done when: the notebook shows the same map as the page for one
   batch. Skip this if the page is enough for the demo.

4. **Memories as Gemma's own image vectors** (`scripts/vec1_roundtrip.py`). Not run. Blocked on
   an open decision below: its last step needs vLLM stopped.
   1. Run the script on VM2 with the small model (E2B, about 12 GB, fits beside vLLM; run
      nothing else at the same time). It works if the answer from stored vectors matches the
      answer from the image and differs from the blank-image control.
   2. Same with `--tokens 70`: is the answer still good enough?
   3. Stop vLLM and run it with `--model google/gemma-4-31B-it`.
   4. Turn the two phases into the memory store: vectors per keyframe on disk, several stills
      per question.
   5. Compression: 16-bit against 8-bit values, skipping near-duplicate keyframes; report
      answer quality against size.

5. **Proactive memory.** Not started; the idea is part 5 of `README.md`. In order:
   1. **Object memories with a held-in-hand flag.** One memory per tracked object: its crop
      embedding, where and when it was last seen, whether it has been in a hand. Boxes per
      keyframe, each crop embedded with CLIP; crops linked into tracks by cosine similarity;
      hand detection (MediaPipe), and a track whose box overlapped a hand gets the flag. Next
      step: check whether the SAM 3 already running on VM1 can give the boxes (its API takes a
      point or a grid of points today) before picking another detector.
   2. **Nudge when something is left behind.** Cluster the scene embeddings into places; at a
      transition list the held objects last seen in the place being left; drop junk with a CLIP
      text-prompt score ("a personal item people carry with them"); give Gemma the rest as text
      (object, minutes since put down, place) for a yes or no and one sentence. At most one
      nudge per transition. Without Gemma: the score with a threshold and a fixed sentence.
   3. **Report what changed on re-entering a place.** Recognise the place from the scene
      embedding; match current object embeddings against the place's stored tracks (unmatched
      stored tracks are missing, unmatched current objects are new); Gemma phrases the
      difference in one sentence. The stage demo: a teammate removes an object and the glasses
      notice.

## Open decisions

For the owner and the team; do not settle these in code.

- **Stopping the shared Gemma.** Step 4.3 above stops vLLM, but teammates use that server and
  the lookups API (pull request 7) makes the production backend call it. Either
  agree a time window, or stop at the small model.
- **What the demo's headline is.** The image-vector memory (item 4) was called the headline, and
  later "not needed" for the two working pieces. It has not been run at all.
- **Aim lookup or memory.** This project is a pivot away from aiming at one object. The
  segmenter in this repo and the lookups API in pull request 7 are that aim lookup. Which one
  the glasses app uses on stage is not decided here.
- **The summary depends on a vLLM flag.** `--trust-request-mm-kwargs` was added when the gemma
  container was recreated. If someone restarts vLLM without it, `ask.summarise` gets a 400 and
  batches are stored with an empty summary.

## Later ideas

Collective memory and sleep consolidation: `README.md`.
