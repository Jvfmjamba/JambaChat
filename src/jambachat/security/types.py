import json
from typing import Any

from sqlalchemy import Dialect, Text
from sqlalchemy.types import TypeDecorator

from jambachat.security.crypto import get_cipher


class EncryptedText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().decrypt(value)


class EncryptedJSON(TypeDecorator[dict[str, Any]]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: dict[str, Any] | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return get_cipher().encrypt(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def process_result_value(self, value: str | None, dialect: Dialect) -> dict[str, Any] | None:
        if value is None:
            return None
        decoded: dict[str, Any] = json.loads(get_cipher().decrypt(value))
        return decoded
