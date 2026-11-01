"""
Password hashing, verification, and policy enforcement for Vertex (Game Hub).
Uses Python standard library hashlib.scrypt with base64 encoded salt and key.
"""

import base64
import hashlib
import os
import secrets
from typing import Optional

SCRYPT_N = 16384  # CPU/memory cost
SCRYPT_R = 8      # Block size
SCRYPT_P = 1      # Parallelization
KEY_LEN = 32      # Length of derived key in bytes
SALT_LEN = 16     # Length of salt in bytes


def validate_password_policy(pw: str) -> Optional[str]:
    """Validate password against minimum complexity requirements.

    Returns None if valid, or an error description string.
    """
    if not pw:
        return "Password cannot be empty."
    if len(pw) < 8:
        return "Password must be at least 8 characters long."
    if len(pw) > 256:
        return "Password cannot exceed 256 characters."
    return None


def hash_password(pw: str) -> str:
    """Hash a plaintext password using scrypt.

    Returns formatted string: scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>
    """
    if not pw:
        raise ValueError("Password cannot be empty")
    salt = secrets.token_bytes(SALT_LEN)
    derived = hashlib.scrypt(
        pw.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=32 * 1024 * 1024,
        dklen=KEY_LEN,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${hash_b64}"


def verify_password(pw: str, stored_hash: Optional[str]) -> bool:
    """Verify a plaintext password against a stored scrypt hash string.

    Returns False for empty passwords, None/empty hashes, or unrecognized formats
    (such as legacy placeholders like 'managed_by_web_auth').
    """
    if not pw or not stored_hash:
        return False

    parts = stored_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False

    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = base64.b64decode(parts[4].encode("ascii"))
        expected_hash = base64.b64decode(parts[5].encode("ascii"))
    except Exception:
        return False

    try:
        derived = hashlib.scrypt(
            pw.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=64 * 1024 * 1024,
            dklen=len(expected_hash),
        )
        return secrets.compare_digest(derived, expected_hash)
    except Exception:
        return False
