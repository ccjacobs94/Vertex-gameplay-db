"""scrypt password hashing and invite/token helpers compatible with legacy hashes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Tuple

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def validate_password_policy(password: str) -> Tuple[bool, str]:
    if not isinstance(password, str) or len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if len(password) > 128:
        return False, "Password must be 128 characters or fewer"
    if not password.strip():
        return False, "Password cannot be blank"
    return True, ""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(digest).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${hash_b64}"


def verify_password(password: str, encoded_hash: str) -> bool:
    if not password or not encoded_hash:
        return False
    try:
        parts = encoded_hash.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = base64.b64decode(parts[4].encode("ascii"))
        expected = base64.b64decode(parts[5].encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def hash_opaque_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_opaque_token(prefix: str = "") -> str:
    raw = secrets.token_urlsafe(32)
    return f"{prefix}{raw}" if prefix else raw
