# What the backend needs from the Gemma server

The backend's memory search (`/api/ask`, see `backend/app/agent.py`) needs two OpenAI-compatible
endpoints: **chat completions with tool calling** for Gemma, and **text embeddings** that land in
the same 512-wide CLIP space as the stored images, for both text queries and uploaded images. The vector database, the search and the
question loop all run on the backend (VM1); the server only answers these two kinds of request.

## How a question flows

1. A user asks the backend a question, e.g. "Where did I leave my keys?".
2. The backend sends Gemma the question and the `vector_search` tool definition (**chat**).
3. Gemma answers with a tool call: `vector_search` and its arguments as JSON.
4. The backend embeds `user_query` and each `alternative_queries` entry (**embeddings**), searches
   its SQLite vector table and matches the keywords.
5. The backend sends the conversation back to Gemma, now with the results as a `tool` message and
   the stills found as images (**chat** again). Gemma may search again, up to 3 times.
6. On the 4th request the tool is left out, so Gemma has to answer in plain text.

## Endpoint 1: `POST /v1/chat/completions` with tool calling

### First request: question and tool

```json
{
  "model": "google/gemma-4-31B-it",
  "messages": [
    {"role": "system", "content": "You answer questions about what the user has seen ... The current time is 2026-11-14T12:00:00+00:00 (UTC)."},
    {"role": "user", "content": "Where did I leave my keys?"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "vector_search",
        "description": "Search the user's memories: stills seen through their smart glasses and text notes ...",
        "parameters": {
          "type": "object",
          "properties": {
            "user_query": {"type": "string"},
            "alternative_queries": {"type": "array", "items": {"type": "string"}},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "timespan": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}},
            "kinds": {"type": "array", "items": {"type": "string", "enum": ["image", "text"]}},
            "limit": {"type": "integer"}
          },
          "required": ["user_query"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "max_tokens": 800,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

The full tool definition, with every description, is `VECTOR_SEARCH_TOOL` in
`backend/app/search.py`.

### Expected response: a structured tool call

The tool call must arrive **parsed into `tool_calls`**, not as text inside `content`:

```json
{
  "choices": [{
    "finish_reason": "tool_calls",
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "chatcmpl-tool-1a2b",
        "type": "function",
        "function": {
          "name": "vector_search",
          "arguments": "{\"user_query\": \"car keys on a table\", \"alternative_queries\": [\"keys in a hand\"], \"keywords\": [\"keys\"], \"timespan\": {\"start\": \"2026-11-14T06:00:00Z\"}}"
        }
      }]
    }
  }]
}
```

- `arguments` is a JSON string (an object also works on our side).
- Several tool calls in one message are fine.
- If Gemma answers straight away, `content` holds the answer and `tool_calls` is empty or missing.

### Follow-up requests: the history must be accepted

The next request repeats the messages and adds three kinds the chat template has to accept:

```json
[
  {"role": "assistant", "content": "", "tool_calls": [ ...exactly as returned... ]},
  {"role": "tool", "tool_call_id": "chatcmpl-tool-1a2b",
   "content": "[{\"id\": 12, \"kind\": \"image\", \"seen_at\": \"2026-11-14T09:00:00+00:00\", \"text\": \"\", \"image_attached\": true}]"},
  {"role": "user", "content": [
    {"type": "text", "text": "The images found:"},
    {"type": "text", "text": "Memory 12, seen at 2026-11-14T09:00:00+00:00:"},
    {"type": "image_url", "image_url": {"url": "https://.../12.jpg"}}
  ]}
]
```

- A `user` message with images comes **directly after `tool` messages**, so the template must
  allow that order.
- Up to **16 images** per request, always as `data:image/jpeg;base64,...` URLs, at most 1024 px
  on a side. The server never has to fetch anything.
- A request is at most about 16 stills × 280 tokens plus text, roughly 6k tokens, well within 32k.
- The last request has no `tools` field and must get a plain-text `content` back.

## Server settings that make tool calling work (vLLM)

- `--enable-auto-tool-choice`, required for `"tool_choice": "auto"`. Without it vLLM answers 400.
- `--tool-call-parser` set to vLLM's parser for Gemma 4. `vllm serve --help` lists the choices;
  recent versions call it `gemma4`.
- A chat template that renders `tools`, assistant `tool_calls` and `tool` messages. Gemma 4's own
  template does; pass `--chat-template` only if the installed one does not.
- Keep `--trust-request-mm-kwargs`: the memory API's summary sends `mm_processor_kwargs`
  (decisions.md). vLLM is shared with the team, so a restart needs the same flags plus the new
  ones.
- `chat_template_kwargs: {"enable_thinking": false}` must keep working. Because we send it, no
  reasoning parser is needed for us.

If the model runs on **Verda** instead, the same request and response formats apply; we only
need its base URL, API key and model name.

## Endpoint 2: `POST /v1/embeddings` for text and images

### Request

```json
{"model": "ViT-B-32", "input": ["car keys on a table", "keys in a hand"]}
```

- `input` is a list of 1 to 6 strings. A search sends short texts (the query plus up to 5
  alternatives).
- **Images arrive in the same field** as `data:image/jpeg;base64,...` strings, one per request,
  when someone uploads a memory on the backend's Memory lab page. Any input that starts with
  `data:image/` must go through the CLIP **image** tower; everything else through the text tower.
  (This is our convention: the OpenAI API has no image embeddings.)

### Response

```json
{
  "object": "list",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456, "... 512 floats"]},
    {"object": "embedding", "index": 1, "embedding": ["... 512 floats"]}
  ],
  "model": "ViT-B-32"
}
```

### Requirements

- **One model for both:** open_clip `ViT-B-32` with `laion2b_s34b_b79k`, text tower for texts
  and image tower for images (`glass_memory.embed`). With different models the distances between
  a question and an image mean nothing.
- 512 float32 values per input, L2-normalised (cosine distance makes that optional but cheap).
- One `data` entry per input, each with its `index`.
- Under about 1 s for 6 texts or one image. Request bodies with an image are up to about 1 MB.

vLLM cannot serve open_clip, so this is best as a small route in the memory API, which already
loads CLIP: for example `POST /v1/embeddings` behind the existing Caddy and bearer token. The
backend appends `/v1/embeddings` to the base URL it is given.

## Access and our settings

The backend runs on VM1 (the Hetzner app VM) and calls both endpoints over the internet, with a
120 s timeout. Each endpoint needs an HTTPS address that VM1 can reach and a bearer token
(`Authorization: Bearer <key>`). We set these as GitHub secrets and variables, which become the
backend's environment:

| Setting | Value we need from you | Example |
| --- | --- | --- |
| `LLM_URL` | Chat base URL, without `/v1` | `https://<vm2-ip-with-dashes>.sslip.io` |
| `LLM_API_KEY` | vLLM API key | secret |
| `LLM_MODEL` | Served model name | `google/gemma-4-31B-it` |
| `EMBED_URL` | Embeddings base URL, without `/v1` | `https://<vm2-ip-with-dashes>.sslip.io` |
| `EMBED_API_KEY` | Token for the embeddings route | secret |
| `EMBED_MODEL` | Model name sent in the request | `ViT-B-32` |

