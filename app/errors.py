"""Shared exception hierarchy (docs/03_lld.md §8, status codes in docs/00_design_baseline.md §7).

Every AppError maps to one HTTP status and an RFC 7807 problem type
`urn:bonds-parser:problem:<slug>`. The API layer turns them into problem+json responses;
the engine and services raise them without knowing about HTTP.
"""

from __future__ import annotations

from typing import Any, ClassVar

PROBLEM_TYPE_PREFIX = "urn:bonds-parser:problem:"


class ConfigError(ValueError):
    """Invalid BONDS_* setting. Raised at startup; the process refuses to start."""


class AppError(Exception):
    status: ClassVar[int] = 500
    slug: ClassVar[str] = "internal-error"
    title: ClassVar[str] = "Internal error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.errors = errors or []
        self.headers = headers or {}

    @property
    def type_uri(self) -> str:
        return PROBLEM_TYPE_PREFIX + self.slug


class BadRequestError(AppError):
    status, slug, title = 400, "bad-request", "Bad request"


class AuthError(AppError):
    status, slug, title = 401, "unauthorized", "Authentication required"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"WWW-Authenticate": 'Basic realm="bonds-parser"'})


class AuthBlockedError(AppError):
    status, slug, title = 429, "auth-blocked", "Too many failed authentication attempts"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(headers={"Retry-After": str(retry_after_seconds)})


class NotFoundError(AppError):
    status, slug, title = 404, "not-found", "Not found"


class InvalidStateError(AppError):
    status, slug, title = 409, "invalid-state", "Operation not allowed in the current status"


class ValidationNotAcceptedError(AppError):
    status, slug, title = 409, "validation-not-accepted", "Validation issues not accepted"


class ConflictError(AppError):
    status, slug, title = 409, "conflict", "Conflicting update, retry"


class PayloadTooLargeError(AppError):
    status, slug, title = 413, "payload-too-large", "File too large"


class UnsupportedMediaTypeError(AppError):
    status, slug, title = 415, "unsupported-media-type", "Not a PDF file"


class TooManyPagesError(AppError):
    status, slug, title = 422, "too-many-pages", "PDF has too many pages"


class EncryptedPdfError(AppError):
    status, slug, title = 422, "encrypted-pdf", "Password-protected PDFs are not supported yet"


class InvalidPdfError(AppError):
    status, slug, title = 422, "invalid-pdf", "PDF cannot be read"


class MappingValidationError(AppError):
    status, slug, title = 400, "invalid-mapping", "Invalid mapping payload"


class ApprovalBlockedError(AppError):
    status, slug, title = 422, "approval-blocked", "Approval blocked: required fields missing"


class ServiceUnavailableError(AppError):
    status, slug, title = 503, "service-unavailable", "Service temporarily unavailable"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"Retry-After": "1"})
