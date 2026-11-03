import os

import pytest

from jambachat.security.crypto import Cipher, CryptoError, decode_key, generate_key, key_id

OLD = os.urandom(32)
NEW = os.urandom(32)
INDEX = os.urandom(32)


def test_roundtrip_and_ciphertext_hides_plaintext() -> None:
    cipher = Cipher([NEW], INDEX)
    token = cipher.encrypt("pagar fatura do cartão")

    assert "fatura" not in token
    assert cipher.decrypt(token) == "pagar fatura do cartão"


def test_same_plaintext_produces_different_tokens() -> None:
    cipher = Cipher([NEW], INDEX)

    assert cipher.encrypt("x") != cipher.encrypt("x")


def test_rotation_keeps_old_tokens_readable() -> None:
    old_token = Cipher([OLD], INDEX).encrypt("segredo")
    rotated = Cipher([NEW, OLD], INDEX)

    assert rotated.decrypt(old_token) == "segredo"
    assert rotated.needs_rotation(old_token)
    assert not rotated.needs_rotation(rotated.encrypt("segredo"))


def test_unknown_key_is_rejected() -> None:
    token = Cipher([OLD], INDEX).encrypt("segredo")

    with pytest.raises(CryptoError):
        Cipher([NEW], INDEX).decrypt(token)


@pytest.mark.parametrize("position", [-1, -5, 20])
def test_tampered_token_is_rejected(position: int) -> None:
    cipher = Cipher([NEW], INDEX)
    token = list(cipher.encrypt("valor 42,00"))
    token[position] = "A" if token[position] != "A" else "B"

    with pytest.raises(CryptoError):
        cipher.decrypt("".join(token))


def test_key_id_is_bound_to_ciphertext() -> None:
    cipher = Cipher([NEW, OLD], INDEX)
    version, _, payload = cipher.encrypt("x").split(".", 2)

    with pytest.raises(CryptoError):
        cipher.decrypt(f"{version}.{key_id(OLD)}.{payload}")


def test_blind_index_is_deterministic_and_keyed() -> None:
    a = Cipher([NEW], INDEX).blind_index("5511999990000")
    b = Cipher([NEW], INDEX).blind_index("5511999990000")
    c = Cipher([NEW], os.urandom(32)).blind_index("5511999990000")

    assert a == b
    assert a != c
    assert "5511" not in a


def test_decode_key_validates_size() -> None:
    assert len(decode_key(generate_key())) == 32
    with pytest.raises(CryptoError):
        decode_key("curta")
    with pytest.raises(CryptoError):
        Cipher([], INDEX)
