# Decisions

Choices already made and why, so that nobody re-derives or silently reverses them. All dated
2026-11-14 unless marked. What is left to do: [todo.md](todo.md). Paths are relative to the
project folder (`memory/`).

Machines: **VM2** is the Gemma server (vLLM on port 8000, shared with the team) and also runs
this project's API. **VM1** is the GPU server that runs the segmenter (SAM 3).

## Direction

- **A memory of the whole scene, not an aimed lookup.** The project started as an AR lookup of
  the object at the centre of view. Aiming at the centre object never felt good, and a memory of
  everything seen needs no aiming.
- **No hard-coded object list.** "Was in a hand" marks an object as the wearer's own, and Gemma
  judges whether something is worth saying.

## Machines

- **Everything runs on VM2**, the embedding model beside Gemma. About 15 GB of GPU memory is free next to vLLM
  and the API holds 1,460 MiB of it. Nothing heavy runs on a laptop. VM1 is only needed for
  object detection.
- **The gemma container and port 8000 are never touched.** Teammates use them. The one
  exception so far: the container was recreated from the same image with the same arguments
  plus `--trust-request-mm-kwargs` (see Gemma requests).
- **Ask the owner first** before installing system packages, opening ports or downloading more
  than a few GB on VM2.
- **Secrets live only in `/opt/glass-memory/.env` on VM2** (`GEMMA_API_KEY`, `GLASS_API_TOKEN`),
  typed there by the owner. Never print, log or commit them. VM addresses stay out of the repo;
  `VM2_IP` is in `memory/.env`.

## Embeddings and storage

- **One embedding model for everything:** EmbeddingGemma 2 (`google/embeddinggemma-2`, text and
  image encoders, 768-wide; it replaced CLIP ViT-B/32 on 2026-11-14) for keyframes, object crops
  and questions, stored L2-normalised as float32, so cosine similarity is a dot
  product. Changing the model invalidates every stored embedding.
- **No vector database.** Search is a brute-force dot product. A vector is about 2 KB and a
  still about 100 KB, so an hour at one keyframe a second is roughly 7 MB of vectors and 360 MB
  of stills.
- **Gemma never reads these embeddings.** They only pick which stills to send.
- **Map coordinates are computed once, when a batch is stored** (UMAP on cosine distance, saved
  as `x` and `y` in the index), so every viewer draws the same map. Fewer than five memories get
  a straight line.

## Gemma requests

- **The Gemma 4 31B already on VM2 is used as it is**, through vLLM's OpenAI-style chat API.
  Stills go as base64 `image_url` parts. vLLM sets no cap on images per request for Gemma 4.
- **Token cost per still is a dial.** A still is 280 tokens by default and `max_soft_tokens`
  sets it per request (70 to 1120). The 32k context holds about 110 stills at 280 tokens.
- **The summary sends up to 48 evenly spaced stills at 70 tokens each.** Measured on the test
  video: 3,870 prompt tokens and 4.1 s, against 13,374 tokens and 6.9 s for the same 48 stills
  at the default size.
- **That needs vLLM started with `--trust-request-mm-kwargs`.** Without the flag vLLM answers
  400 to `mm_processor_kwargs` in a request. Every new start of vLLM needs it again.

## The API

The contract is in `src/glass_memory/README.md`. The choices behind it:

- **One request is one batch** (the stills of one video), stored in the layout `embed.build`
  writes plus `summary.txt`, so `embed.load` reads it unchanged.
- **It listens on 127.0.0.1:8001;** vLLM holds port 8000 and Caddy serves HTTPS to the outside:
  `/v1/chat/*` and `/v1/models` to vLLM (so the backend's `LLM_URL` is the same address), the
  rest to the API. Since 2026-11-14 VM2 runs vLLM with `GEMMA_URL` set, so no image vectors.

Made while writing the code, for the owner to keep or overrule:

- The token is checked in a middleware, before the body is read, so an unauthorised upload is
  never parsed or spooled to disk.
- A batch is stored with an empty summary when Gemma fails.
- A batch holds 1000 frames at most (the form parser's limit) and a question retrieves 48
  memories at most (as many stills as a summary sends).
- The batch id is the UTC time of the post, so ids sort by time.
- The service runs as a system user `glass`, which can write only its home and the batches
  folder.
- `setup` accepts only an unquoted token of 32 or more of `A-Za-z0-9_-`: systemd would strip
  quotes and the page's link would keep them.

## Deployment

- **Modelled on the segmenter** (`gpu-*` in the Makefile, `../segmenter/deploy/`): rsync
  to `/opt/glass-memory`, `uv sync --locked`, a systemd service, Caddy on
  `<ip-with-dashes>.sslip.io` and a bearer token.
- **The first deploy and `setup` ask for a typed yes,** because they install uv, about 3.3 GB of
  packages and Caddy, and open ports 80 and 443.

## The page

- **One static HTML file with no build step, served by the API at `/`,** the way the segmenter
  serves its webcam page.
- **The token travels in the URL fragment** (`#token=...`) and then only in the `Authorization`
  header, never in a query string. Stills are fetched with the header and shown from blob URLs,
  because an `<img>` tag cannot send it.
- **The marimo notebook stays** as the tool for exploring a batch on the machine itself; the
  page is the one to show and share.

## Gemma's own image vectors

Not yet run; these were read from the source.

- **The idea:** Gemma's vision encoder and projector turn each keyframe into the image vectors
  its language model reads, only those are stored, and the language model answers from them
  later. The pitch is that no viewable image exists, not storage or speed.
- **vLLM cannot do it.** It refuses pre-computed image vectors for every Gemma 4 model
  ([gemma4_mm.py](https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/models/gemma4_mm.py)
  asserts "Gemma4 does not support image_embeds"). So the target is the 31B run in transformers
  with vLLM stopped, tested first on E2B, which fits beside vLLM (11.4 GB at BF16 by
  [Google's table](https://ai.google.dev/gemma/docs/core)).
- **Each model reads only its own vectors:** E2B's are 1536 wide, the 31B's 5376.
- **The vectors go in through `generate(mm_encoder_outputs={"image": ...})`,** with the image
  placeholder tokens left in `input_ids`
  ([generation/utils.py](https://github.com/huggingface/transformers/blob/main/src/transformers/generation/utils.py),
  [modeling_gemma4.py](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma4/modeling_gemma4.py)).
- **All on one machine.** The vision encoder is about 550M of the 31B's parameters, so moving it
  elsewhere saves little work and sends more data than the JPEG.
- **The size is accepted for the demo.** With the 31B a still at 70 tokens is 70 × 5376 16-bit
  values, about 750 KB (about 2.7 GB an hour); at 280 tokens about 3 MB. Its JPEG is about
  100 KB.
