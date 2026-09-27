"""Error classes map to the HTTP status table in docs/00_design_baseline.md §7."""

from __future__ import annotations

import pytest

from app import errors as e


@pytest.mark.parametrize(
    ("cls", "status", "slug"),
    [
        (e.BadRequestError, 400, "bad-request"),
        (e.NotFoundError, 404, "not-found"),
        (e.InvalidStateError, 409, "invalid-state"),
        (e.ValidationNotAcceptedError, 409, "validation-not-accepted"),
        (e.ConflictError, 409, "conflict"),
        (e.PayloadTooLargeError, 413, "payload-too-large"),
        (e.UnsupportedMediaTypeError, 415, "unsupported-media-type"),
        (e.TooManyPagesError, 422, "too-many-pages"),
        (e.EncryptedPdfError, 422, "encrypted-pdf"),
        (e.InvalidPdfError, 422, "invalid-pdf"),
        (e.MappingValidationError, 422, "invalid-mapping"),
        (e.ApprovalBlockedError, 422, "approval-blocked"),
    ],
)
def test_status_and_problem_type(cls: type[e.AppError], status: int, slug: str) -> None:
    err = cls("detail text", errors=[{"field": "trade_date"}])
    assert (err.status, err.slug) == (status, slug)
    assert err.type_uri == f"urn:bonds-parser:problem:{slug}"
    assert err.detail == "detail text"
    assert err.errors == [{"field": "trade_date"}]


def test_default_detail_is_title() -> None:
    assert e.NotFoundError().detail == "Not found"


def test_auth_errors_carry_headers() -> None:
    assert e.AuthError().status == 401
    assert e.AuthError().headers == {"WWW-Authenticate": 'Basic realm="bonds-parser"'}
    blocked = e.AuthBlockedError(900)
    assert (blocked.status, blocked.headers) == (429, {"Retry-After": "900"})
    assert e.ServiceUnavailableError().headers == {"Retry-After": "1"}


def test_config_error_is_not_an_http_error() -> None:
    assert issubclass(e.ConfigError, ValueError)
    assert not issubclass(e.ConfigError, e.AppError)
