# glass_memory

Turns glasses video into memories (EmbeddingGemma 2 embeddings with their keyframes) and retrieves them for
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
- `ask` has two backends for Gemma (`GEMMA_MODEL`, by default `google/gemma-4-31B-it`), chosen
  by `GEMMA_URL`; `ask.served()` is that address or None. Unset: `ask.gemma()` loads the model
  and its processor into this process once (transformers) and keeps them on the GPU. Set: an
  OpenAI-style chat API there (vLLM) is sent the stills, with `GEMMA_API_KEY` as its bearer
  token; it stores and reads no image vectors, so `ask.store` does nothing and
  `ask.ask(..., vectors=True)` raises `ValueError`. The two do not fit on VM2's GPU together.
  To vLLM: add `GEMMA_URL=http://localhost:8000` to `/opt/glass-memory/.env`,
  `systemctl restart glass-memory`, then `docker start gemma` and wait for
  `curl localhost:8000/health`. Back: `docker stop gemma`, remove that line, restart the service.
  vLLM needs `--trust-request-mm-kwargs` for the summary's 70-token stills. `ask` never takes or
  returns those embeddings.
- `ask.store(keyframes)`: writes Gemma's own image vectors of each keyframe, at 70 image tokens,
  to `vectors/<name>.pt` beside `keyframes/` (`{"vectors", "size"}`); a keyframe that has them
  is skipped.
- `ask.ask(question, keyframes, times, vectors=False)`: Gemma's answer from those stills at its
  default 280 image tokens, or with `vectors` from the stored image vectors, with no still
  given to it (missing vectors are stored first).
- `ask.pooled(keyframes)`: one float32 row per keyframe, the mean of its stored image vectors
  over their tokens (missing vectors are stored first). `ValueError` when `GEMMA_URL` is set.
- `ask.summarise(keyframes, times)`: Gemma's one short paragraph on what happens, from up to 48
  evenly spaced keyframes at 70 image tokens each: their stored image vectors, or their stills
  when an API serves Gemma.

### HTTP API (`api.py`)

`uvicorn glass_memory.api:app --host 127.0.0.1 --port 8001`, started in the folder the batches
are kept under. Needs `GLASS_API_TOKEN` (32 characters or more, or the app does not start).
Gemma is loaded when the app starts, before it serves a request, unless `GEMMA_URL` is set.

- Auth: every path except `GET /` and `GET /health` needs
  `Authorization: Bearer <GLASS_API_TOKEN>`, else 401, decided before the body is read. There is
  no `/docs` or `/openapi.json`.
- A batch is `res/items/API-1/<batch>/` under the working directory: what `embed.store` writes
  plus `vectors/` (what `ask.store` writes), `summary.txt` and, once asked for,
  `vector_map.npy`, so `embed.load` reads it.
  `<batch>` is the UTC time of the post (`%Y%m%dT%H%M%S%f`), so ids sort by time and hold no
  `/`.
- `GET /health`: `{"status": "ok"}`.
- `GET /`: `web/index.html`, the map page. It takes the token from the link's fragment
  (`#token=...`, verbatim, not percent-decoded), sends it only in the `Authorization` header,
  and asks without `k`, with `vectors` from its tick box. A second tick box shows the map of
  Gemma's image vectors beside the embedding map, with the same colours and rings on both.
- `POST /batches`, multipart: `frames` = JPEG files in time order (repeated field, 1000 at
  most), `every_s` = seconds between them (default 1.0); frame i gets `t_s = i * every_s`.
  Answers `{"batch", "memories", "summary"}`. When Gemma fails the batch is stored all the same
  and `summary` is empty. 415 for a frame that is not a JPEG, 400 above 1000 frames, 422 for no
  frames or an `every_s` that is not a positive finite number.
- `GET /batches`: `[{"batch", "created", "memories", "summary"}]`, newest first; `created` is
  ISO 8601.
- `GET /batches/{batch}`: one such object. 404 for an unknown batch, here and below.
- `GET /batches/{batch}/map`: `[{"t_s", "x", "y", "keyframe"}]` in time order; `keyframe` is a
  bare file name. With `?vectors=true`, `x` and `y` are the memory's place on the map of Gemma's
  image vectors instead: `embed.layout` of `ask.pooled`, made on the first request (which stores
  any missing vectors) and kept in `vector_map.npy`. 400 for that when `GEMMA_URL` is set.
- `GET /batches/{batch}/keyframes/{name}`: that still, `image/jpeg`.
- `POST /batches/{batch}/ask`, JSON `{"question": str, "k": int = 4, "vectors": bool = false}`
  with `k` from 1 to 48: `{"answer", "keyframes"}`. The embeddings pick the `k` memories closest to the
  question, Gemma answers from their stills, or with `vectors` from its stored image vectors of
  them, and `keyframes` holds their bare file names; they are given and named in time order.
  400 for `vectors` when `GEMMA_URL` is set.
- `POST /v1/embeddings`, OpenAI-style JSON `{"model": "google/embeddinggemma-2", "input": str | [str]}`
  (1 to 16 strings): `{"object": "list", "data": [{"object", "index", "embedding"}], "model"}`,
  one 768-wide `embed.embed_text` vector per input, in order, for the app backend's search over
  the same stills. 400 for another `model`, 422 for no input.
- Client: `scripts/send_batch.py FRAMES [--every S]` posts a folder's `.jpg` files, in file name
  order, as one batch and prints the response. It reads `GLASS_API_URL` and `GLASS_API_TOKEN`
  from the environment.

## Rules

- One embedding model (`embed.MODEL`, EmbeddingGemma 2 with text and image encoders) for keyframes, object crops and questions; changing it invalidates every stored
  embedding.
- Embeddings are stored L2-normalised.

## Pointers

Terms in `GLOSSARY.md`, the idea in the project's `README.md`, what is left in `docs/todo.md`.
Deployment to VM2: `make memory-*` (the header of `deploy/deploy.sh` lists the commands).
