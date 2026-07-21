from __future__ import annotations

from typing import Any


class MnemomeError(Exception):
    code = "MNEMOME_ERROR"
    status_code = 400
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(MnemomeError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictError(MnemomeError):
    code = "VERSION_CONFLICT"
    status_code = 409


class ValidationError(MnemomeError):
    code = "VALIDATION_ERROR"
    status_code = 422


class AuthenticationError(MnemomeError):
    code = "UNAUTHENTICATED"
    status_code = 401


class AuthorizationError(MnemomeError):
    code = "FORBIDDEN"
    status_code = 403


class ConfigurationError(MnemomeError):
    code = "CONFIGURATION_ERROR"
    status_code = 500
