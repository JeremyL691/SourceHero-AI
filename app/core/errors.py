"""Domain exceptions.

Services raise these; only `app.main` translates them to HTTP. Nothing below the
router layer raises `HTTPException`.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every error the domain raises deliberately."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class DomainValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "permission_denied"


class BudgetExceededError(DomainError):
    status_code = 429
    code = "budget_exceeded"


__all__ = [
    "BudgetExceededError",
    "ConflictError",
    "DomainError",
    "DomainValidationError",
    "NotFoundError",
    "PermissionDeniedError",
]
