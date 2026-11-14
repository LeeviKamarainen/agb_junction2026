"""Turns what the wearer said into an intent, with plain keyword rules.

Rules keep the demo predictable. To let Gemma interpret instead, replace `interpret` and keep its
return type; the routes only see the `Intent`.
"""

import re
from dataclasses import dataclass
from typing import Literal

IntentName = Literal["home", "help", "take_photo", "gallery", "ask_memory", "unknown"]

# Button actions are intent names, so pressing "Photo" and saying "take a photo" do the same.
BUTTON_ACTIONS: set[str] = {"home", "help", "take_photo", "gallery"}

_QUESTION_START = re.compile(
    r"^(where|what|when|who|which|why|how|did|do|does|have|has|is|are|was|were|can you find)\b"
)


@dataclass(frozen=True)
class Intent:
    name: IntentName
    # The question for ask_memory, the words heard for unknown, else empty.
    text: str = ""


def _has(words: str, *phrases: str) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", words) for phrase in phrases)


def interpret(text: str) -> Intent:
    spoken = text.strip()
    words = re.sub(r"[^\w\s']", " ", spoken.lower())
    words = re.sub(r"\s+", " ", words).strip()
    if not words:
        return Intent("unknown", spoken)
    if _has(words, "help", "what can i say", "what can you do"):
        return Intent("help")
    if _has(words, "home", "menu", "go back", "cancel", "never mind", "nevermind"):
        return Intent("home")
    # Before take_photo: "show my photos" mentions photos but asks to see them.
    if _has(
        words,
        "gallery",
        "show photos",
        "show my photos",
        "my photos",
        "last photo",
        "show pictures",
        "my pictures",
    ):
        return Intent("gallery")
    # Describing what is in front of the wearer starts with a photo; the upload describes it.
    if _has(
        words, "what am i looking at", "what do you see", "describe", "what is this", "what's this"
    ):
        return Intent("take_photo")
    if _has(words, "photo", "picture", "capture", "snap", "take a pic", "shoot"):
        return Intent("take_photo")
    if spoken.endswith("?") or _QUESTION_START.match(words):
        return Intent("ask_memory", spoken)
    return Intent("unknown", spoken)
