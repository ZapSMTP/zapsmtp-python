"""The errors the client raises."""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

import httpx

from ._retry import retry_after_seconds

__all__ = [
    "AuthenticationError",
    "ConflictError",
    "ConnectionError",
    "NotFoundError",
    "PermissionError",
    "RateLimitError",
    "ServerError",
    "ValidationError",
    "WebhookVerificationError",
    "ZapSMTPError",
]


class ZapSMTPError(Exception):
    """An error answer of the API, or no answer at all (:class:`ConnectionError`).

    Attributes:
        message: What went wrong, as the API said it.
        status: The HTTP status; 0 when there was no answer.
        code: The API's error code, e.g. ``"invalid_parameter"``.
        type: The error type, e.g. ``"invalid_request_error"``.
        request_id: The X-Request-Id of the answer: quote it to support.
        headers: The answer's headers.
        body: The parsed error body.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "",
        type: str = "",
        request_id: Optional[str] = None,
        headers: Optional[httpx.Headers] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.type = type
        self.request_id = request_id
        self.headers = headers
        self.body = body

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(message={self.message!r}, status={self.status}, "
            f"code={self.code!r}, request_id={self.request_id!r})"
        )


class AuthenticationError(ZapSMTPError):
    """401: the API key is missing, invalid or revoked."""


class PermissionError(ZapSMTPError):  # noqa: A001 - the API's name for 403
    """403: the key may not do this, or the domain is not verified."""


class NotFoundError(ZapSMTPError):
    """404: no such object."""


class ConflictError(ZapSMTPError):
    """409: the request conflicts with the current state or an Idempotency-Key."""


class ValidationError(ZapSMTPError):
    """400, 413 or 422: the request is wrong."""


class RateLimitError(ZapSMTPError):
    """429: too many requests, or a quota is used up."""

    @property
    def retry_after(self) -> Optional[float]:
        """Seconds the API asked to wait (Retry-After), when it said."""
        return retry_after_seconds(self.headers)


class ServerError(ZapSMTPError):
    """5xx: the API failed."""


class ConnectionError(ZapSMTPError):  # noqa: A001 - mirrors the other SDKs
    """No answer: a network failure or a timeout. ``status`` is 0."""


class WebhookVerificationError(ZapSMTPError):
    """A webhook request whose signature is missing, wrong or too old."""


_BY_STATUS: Dict[int, Type[ZapSMTPError]] = {
    400: ValidationError,
    401: AuthenticationError,
    403: PermissionError,
    404: NotFoundError,
    409: ConflictError,
    413: ValidationError,
    422: ValidationError,
    429: RateLimitError,
}


def _dig(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(*values: Any) -> Any:
    return next(value for value in values if value is not None)


def error_for(status: int, body: Any, headers: httpx.Headers) -> ZapSMTPError:
    """The error for an answer with an error status.

    Reads both shapes the API sends: ``{"error": {"code", "message", "type"}}``
    and the flat ``{"name", "message"}``.
    """
    code = _first(_dig(body, "error", "code"), _dig(body, "name"), "")
    message = _first(_dig(body, "error", "message"), _dig(body, "message"), f"HTTP {status}")
    kind = _first(_dig(body, "error", "type"), "")
    cls = _BY_STATUS.get(status) or (ServerError if status >= 500 else ZapSMTPError)
    return cls(
        str(message),
        status=status,
        code=str(code),
        type=str(kind),
        request_id=headers.get("x-request-id"),
        headers=headers,
        body=body,
    )
