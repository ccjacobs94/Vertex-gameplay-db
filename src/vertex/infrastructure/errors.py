"""Typed domain and infrastructure exceptions (no HTTP dependencies)."""

from __future__ import annotations


class VertexError(Exception):
    """Base error raised across domain and infrastructure layers."""

    code: str = "vertex_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class ConfigurationError(VertexError):
    code = "configuration_error"


class MissingSecretKeyError(ConfigurationError):
    code = "missing_secret_key"


class SecretDecryptionError(VertexError):
    code = "secret_decryption_failed"


class MigrationError(VertexError):
    code = "migration_error"


class AuthenticationError(VertexError):
    code = "authentication_failed"


class RateLimitedError(VertexError):
    code = "rate_limited"


class AuthorizationError(VertexError):
    code = "forbidden"


class NotFoundError(VertexError):
    code = "not_found"


class ValidationError(VertexError):
    code = "validation_error"


class ConflictError(VertexError):
    code = "conflict"


class ScopeViolationError(AuthorizationError):
    code = "invalid_user_scope"


class BackupError(VertexError):
    code = "backup_error"
