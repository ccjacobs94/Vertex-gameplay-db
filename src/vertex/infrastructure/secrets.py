"""Fail-closed Fernet encryption at rest with explicit key rotation support."""

from __future__ import annotations

import base64
import hashlib
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from vertex.infrastructure.errors import (
    ConfigurationError,
    MissingSecretKeyError,
    SecretDecryptionError,
)


def legacy_fernet_from_secret(secret: str) -> Fernet:
    """Derive Fernet key identically to legacy system_db._fernet_from_secret()."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def parse_fernet_key(raw: str) -> tuple[Fernet, bytes]:
    """Parse an explicit 44-char Fernet key or >=12-char passphrase deterministically."""
    cleaned = (raw or "").strip()
    if not cleaned:
        raise MissingSecretKeyError(
            "VERTEX_SECRET_KEY (or GAMEHUB_SECRET_KEY) is required to encrypt or decrypt credentials"
        )
    # Accept a native urlsafe-base64 32-byte Fernet key directly.
    if len(cleaned) == 44:
        try:
            key_bytes = cleaned.encode("ascii")
            return Fernet(key_bytes), hashlib.sha256(key_bytes).digest()
        except Exception:
            pass
    if len(cleaned) < 12:
        raise ConfigurationError(
            "Configured secret key must be a valid Fernet key or at least 12 characters"
        )
    key_bytes = base64.urlsafe_b64encode(hashlib.sha256(cleaned.encode("utf-8")).digest())
    return Fernet(key_bytes), hashlib.sha256(key_bytes).digest()


class SecretBox:
    """Fail-closed wrapper around MultiFernet without fallback hardcoded keys."""

    def __init__(self, primary_key: str, previous_keys: Iterable[str] = ()) -> None:
        self._primary_raw = (primary_key or "").strip()
        self._previous_raw = tuple(k.strip() for k in previous_keys if k and k.strip())
        self._primary: Fernet | None = None
        self._multi: MultiFernet | None = None
        self._fingerprint: str | None = None

        if self._primary_raw:
            primary, digest = parse_fernet_key(self._primary_raw)
            ciphers: list[Fernet] = [primary]
            for prev in self._previous_raw:
                prev_cipher, _ = parse_fernet_key(prev)
                ciphers.append(prev_cipher)
            self._primary = primary
            self._multi = MultiFernet(ciphers)
            self._fingerprint = hashlib.sha256(digest).hexdigest()[:16]

    @property
    def is_configured(self) -> bool:
        return self._multi is not None

    def key_fingerprint(self) -> str | None:
        return self._fingerprint

    def _require_multi(self) -> MultiFernet:
        if self._multi is None or self._primary is None:
            raise MissingSecretKeyError(
                "Secret encryption key is missing; set VERTEX_SECRET_KEY before managing credentials"
            )
        return self._multi

    def encrypt(self, plaintext: str) -> str:
        if plaintext == "":
            return ""
        cipher = self._require_multi()
        return cipher.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str:
        if not ciphertext:
            return ""
        cipher = self._require_multi()
        try:
            return cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise SecretDecryptionError(
                "Stored credential cannot be decrypted with the configured VERTEX_SECRET_KEY"
            ) from exc

    def rotate_ciphertext(self, ciphertext: str | None) -> str:
        if not ciphertext:
            return ""
        plaintext = self.decrypt(ciphertext)
        assert self._primary is not None
        return self._primary.encrypt(plaintext.encode("utf-8")).decode("ascii")
