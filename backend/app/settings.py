from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env may also hold keys that SDKs read straight from the environment.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No defaults on purpose: a missing setting stops the app at startup.
    database_path: Path


@lru_cache
def get_settings() -> Settings:
    return Settings()
