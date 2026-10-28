"""The hand-written core of the SDK: HTTP, retries, errors, paging.

The API resources and models are generated (``_generated``) from the API
reference; they call :meth:`SyncAPIClient._request` and friends.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple, Type, TypeVar, Union

import httpx

from ._exceptions import ConnectionError, ValidationError, ZapSMTPError, error_for
from ._models import BaseModel, construct
from ._pagination import AsyncPage, AsyncPageRequest, Query, SyncPage
from ._response import ResponseInfo, record
from ._retry import backoff, retry_after_seconds, retryable
from ._types import NOT_GIVEN, NotGiven, Timeout, TimeoutArg
from ._utils import MultipartForm, dump_json, encode_query, expand_path, loads, strip
from ._version import __version__

__all__ = ["AsyncAPIClient", "RequestOptions", "SyncAPIClient"]

DEFAULT_BASE_URL = "https://api.zapsmtp.com/v1"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2

M = TypeVar("M", bound=BaseModel)
SyncClientT = TypeVar("SyncClientT", bound="SyncAPIClient")
AsyncClientT = TypeVar("AsyncClientT", bound="AsyncAPIClient")

_SAFE_METHODS = frozenset({"GET", "PUT", "DELETE"})


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class RequestOptions:
    """Per-request settings every generated method takes as keyword arguments."""

    idempotency_key: Optional[str] = None
    extra_headers: Optional[Mapping[str, str]] = None
    timeout: TimeoutArg = NOT_GIVEN
    max_retries: Optional[int] = None


@dataclass(frozen=True)
class _Request:
    method: str
    path: str
    query: Query = field(default_factory=dict)
    body: Any = NOT_GIVEN
    form: Optional[MultipartForm] = None
    headers: Mapping[str, Optional[str]] = field(default_factory=dict)
    idempotent: bool = False
    path_params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Prepared:
    method: str
    url: str
    headers: httpx.Headers
    content: Optional[bytes]
    form: Optional[MultipartForm]
    timeout: Timeout
    max_retries: int
    safe: bool


class _AttemptTimeout(Exception):
    pass


@dataclass(frozen=True)
class _Answer:
    status: int
    headers: httpx.Headers
    text: str


def _request(
    method: str,
    path: str,
    query: Optional[Query],
    body: Any,
    form: Optional[MultipartForm],
    headers: Optional[Mapping[str, Optional[str]]],
    idempotent: bool,
    path_params: Optional[Mapping[str, Any]],
) -> _Request:
    return _Request(
        method,
        path,
        strip(query or {}),
        strip(body) if isinstance(body, dict) else body,
        form,
        headers or {},
        idempotent,
        path_params or {},
    )


def _user_agent() -> str:
    return f"zapsmtp-python/{__version__} (python/{platform.python_version()})"


def _decode(answer: _Answer) -> Any:
    if not answer.text:
        return None
    try:
        return loads(answer.text)
    except ValueError:
        return answer.text


def _connection_error(err: Exception, timeout: Timeout) -> ConnectionError:
    if isinstance(err, (_AttemptTimeout, httpx.TimeoutException)):
        return ConnectionError(f"timed out after {timeout:g} s" if timeout is not None else "timed out")
    return ConnectionError(str(err) or type(err).__name__)


class _BaseClient:
    api_key: str
    base_url: str
    timeout: Timeout
    max_retries: int

    def __init__(
        self,
        api_key: Optional[str],
        *,
        base_url: Optional[str],
        timeout: Timeout,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("ZAP_API_KEY")
        if not key:
            raise ZapSMTPError("Missing API key: pass api_key=... or set ZAP_API_KEY")
        self.api_key = key
        self.base_url = (base_url or os.environ.get("ZAP_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._default_headers: Dict[str, str] = dict(default_headers or {})

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    def _prepare(self, req: _Request, options: RequestOptions) -> _Prepared:
        headers = httpx.Headers(
            {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": _user_agent()}
        )
        headers.update(self._default_headers)
        headers.update({k: v for k, v in req.headers.items() if v is not None})
        if req.idempotent:
            key = options.idempotency_key
            headers["Idempotency-Key"] = str(uuid.uuid4()) if key is None else key
        headers.update(options.extra_headers or {})  # the caller's headers replace ours, whatever their case
        # an empty key turns deduplication off on the server, so a retried send
        # could go out twice: refused before anything is sent
        key = headers.get("idempotency-key")
        if key is not None and not key.strip():
            raise ValidationError("idempotency_key must not be empty", code="invalid_parameter")
        content: Optional[bytes] = None
        if req.form is None and not isinstance(req.body, NotGiven):
            headers["Content-Type"] = "application/json"
            content = dump_json(req.body)
        query = encode_query(req.query)
        timeout = self.timeout if isinstance(options.timeout, NotGiven) else options.timeout
        return _Prepared(
            method=req.method,
            url=self.base_url + expand_path(req.path, req.path_params) + (f"?{query}" if query else ""),
            headers=headers,
            content=content,
            form=req.form,
            timeout=timeout,
            max_retries=self.max_retries if options.max_retries is None else options.max_retries,
            safe=req.method in _SAFE_METHODS or req.idempotent,
        )

    def _build(self, client: Union[httpx.Client, httpx.AsyncClient], p: _Prepared) -> httpx.Request:
        if p.form is not None:
            return client.build_request(
                p.method,
                p.url,
                headers=p.headers,
                files={p.form.field: (p.form.filename, p.form.content)},
                data=p.form.fields,
                timeout=p.timeout,
            )
        return client.build_request(p.method, p.url, headers=p.headers, content=p.content, timeout=p.timeout)

    @staticmethod
    def _deadline(p: _Prepared) -> Optional[float]:
        return None if p.timeout is None else _monotonic() + p.timeout

    @staticmethod
    def _check(deadline: Optional[float]) -> None:
        # the timeout bounds the whole attempt, the body read included
        if deadline is not None and _monotonic() > deadline:
            raise _AttemptTimeout()

    @classmethod
    def _checked(cls, chunk: bytes, deadline: Optional[float]) -> bytes:
        cls._check(deadline)
        return chunk

    @staticmethod
    def _answer(response: httpx.Response, body: bytes) -> _Answer:
        return _Answer(response.status_code, response.headers, body.decode(response.charset_encoding or "utf-8", "replace"))

    @staticmethod
    def _outcome(answer: _Answer) -> Tuple[Any, ResponseInfo, Optional[ZapSMTPError]]:
        info = ResponseInfo(answer.status, answer.headers, answer.headers.get("x-request-id"))
        parsed = _decode(answer)
        if 200 <= answer.status < 300:
            record(info)
            return parsed, info, None
        return parsed, info, error_for(answer.status, parsed, answer.headers)


class SyncAPIClient(_BaseClient):
    """The synchronous client core. Use :class:`zapsmtp.ZapSMTP`."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(
            api_key, base_url=base_url, timeout=timeout, max_retries=max_retries, default_headers=default_headers
        )
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client()

    def with_options(
        self: SyncClientT,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: TimeoutArg = NOT_GIVEN,
        max_retries: Optional[int] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> SyncClientT:
        """A copy of this client with some settings changed; it shares the connection pool."""
        return type(self)(
            api_key or self.api_key,
            base_url=base_url or self.base_url,
            timeout=self.timeout if isinstance(timeout, NotGiven) else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
            default_headers={**self._default_headers, **(default_headers or {})},
            http_client=self._client,
        )

    def close(self) -> None:
        """Closes the connection pool, unless it was passed in as ``http_client``."""
        if self._owns_client:
            self._client.close()

    def __enter__(self: SyncClientT) -> SyncClientT:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _attempt(self, p: _Prepared) -> _Answer:
        deadline = self._deadline(p)
        response = self._client.send(self._build(self._client, p), stream=True)
        try:
            self._check(deadline)
            chunks = [self._checked(chunk, deadline) for chunk in response.iter_bytes()]
            self._check(deadline)  # the end of the body too, not only its chunks
            return self._answer(response, b"".join(chunks))
        finally:
            response.close()

    def _send(self, req: _Request, options: RequestOptions) -> Any:
        p = self._prepare(req, options)
        attempt = -1
        while True:
            attempt += 1
            try:
                answer = self._attempt(p)
            except Exception as err:
                # a timeout or a broken connection (sending, or reading the
                # answer): the request may have reached the server, so only a
                # request that is safe to repeat is sent again
                if attempt < p.max_retries and p.safe:
                    _sleep(backoff(attempt, None))
                    continue
                raise _connection_error(err, p.timeout) from err
            parsed, info, error = self._outcome(answer)
            if error is None:
                return parsed
            if attempt < p.max_retries and retryable(info.status, error.code, p.safe):
                _sleep(backoff(attempt, retry_after_seconds(info.headers)))
                continue
            raise error

    def _request(
        self,
        method: str,
        path: str,
        *,
        cast: Type[M],
        options: RequestOptions,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Query] = None,
        body: Any = NOT_GIVEN,
        form: Optional[MultipartForm] = None,
        headers: Optional[Mapping[str, Optional[str]]] = None,
        idempotent: bool = False,
    ) -> M:
        result: M = construct(cast, self._send(_request(method, path, query, body, form, headers, idempotent, path_params), options))
        return result

    def _request_empty(
        self,
        method: str,
        path: str,
        *,
        options: RequestOptions,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Query] = None,
        body: Any = NOT_GIVEN,
        headers: Optional[Mapping[str, Optional[str]]] = None,
        idempotent: bool = False,
    ) -> None:
        self._send(_request(method, path, query, body, None, headers, idempotent, path_params), options)

    def _request_page(
        self,
        method: str,
        path: str,
        *,
        cast: Type[M],
        paging: Optional[str],
        options: RequestOptions,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Query] = None,
    ) -> SyncPage[M]:
        def load(q: Query) -> SyncPage[M]:
            req = _request(method, path, q, NOT_GIVEN, None, None, False, path_params)
            return SyncPage(self._send(req, options), cast, req.query, paging, load)

        return load(strip(query or {}))


