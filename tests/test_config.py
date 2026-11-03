from jambachat.config import Settings


def test_phone_lists_are_split_and_normalized() -> None:
    settings = Settings(allowed_phones="+5511999990000, 5521988887777 ,", _env_file=None)

    assert settings.allowed_phones == frozenset({"5511999990000", "5521988887777"})


def test_empty_phone_list() -> None:
    settings = Settings(allowed_phones="", _env_file=None)

    assert settings.allowed_phones == frozenset()
