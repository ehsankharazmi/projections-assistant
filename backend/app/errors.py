"""Application errors with stable codes and user-facing messages."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """An expected failure. `code` is stable; `message` is safe to show to users."""

    status_code = 400

    def __init__(self, code: str, message: str, *, status_code: int | None = None,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class NotFound(AppError):
    status_code = 404

    def __init__(self, what: str = "resource"):
        super().__init__("not_found", f"The requested {what} was not found.")


class Conflict(AppError):
    status_code = 409


class Forbidden(AppError):
    status_code = 403

    def __init__(self, message: str = "You do not have access to this resource."):
        super().__init__("forbidden", message)


class Unauthorized(AppError):
    status_code = 401

    def __init__(self, message: str = "Sign-in is required."):
        super().__init__("unauthorized", message)