class AsyncAPIClient(_BaseClient):
    """The asyncio client core. Use :class:`zapsmtp.AsyncZapSMTP`."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            api_key, base_url=base_url, timeout=timeout, max_retries=max_retries, default_headers=default_headers
        )
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.AsyncClient()

    def with_options(
        self: AsyncClientT,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: TimeoutArg = NOT_GIVEN,
        max_retries: Optional[int] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> AsyncClientT:
        """A copy of this client with some settings changed; it shares the connection pool."""
        return type(self)(
            api_key or self.api_key,
            base_url=base_url or self.base_url,
            timeout=self.timeout if isinstance(timeout, NotGiven) else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
            default_headers={**self._default_headers, **(default_headers or {})},
            http_client=self._client,
        )

    async def close(self) -> None:
        """Closes the connection pool, unless it was passed in as ``http_client``."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self: AsyncClientT) -> AsyncClientT:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _attempt(self, p: _Prepared) -> _Answer:
        deadline = self._deadline(p)
        response = await self._client.send(self._build(self._client, p), stream=True)
        try:
            self._check(deadline)
            chunks = [self._checked(chunk, deadline) async for chunk in response.aiter_bytes()]
            self._check(deadline)  # the end of the body too, not only its chunks
            return self._answer(response, b"".join(chunks))
        finally:
            await response.aclose()

    async def _send(self, req: _Request, options: RequestOptions) -> Any:
        p = self._prepare(req, options)
        attempt = -1
        while True:
            attempt += 1
            try:
                answer = await self._attempt(p)
            except Exception as err:
                # see SyncAPIClient._send; cancellation (asyncio.CancelledError)
                # is not an Exception and goes straight through
                if attempt < p.max_retries and p.safe:
                    await _async_sleep(backoff(attempt, None))
                    continue
                raise _connection_error(err, p.timeout) from err
            parsed, info, error = self._outcome(answer)
            if error is None:
                return parsed
            if attempt < p.max_retries and retryable(info.status, error.code, p.safe):
                await _async_sleep(backoff(attempt, retry_after_seconds(info.headers)))
                continue
            raise error

    async def _request(
        self,
        method: str,
        path: str,
        *,
        cast: Type[M],
        options: RequestOptions,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Query] = None,
        body: Any = NOT_GIVEN,
        form: Optional[MultipartForm] = None,
        headers: Optional[Mapping[str, Optional[str]]] = None,
        idempotent: bool = False,
    ) -> M:
        result: M = construct(cast, await self._send(_request(method, path, query, body, form, headers, idempotent, path_params), options))
        return result

    async def _request_empty(
        self,
        method: str,
        path: str,
        *,
        options: RequestOptions,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Query] = None,
        body: Any = NOT_GIVEN,
        headers: Optional[Mapping[str, Optional[str]]] = None,
        idempotent: bool = False,
    ) -> None:
        await self._send(_request(method, path, query, body, None, headers, idempotent, path_params), options)

    def _request_page(
        self,
        method: str,
        path: str,
        *,
        cast: Type[M],
        paging: Optional[str],
        options: RequestOptions,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Query] = None,
    ) -> AsyncPageRequest[M]:
        async def load(q: Query) -> AsyncPage[M]:
            req = _request(method, path, q, NOT_GIVEN, None, None, False, path_params)
            return AsyncPage(await self._send(req, options), cast, req.query, paging, load)

        first = strip(query or {})
        return AsyncPageRequest(lambda: load(first))
