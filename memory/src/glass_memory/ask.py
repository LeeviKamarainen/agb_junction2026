"""Ask Gemma about keyframes, or for a summary of them. Two backends, chosen by GEMMA_URL:

- Unset: Gemma runs in this process (transformers), loaded once by `gemma()` and kept on the GPU.
  It answers from the stills, or from its own image vectors of them: its vision encoder turns a
  keyframe into vectors once, they are stored beside the keyframes, and the language model then
  answers from those without being given the still.
- Set (for vLLM on the same machine, http://localhost:8000): an OpenAI-style chat API serves
  Gemma and reads the stills. It needs GEMMA_API_KEY and cannot take image vectors.

GEMMA_MODEL (default google/gemma-4-31B-it) names the model for both.
"""

import base64
import json
import os
import threading
import urllib.request
from functools import cache
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForMultimodalLM, AutoProcessor
from transformers.modeling_outputs import BaseModelOutputWithPooling

INTRO = "These are stills from a person's smart glasses, in time order. Answer from them only."
SUMMARY = "In one short paragraph, say what happens in these stills."
TOKENS = 70  # image tokens per still in a summary and in stored vectors
PIXEL_KEYS = ("pixel_values", "image_position_ids")
_gpu = threading.Lock()  # one request on the model at a time


def served():
    """The address of the API that serves Gemma in place of this process, or None."""
    return os.environ.get("GEMMA_URL")


def _name():
    return os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it")


@cache
def gemma():
    model = AutoModelForMultimodalLM.from_pretrained(_name(), dtype="auto", device_map="auto")
    return model, AutoProcessor.from_pretrained(_name())


def _content(text, images, times):
    """The message: the intro, each image part with the time it was seen at, then `text`."""
    content = [{"type": "text", "text": INTRO}]
    for i, (image, t) in enumerate(zip(images, times, strict=True), start=1):
        content += [{"type": "text", "text": f"Still {i}, seen at {t:.0f} s:"}, image]
    return content + [{"type": "text", "text": text}]


def _served_answer(text, keyframes, times, tokens, max_tokens):
    """The reply of the API at GEMMA_URL, sent the stills themselves."""
    stills = [base64.b64encode(Path(k).read_bytes()).decode() for k in keyframes]
    images = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{s}"}} for s in stills
    ]
    body = {
        "model": _name(),
        "messages": [{"role": "user", "content": _content(text, images, times)}],
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        # vLLM takes this only when started with --trust-request-mm-kwargs.
        **({"mm_processor_kwargs": {"max_soft_tokens": tokens}} if tokens else {}),
    }
    request = urllib.request.Request(
        served() + "/v1/chat/completions",
        json.dumps(body).encode(),
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['GEMMA_API_KEY']}",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def _inputs(text, images, times, tokens):
    """The prompt for the model in this process. `tokens` is the image tokens per image; None is
    Gemma's own default, 280."""
    model, processor = gemma()
    images = [{"type": "image", "image": image} for image in images]
    return processor.apply_chat_template(
        [{"role": "user", "content": _content(text, images, times)}],
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
        enable_thinking=False,
        processor_kwargs={"max_soft_tokens": tokens} if tokens else {},
    ).to(model.device)


def _stored(keyframe):
    """A keyframe's image vectors and its size, from `vectors/<name>.pt` beside `keyframes/`;
    encoded and saved there the first time."""
    keyframe = Path(keyframe)
    path = keyframe.parent.parent / "vectors" / f"{keyframe.stem}.pt"
    if not path.exists():
        image = Image.open(keyframe).convert("RGB")
        inputs = _inputs("", [image], [0], TOKENS)
        features = gemma()[0].get_image_features(*(inputs[k] for k in PIXEL_KEYS), return_dict=True)
        path.parent.mkdir(exist_ok=True)
        torch.save({"vectors": features.pooler_output[0].cpu(), "size": image.size}, path)
    return torch.load(path)


@torch.no_grad()
def store(keyframes):
    """Store the image vectors of every keyframe that has none yet. Nothing to do when an API
    serves Gemma."""
    if served():
        return
    with _gpu:
        for keyframe in keyframes:
            _stored(keyframe)


@torch.no_grad()
def pooled(keyframes):
    """One float32 row per keyframe: the mean of its stored image vectors over their tokens. A
    keyframe that has no vectors yet gets them stored first."""
    if served():
        raise ValueError("the API at GEMMA_URL stores no image vectors; unset it to read them")
    with _gpu:
        return torch.stack(
            [_stored(keyframe)["vectors"].float().mean(0) for keyframe in keyframes]
        ).numpy()


@torch.no_grad()
def _answer(text, keyframes, times, vectors, tokens, max_tokens):
    """Gemma's reply. In this process it reads the stored vectors when `vectors`, else the stills
    at `tokens` image tokens each; an API reads the stills."""
    if served():
        if vectors:
            raise ValueError(
                "the API at GEMMA_URL reads stills only; unset it to answer from image vectors"
            )
        return _served_answer(text, keyframes, times, tokens, max_tokens)
    model, processor = gemma()
    with _gpu:
        if vectors:
            stored = [_stored(keyframe) for keyframe in keyframes]
            # Blank images of the stills' sizes only put the right number of placeholders in the
            # prompt.
            inputs = _inputs(text, [Image.new("RGB", s["size"]) for s in stored], times, TOKENS)
            given = [s["vectors"].to(model.device) for s in stored]
            inputs = {k: v for k, v in inputs.items() if k not in PIXEL_KEYS}
            inputs["mm_encoder_outputs"] = {
                "image": BaseModelOutputWithPooling(pooler_output=given)
            }
        else:
            inputs = _inputs(text, [Image.open(k).convert("RGB") for k in keyframes], times, tokens)
        out = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    return processor.decode(
        out[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True
    ).strip()


def ask(question, keyframes, times, vectors=False, max_tokens=300):
    """Gemma's answer to `question`, given keyframe paths and the seconds each was seen at. It
    reads the stills, or with `vectors` its stored image vectors of them."""
    return _answer(question, keyframes, times, vectors, None, max_tokens)


def summarise(keyframes, times, max_tokens=200):
    """One short paragraph on what happens, from up to 48 evenly spaced keyframes at 70 image
    tokens each: their stored vectors, or their stills when an API serves Gemma."""
    n = min(len(keyframes), 48)
    picked = [i * len(keyframes) // n for i in range(n)]
    return _answer(
        SUMMARY,
        [keyframes[i] for i in picked],
        [times[i] for i in picked],
        not served(),
        TOKENS,
        max_tokens,
    )