## Checks once it is set up

A tool call comes back structured (`finish_reason` is `tool_calls` and `tool_calls` is filled):

```bash
curl -s "$LLM_URL/v1/chat/completions" -H "Authorization: Bearer $LLM_API_KEY" -H "Content-Type: application/json" -d '{"model":"google/gemma-4-31B-it","messages":[{"role":"user","content":"Search my memories for my keys."}],"tools":[{"type":"function","function":{"name":"vector_search","description":"Search the user memories","parameters":{"type":"object","properties":{"user_query":{"type":"string"}},"required":["user_query"]}}}],"tool_choice":"auto","chat_template_kwargs":{"enable_thinking":false}}'
```

Embeddings come back 512 wide, for a text and for an image:

```bash
curl -s "$EMBED_URL/v1/embeddings" -H "Authorization: Bearer $EMBED_API_KEY" -H "Content-Type: application/json" -d '{"model":"ViT-B-32","input":["car keys on a table"]}'
```

```bash
curl -s "$EMBED_URL/v1/embeddings" -H "Authorization: Bearer $EMBED_API_KEY" -H "Content-Type: application/json" -d "{\"model\":\"ViT-B-32\",\"input\":[\"data:image/jpeg;base64,$(base64 -w0 still.jpg)\"]}"
```

The whole loop: open `/memory` on the web app, upload a few stills, then ask about them.

## Open questions

- Is vLLM's port 8000 reachable from VM1 over HTTPS, or should Caddy proxy a path to it?
- Who restarts the shared vLLM with the tool-calling flags, and when?
- Should the glasses' stills reach our database through the memory API, or straight from the
  glasses to the backend's upload route?
