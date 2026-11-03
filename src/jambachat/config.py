from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

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

    public_base_url: str = ""

    max_audio_mb: int = 16
    max_image_mb: int = 5

    tavily_api_key: SecretStr = SecretStr("")

    pluggy_client_id: SecretStr = SecretStr("")
    pluggy_client_secret: SecretStr = SecretStr("")
    pluggy_base_url: str = "https://api.pluggy.ai"
    finance_import_enabled: bool = True

    monthly_summary: bool = True

    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30

    rate_limit_per_minute: int = Field(default=10, ge=1)
    rate_limit_per_day: int = Field(default=300, ge=1)
    max_input_chars: int = Field(default=2000, ge=1, le=4096)

    @field_validator("allowed_phones", "admin_phones", mode="before")
    @classmethod
    def _split_phones(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(p.strip().lstrip("+") for p in value.split(",") if p.strip())
        return value

    @field_validator("finance_import_enabled", mode="before")
    @classmethod
    def _empty_finance_import_enabled_uses_default(cls, value: object) -> object:
        return True if value == "" else value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def production_problems(self) -> list[str]:
        problems = [
            f"{name.upper()} não definida"
            for name, value in (
                ("whatsapp_app_secret", self.whatsapp_app_secret.get_secret_value()),
                ("whatsapp_verify_token", self.whatsapp_verify_token.get_secret_value()),
                ("whatsapp_access_token", self.whatsapp_access_token.get_secret_value()),
                ("whatsapp_phone_number_id", self.whatsapp_phone_number_id),
            )
            if not value
        ]
        if len(self.whatsapp_verify_token.get_secret_value()) < 16:
            problems.append("WHATSAPP_VERIFY_TOKEN precisa ter pelo menos 16 caracteres")
        if not self.allowed_phones:
            problems.append("ALLOWED_PHONES vazia: ninguém conseguiria usar o bot")
        db = urlsplit(self.database_url)
        if db.scheme.startswith("sqlite"):
            problems.append("DATABASE_URL usa sqlite; use postgres em produção")
        elif db.password in {None, "", "jambachat", "postgres", "password", "senha"}:
            problems.append("DATABASE_URL sem senha ou com senha padrão/fraca")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
