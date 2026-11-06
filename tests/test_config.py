import pytest

from jambachat.config import Settings


def test_phone_lists_are_split_and_normalized() -> None:
    settings = Settings(allowed_phones="+5511999990000, 5521988887777 ,", _env_file=None)

    assert settings.allowed_phones == frozenset({"5511999990000", "5521988887777"})


def test_empty_phone_list() -> None:
    settings = Settings(allowed_phones="", _env_file=None)

    assert settings.allowed_phones == frozenset()


def test_finance_is_disabled_by_missing_credentials() -> None:
    settings = Settings(_env_file=None)

    assert settings.pluggy_client_id.get_secret_value() == ""
    assert settings.pluggy_client_secret.get_secret_value() == ""
    assert settings.pluggy_base_url == "https://api.pluggy.ai"
    assert settings.finance_import_enabled is True
    assert Settings(_env_file=None, finance_import_enabled="").finance_import_enabled is True


def test_pluggy_base_url_requires_https() -> None:
    assert Settings(_env_file=None, pluggy_base_url="").pluggy_base_url == "https://api.pluggy.ai"

    with pytest.raises(ValueError, match="PLUGGY_BASE_URL deve começar com https://"):
        Settings(_env_file=None, pluggy_base_url="http://api.pluggy.ai")


def test_production_problems_flag_insecure_setups() -> None:
    insecure = Settings(_env_file=None, app_env="production")
    problems = " ".join(insecure.production_problems())

    for expected in ("WHATSAPP_APP_SECRET", "ALLOWED_PHONES", "sqlite", "16 caracteres"):
        assert expected in problems

    weak_db = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jambachat:jambachat@db:5432/jambachat",
    )
    assert any("senha" in p for p in weak_db.production_problems())

    secure = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://app:Xk29fj3lQp0zR7@db:5432/jambachat",
        whatsapp_app_secret="s",
        whatsapp_verify_token="v" * 32,
        whatsapp_access_token="a",
        whatsapp_phone_number_id="1",
        allowed_phones="5511999990000",
    )
    assert secure.production_problems() == []
