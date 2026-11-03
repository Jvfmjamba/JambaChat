import base64
import binascii
import hashlib
import hmac
import os
from collections.abc import Sequence
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jambachat.config import get_settings

FORMAT_VERSION = "v1"
NONCE_SIZE = 12
KEY_SIZE = 32


class CryptoError(Exception):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(KEY_SIZE)).decode()


def decode_key(raw: str) -> bytes:
    raw = raw.strip()
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("chave em base64 inválida") from exc
    if len(key) != KEY_SIZE:
        raise CryptoError(f"a chave precisa ter {KEY_SIZE} bytes")
    return key


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


class Cipher:
    def __init__(self, keys: Sequence[bytes], index_key: bytes) -> None:
        if not keys:
            raise CryptoError("nenhuma chave de criptografia configurada")
        self._primary_id = key_id(keys[0])
        self._aeads = {key_id(k): AESGCM(k) for k in keys}
        self._index_key = index_key

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(NONCE_SIZE)
        aad = self._primary_id.encode()
        sealed = self._aeads[self._primary_id].encrypt(nonce, plaintext.encode(), aad)
        payload = base64.urlsafe_b64encode(nonce + sealed).decode()
        return f"{FORMAT_VERSION}.{self._primary_id}.{payload}"

    def decrypt(self, token: str) -> str:
        try:
            version, kid, payload = token.split(".", 2)
            if version != FORMAT_VERSION:
                raise CryptoError("versão de token desconhecida")
            raw = base64.urlsafe_b64decode(payload)
            plain = self._aeads[kid].decrypt(raw[:NONCE_SIZE], raw[NONCE_SIZE:], kid.encode())
            return plain.decode()
        except CryptoError:
            raise
        except (ValueError, KeyError, InvalidTag) as exc:
            raise CryptoError("não foi possível descriptografar o valor") from exc

    def needs_rotation(self, token: str) -> bool:
        return token.split(".", 2)[1] != self._primary_id

    def blind_index(self, value: str) -> str:
        return hmac.new(self._index_key, value.encode(), hashlib.sha256).hexdigest()


@lru_cache
def get_cipher() -> Cipher:
    settings = get_settings()
    raw_keys = [k for k in settings.encryption_keys.get_secret_value().split(",") if k.strip()]
    raw_index = settings.index_key.get_secret_value()
    if not raw_keys or not raw_index:
        raise CryptoError(
            "defina ENCRYPTION_KEYS e INDEX_KEY; gere com: python -m jambachat.security.keygen"
        )
    return Cipher([decode_key(k) for k in raw_keys], decode_key(raw_index))
