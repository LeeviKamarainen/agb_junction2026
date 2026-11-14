"""Ask Gemma about keyframes, or for a summary of them, through vLLM's OpenAI-style chat API.

Reads GEMMA_API_KEY, and optionally GEMMA_URL (default http://localhost:8000) and GEMMA_MODEL
(default google/gemma-4-31B-it), from the environment.
"""

import base64
import json
import os
import urllib.request
from pathlib import Path

INTRO = "These are stills from a person's smart glasses, in time order. Answer from them only."
SUMMARY = "In one short paragraph, say what happens in these stills."


def _chat(text, keyframes, times, max_tokens, **extra):
    """Gemma's reply to one request: the intro, each still with its time, then `text`."""
    content = [{"type": "text", "text": INTRO}]
    for i, (path, t) in enumerate(zip(keyframes, times, strict=True), start=1):
        data = base64.b64encode(Path(path).read_bytes()).decode()
        content.append({"type": "text", "text": f"Still {i}, seen at {t:.0f} s:"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}
        )
    content.append({"type": "text", "text": text})
    body = {
        "model": os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it"),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        **extra,
    }
    request = urllib.request.Request(
        os.environ.get("GEMMA_URL", "http://localhost:8000") + "/v1/chat/completions",
        json.dumps(body).encode(),
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['GEMMA_API_KEY']}",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def ask(question, keyframes, times, max_tokens=300):
    """Gemma's answer to `question`, given keyframe paths and the seconds each was seen at."""
    return _chat(question, keyframes, times, max_tokens)


def summarise(keyframes, times, max_tokens=200):
    """One short paragraph on what happens, from up to 48 evenly spaced stills at 70 tokens each."""
    n = min(len(keyframes), 48)
    picked = [i * len(keyframes) // n for i in range(n)]
    return _chat(
        SUMMARY,
        [keyframes[i] for i in picked],
        [times[i] for i in picked],
        max_tokens,
        mm_processor_kwargs={"max_soft_tokens": 70},
    )
