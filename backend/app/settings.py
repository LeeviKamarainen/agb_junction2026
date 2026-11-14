from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env may also hold keys that SDKs read straight from the environment.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No default on purpose: a missing database path stops the app at startup.
    database_path: Path

    # The chat model that answers questions with the vector_search tool: any OpenAI-style
    # /v1/chat/completions server with tool calling, e.g. Gemma on our vLLM or on Verda. Optional:
    # without it /api/ask answers 503 and the rest of the app still runs.
    llm_url: str | None = None  # base URL, without /v1
    llm_api_key: str | None = None
    llm_model: str = "google/gemma-4-31B-it"

    # Turns search queries into vectors through an OpenAI-style /v1/embeddings server. It must be
    # the model that embedded the stored memories (CLIP ViT-B/32 text tower for the images), or the
    # distances mean nothing. Optional: without it /api/search and /api/ask answer 503.
    embed_url: str | None = None  # base URL, without /v1
    embed_api_key: str | None = None
    embed_model: str = "ViT-B-32"


@lru_cache
def get_settings() -> Settings:
    return Settings()
