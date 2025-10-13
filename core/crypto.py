import base64
import hashlib
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings


class SecretEncryptionError(Exception):
    """Raised when a secret cannot be encrypted or decrypted."""


def _derive_key_from_secret(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    try:
        return Fernet(_derive_key_from_secret(settings.SECRET_KEY))
    except Exception as exc:  # pragma: no cover - defensive, should never happen
        raise SecretEncryptionError("Failed to initialise encryption backend") from exc


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None if value is None else ""
    try:
        token = _get_fernet().encrypt(value.encode("utf-8"))
        return token.decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise SecretEncryptionError("Failed to encrypt secret") from exc


def decrypt_secret(token: Optional[str]) -> Optional[str]:
    if token in (None, ""):
        return token
    try:
        value = _get_fernet().decrypt(token.encode("utf-8"))
        return value.decode("utf-8")
    except InvalidToken as exc:
        raise SecretEncryptionError("Invalid secret token provided") from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise SecretEncryptionError("Failed to decrypt secret") from exc
