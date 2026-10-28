"""Response metadata: ``client.emails.with_response.send(...)`` and friends."""

from __future__ import annotations

import contextvars
import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Generic, List, Optional, TypeVar

import httpx

if TYPE_CHECKING:
    from typing_extensions import ParamSpec

    P = ParamSpec("P")

__all__ = ["APIResponse", "ResponseInfo"]

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class ResponseInfo:
    """What the API answered besides the body."""

    status: int
    headers: httpx.Headers
    request_id: Optional[str]
    """X-Request-Id: quote it to support."""


@dataclass(frozen=True)
class APIResponse(Generic[T]):
    """A result with the status and headers of the answer that carried it.

    ``headers`` holds the rate-limit and quota headers, e.g.
    ``response.headers["ratelimit-remaining"]``.
    """

    data: T
    status: int
    headers: httpx.Headers
    request_id: Optional[str]
    """X-Request-Id: quote it to support."""


# Set while a with_response call runs; the client records the answer here.
_capture: contextvars.ContextVar[Optional[List[ResponseInfo]]] = contextvars.ContextVar(
    "zapsmtp_capture", default=None
)


def record(info: ResponseInfo) -> None:
    """Called by the client for every successful answer."""
    holder = _capture.get()
    if holder is not None:
        holder.append(info)


def _result(data: R, holder: List[ResponseInfo]) -> APIResponse[R]:
    info = holder[-1]
    return APIResponse(data=data, status=info.status, headers=info.headers, request_id=info.request_id)


def to_response(fn: Callable[P, R]) -> Callable[P, APIResponse[R]]:
    """The method ``fn``, returning an :class:`APIResponse` around its result."""

    @functools.wraps(fn)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> APIResponse[R]:
        holder: List[ResponseInfo] = []
        token = _capture.set(holder)
        try:
            data = fn(*args, **kwargs)
        finally:
            _capture.reset(token)
        return _result(data, holder)

    return wrapped


def async_to_response(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[APIResponse[R]]]:
    """The async method ``fn``, returning an :class:`APIResponse` around its result."""

    @functools.wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> APIResponse[R]:
        holder: List[ResponseInfo] = []
        token = _capture.set(holder)
        try:
            data = await fn(*args, **kwargs)
        finally:
            _capture.reset(token)
        return _result(data, holder)

    return wrapped


