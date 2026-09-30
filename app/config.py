"""Environment settings. Secrets stay in .env, not in code."""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    discord_application_id: str = ""
    discord_public_key: str = ""
    discord_bot_token: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/signalpeak"
    admin_api_key: str = ""
    approval_role_id: str = ""
    log_unmatched_messages: bool = False
    log_level: str = "INFO"
    source_server_id: str = ""
    destination_server_id: str = ""

    @model_validator(mode="after")
    def normalize_database_url(self) -> "Settings":
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        self.database_url = url
        return self

    @property
    def approval_role_snowflake(self) -> int | None:
        return _snowflake(self.approval_role_id)

    @property
    def source_server_snowflake(self) -> int | None:
        return _snowflake(self.source_server_id)

    @property
    def destination_server_snowflake(self) -> int | None:
        return _snowflake(self.destination_server_id)


def _snowflake(value: str) -> int | None:
    text = value.strip()
    if text.isdigit():
        return int(text)
    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
