from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./jambachat.db"

    encryption_keys: SecretStr = SecretStr("")
    index_key: SecretStr = SecretStr("")

    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v23.0"
    whatsapp_reminder_template: str = "lembrete"

    allowed_phones: Annotated[frozenset[str], NoDecode] = Field(default_factory=frozenset)
    admin_phones: Annotated[frozenset[str], NoDecode] = Field(default_factory=frozenset)
    default_timezone: str = "America/Sao_Paulo"
    send_welcome: bool = True

    llm_provider: Literal["openai_compatible", "anthropic"] = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "qwen3:8b"
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 60.0

    llm_vision: bool = False

    stt_base_url: str = ""
    stt_api_key: SecretStr = SecretStr("")
    stt_model: str = "whisper-large-v3-turbo"

    max_audio_mb: int = 16
    max_image_mb: int = 5

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
