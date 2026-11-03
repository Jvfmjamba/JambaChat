import hashlib
import hmac

PREFIX = "sha256="


def is_valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith(PREFIX):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix(PREFIX))
