from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./jambachat.db"

    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v23.0"
    whatsapp_reminder_template: str = "lembrete"

    allowed_phones: Annotated[frozenset[str], NoDecode] = Field(default_factory=frozenset)
    admin_phones: Annotated[frozenset[str], NoDecode] = Field(default_factory=frozenset)
    default_timezone: str = "America/Sao_Paulo"

    anthropic_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-haiku-5-5"
    llm_max_tokens: int = 1024

    tavily_api_key: SecretStr = SecretStr("")

    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30

    @field_validator("allowed_phones", "admin_phones", mode="before")
    @classmethod
    def _split_phones(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(p.strip().lstrip("+") for p in value.split(",") if p.strip())
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
