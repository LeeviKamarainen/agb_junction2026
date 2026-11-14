# glass_memory

Turns glasses video into memories (CLIP embeddings with their keyframes) and retrieves them for
the memory map and for Gemma. Contracts, rules and pointers only, no history.

## Contracts

- `embed.build(video, out_dir)`: writes `keyframes/*.jpg` (one upright still a second),
  `embeddings.npy` (one L2-normalised float32 row per keyframe) and `index.parquet` (`t_s`,
  `keyframe`, `x`, `y`), all in the same row order. `x` and `y` are the memory's place on the
  map (`embed.layout`: UMAP on cosine distance, a straight line for fewer than five memories).
  `embed.load(out_dir)` reads them back with full keyframe paths. CLI:
  `python -m glass_memory.embed VIDEO`.
- `embed.store(frames, out_dir)`: the same files for stills already in `out_dir/keyframes/`,
  given as `(t_s, file name)` pairs in time order. `build` is `keyframes` then `store`.
- `embed.embed_text(question)`: one vector in the same space, so `embeddings @ vector` is the
  cosine similarity.
- `ask.ask(question, keyframes, times)`: Gemma's answer from those stills. Needs
  `GEMMA_API_KEY` in the environment; never takes or returns embeddings.
- `ask.summarise(keyframes, times)`: Gemma's one short paragraph on what happens, from up to 48
  evenly spaced stills at 70 image tokens each. Same environment as `ask.ask`.

### HTTP API (`api.py`)

`uvicorn glass_memory.api:app --host 127.0.0.1 --port 8001`, started in the folder the batches
are kept under. Needs `GLASS_API_TOKEN` (32 characters or more, or the app does not start) and
what `ask` reads.

- Auth: every path except `GET /` and `GET /health` needs
  `Authorization: Bearer <GLASS_API_TOKEN>`, else 401, decided before the body is read. There is
  no `/docs` or `/openapi.json`.
- A batch is `res/items/API-1/<batch>/` under the working directory: what `embed.store` writes
  plus `summary.txt`, so `embed.load` reads it. `<batch>` is the UTC time of the post
  (`%Y%m%dT%H%M%S%f`), so ids sort by time and hold no `/`.
- `GET /health`: `{"status": "ok"}`.
- `GET /`: `web/index.html`, the map page. It takes the token from the link's fragment
  (`#token=...`, verbatim, not percent-decoded), sends it only in the `Authorization` header,
  and asks without `k`.
- `POST /batches`, multipart: `frames` = JPEG files in time order (repeated field, 1000 at
  most), `every_s` = seconds between them (default 1.0); frame i gets `t_s = i * every_s`.
  Answers `{"batch", "memories", "summary"}`. When Gemma fails the batch is stored all the same
  and `summary` is empty. 415 for a frame that is not a JPEG, 400 above 1000 frames, 422 for no
  frames or an `every_s` that is not a positive finite number.
- `GET /batches`: `[{"batch", "created", "memories", "summary"}]`, newest first; `created` is
  ISO 8601.
- `GET /batches/{batch}`: one such object. 404 for an unknown batch, here and below.
- `GET /batches/{batch}/map`: `[{"t_s", "x", "y", "keyframe"}]` in time order; `keyframe` is a
  bare file name.
- `GET /batches/{batch}/keyframes/{name}`: that still, `image/jpeg`.
- `POST /batches/{batch}/ask`, JSON `{"question": str, "k": int = 4}` with `k` from 1 to 48:
  `{"answer", "keyframes"}`. CLIP picks the `k` memories closest to the question, Gemma answers
  from their stills, and `keyframes` holds their bare file names; stills are sent and named in
  time order.
- Client: `scripts/send_batch.py FRAMES [--every S]` posts a folder's `.jpg` files, in file name
  order, as one batch and prints the response. It reads `GLASS_API_URL` and `GLASS_API_TOKEN`
  from the environment.

## Rules

- One CLIP model for keyframes, object crops and questions; changing it invalidates every stored
  embedding.
- Embeddings are stored L2-normalised.

## Pointers

Terms in `GLOSSARY.md`, the idea in the project's `README.md`, what is left in `docs/todo.md`.
Deployment to VM2: `make memory-*` (the header of `deploy/deploy.sh` lists the commands).
