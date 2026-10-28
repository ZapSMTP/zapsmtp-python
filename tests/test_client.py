"""The client core: configuration, requests, errors, retries (sdks/node/test/core.test.ts)."""

from __future__ import annotations

import asyncio
import io
import json
import platform
import re
from pathlib import Path
from typing import Any, List, Type

import httpx
import pytest

import zapsmtp
from zapsmtp import (
    AuthenticationError,
    ConflictError,
    ConnectionError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    ServerError,
    ValidationError,
    ZapSMTPError,
    _base_client,
)
from zapsmtp._retry import backoff, retry_after_seconds, retryable

from .conftest import REAL_ASYNC_SLEEP, REAL_SLEEP, Fake, api_error, async_client, client, json_response

# ---- configuration ----------------------------------------------------------


def test_reads_the_key_and_base_url_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZAP_API_KEY", "zap_env")
    monkeypatch.setenv("ZAP_BASE_URL", "https://env.test/v1")
    fake = Fake(json_response(200, {"id": "e1"}))
    zap = zapsmtp.ZapSMTP(http_client=httpx.Client(transport=httpx.MockTransport(fake)))
    assert zap.api_key == "zap_env"
    zap.emails.get("e1")
    assert str(fake.calls[0].url) == "https://env.test/v1/emails/e1"
    assert fake.header(0, "Authorization") == "Bearer zap_env"


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZAP_BASE_URL", raising=False)
    zap = zapsmtp.ZapSMTP("k")
    assert zap.base_url == "https://api.zapsmtp.com/v1"
    assert zap.timeout == 60.0
    assert zap.max_retries == 2
    assert repr(zap) == "ZapSMTP(base_url='https://api.zapsmtp.com/v1')"
    zap.close()


def test_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZAP_API_KEY", raising=False)
    with pytest.raises(ZapSMTPError, match="Missing API key"):
        zapsmtp.ZapSMTP()
    monkeypatch.setenv("ZAP_API_KEY", "zap_env")
    with pytest.raises(ZapSMTPError, match="Missing API key"):
        zapsmtp.AsyncZapSMTP("")  # an explicit empty key is not replaced by the environment


def test_sends_the_headers_every_request_carries() -> None:
    fake = Fake(json_response(200, {"id": "x"}))
    zap = client(fake, default_headers={"X-Team": "mail", "user-agent": "overridden"})
    zap.emails.get("a/b c", extra_headers={"X-Extra": "1"})
    assert str(fake.calls[0].url) == "https://api.test/v1/emails/a%2Fb%20c"
    assert fake.header(0, "X-Team") == "mail"
    assert fake.header(0, "X-Extra") == "1"
    assert fake.header(0, "User-Agent") == "overridden"
    assert fake.header(0, "Accept") == "application/json"
    assert fake.header(0, "Content-Type") is None
    assert fake.calls[0].content == b""
    client(fake).usage.get()
    assert re.fullmatch(
        rf"zapsmtp-python/{re.escape(zapsmtp.__version__)} \(python/{re.escape(platform.python_version())}\)",
        fake.header(1, "User-Agent") or "",
    )


@pytest.mark.parametrize("value,message", [("", "id must not be empty"), (".", 'id must not be "."'),
                                           ("..", 'id must not be ".."')])
def test_refuses_an_id_that_cannot_be_a_path_segment_before_sending(value: str, message: str) -> None:
    fake = Fake(json_response(200, {}))
    for call in (lambda: client(fake).emails.get(value), lambda: client(fake).emails.attachments.list(value),
                 lambda: client(fake).templates.versions.get("t1", value)):
        with pytest.raises(ValidationError) as info:
            call()
        assert info.value.code == "invalid_parameter" and info.value.status == 0
    assert str(info.value) == message.replace("id", "vid", 1)

    async def run() -> None:
        with pytest.raises(ValidationError, match=message):
            await async_client(fake).emails.get(value)
        pending = async_client(fake).emails.attachments.list(value)  # refused when awaited
        with pytest.raises(ValidationError, match=message):
            await pending

    asyncio.run(run())
    assert fake.calls == []
    client(fake).emails.get(".a.")  # dots inside an id are fine
    assert fake.calls[0].url.raw_path == b"/v1/emails/.a."


def test_serializes_query_parameters() -> None:
    fake = Fake(json_response(200, {"object": "list", "data": [], "has_more": False}))
    client(fake).emails.list(limit=5, metadata={"order_id": "A-1", "a b": "c"})
    assert str(fake.calls[0].url) == "https://api.test/v1/emails?limit=5&metadata%5Border_id%5D=A-1&metadata%5Ba+b%5D=c"
    client(fake).threads.list(unread=True, awaiting_reply=False)
    assert str(fake.calls[1].url) == "https://api.test/v1/threads?awaiting_reply=false&unread=true"
    client(fake)._request_page(
        "GET", "/x", cast=zapsmtp.types.Email, paging=None, options=_base_client.RequestOptions(),
        query={"tag": ["a", "b"], "none": None, "set": {"z"}, "n": [None, 1.5]},
    )
    assert str(fake.calls[2].url) == "https://api.test/v1/x?tag=a&tag=b&set=z&n=null&n=1.5"


def test_sends_json_bodies_and_an_idempotency_key_on_sends() -> None:
    fake = Fake(json_response(200, {"id": "e1"}))
    zap = client(fake)
    sent = zap.emails.send(from_="a@x.test", to="b@y.test", subject="Hi", text="Hello")
    assert sent.to_dict() == {"id": "e1"}
    assert fake.header(0, "Content-Type") == "application/json"
    assert re.fullmatch(r"[0-9a-f-]{36}", fake.header(0, "Idempotency-Key") or "")
    assert fake.json(0) == {"from": "a@x.test", "to": "b@y.test", "subject": "Hi", "text": "Hello"}
    zap.emails.send(to=["b@y.test"], template={"id": "t", "variables": {"n": 1}}, idempotency_key="order-1")
    assert fake.header(1, "Idempotency-Key") == "order-1"
    assert fake.json(1) == {"to": ["b@y.test"], "template": {"id": "t", "variables": {"n": 1}}}
    zap.domains.update("d1")  # every optional parameter left out: an empty object
    assert fake.json(2) == {}
    assert fake.header(2, "Idempotency-Key") is None
    zap.domains.update("d1", reply_forward_to=None)  # None is sent as null
    assert fake.json(3) == {"reply_forward_to": None}


def test_encodes_any_mapping_sequence_and_model() -> None:
    from types import MappingProxyType

    fake = Fake(json_response(200, {"id": "e1"}))
    tag = zapsmtp.types.Email({"id": "m"})
    client(fake).emails.send(
        to=("a@x.test", "b@x.test"),
        metadata=MappingProxyType({"k": "v"}),
        template={"id": "t", "variables": {"set": frozenset({1}), "model": tag, "range": range(2)}},
    )
    assert fake.json(0) == {
        "to": ["a@x.test", "b@x.test"],
        "metadata": {"k": "v"},
        "template": {"id": "t", "variables": {"set": [1], "model": {"id": "m"}, "range": [0, 1]}},
    }
    with pytest.raises(TypeError, match="not JSON serializable"):
        client(fake).emails.send(to="a@x.test", template={"id": "t", "variables": {"b": b"x"}})


def test_sends_a_batch_with_its_validation_mode() -> None:
    fake = Fake(json_response(200, {"data": []}))
    zap = client(fake)
    zap.emails.send_batch([{"from": "a", "to": "b", "subject": "s"}])
    assert fake.header(0, "x-batch-validation") is None
    assert fake.json(0) == [{"from": "a", "to": "b", "subject": "s"}]
    zap.emails.send_batch(iter([{"from": "a", "to": "b"}]), validation="permissive")
    assert fake.header(1, "x-batch-validation") == "permissive"


def test_answers_none_for_204() -> None:
    assert client(Fake(httpx.Response(204))).domains.delete("d1") is None


def test_keeps_a_body_that_is_not_json() -> None:
    assert client(Fake(httpx.Response(200, content=b"plain"))).usage.get() == "plain"  # type: ignore[comparison-overlap]


def test_with_response_gives_status_headers_and_request_id() -> None:
    fake = Fake(json_response(200, {"id": "e1"}, {"RateLimit-Remaining": "9"}))
    r = client(fake).emails.with_response.get("e1")
    assert r.data.id == "e1"
    assert r.status == 200
    assert r.request_id == "req_1"
    assert r.headers["ratelimit-remaining"] == "9"
    assert client(Fake(httpx.Response(204))).domains.with_response.delete("d1").data is None


def test_with_options_copies_the_client_and_shares_its_pool() -> None:
    fake = Fake(json_response(200, {}))
    zap = client(fake, default_headers={"X-A": "1"})
    other = zap.with_options(timeout=5, max_retries=0, default_headers={"X-B": "2"}, base_url="https://b.test/v1")
    assert (other.timeout, other.max_retries, other.base_url, other.api_key) == (5, 0, "https://b.test/v1", "zap_test_key")
    assert other._client is zap._client
    other.usage.get()
    assert (fake.header(0, "X-A"), fake.header(0, "X-B")) == ("1", "2")
    same = zap.with_options(api_key="zap_other")
    assert (same.timeout, same.max_retries, same.api_key) == (60.0, 2, "zap_other")
    other.close()  # not its pool: still open
    zap.usage.get()


def test_closes_only_its_own_pool() -> None:
    with zapsmtp.ZapSMTP("k") as zap:
        pool = zap._client
    assert pool.is_closed
    fake = Fake(json_response(200, {}))
    with client(fake) as zap:
        pass
    assert not zap._client.is_closed


def _upload_checks(fake: Fake) -> None:
    def part(i: int) -> str:
        return fake.calls[i].content.decode("latin-1")

    assert fake.header(0, "Content-Type", ).startswith("multipart/form-data; boundary=")  # type: ignore[union-attr]
    assert 'name="file"; filename="logo.png"' in part(0) and "\x01\x02\x03" in part(0)
    assert 'name="name"\r\n\r\nlogo.png' in part(0)
    assert 'filename="a.gif"' in part(1) and 'name="name"' not in part(1)
    assert 'filename="image"' in part(2)
    assert 'filename="pic.png"' in part(3) and "\x89P" in part(3)
    assert 'filename="pic.png"' in part(4)
    assert 'filename="image"' in part(5)


def test_uploads_an_image_file_as_multipart(tmp_path: Path) -> None:
    fake = Fake(json_response(201, {"id": "img"}))
    zap = client(fake)
    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG")
    assert zap.template_assets.upload_file(b"\x01\x02\x03", name="logo.png").id == "img"
    zap.template_assets.upload_file(bytearray(b"x"), filename="a.gif")
    zap.template_assets.upload_file(memoryview(b"xy"))
    zap.template_assets.upload_file(image)
    with image.open("rb") as f:
        zap.template_assets.upload_file(f)
    zap.template_assets.upload_file(io.BytesIO(b"raw"))
    _upload_checks(fake)
    with pytest.raises(TypeError, match="not str"):
        zap.template_assets.upload_file(str(image))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="binary mode"):
        zap.template_assets.upload_file(io.StringIO("text"))  # type: ignore[arg-type]


def test_async_client_basics(tmp_path: Path) -> None:
    async def run() -> None:
        fake = Fake(json_response(201, {"id": "img"}))
        image = tmp_path / "pic.png"
        image.write_bytes(b"\x89PNG")
        async with async_client(fake) as zap:
            await zap.template_assets.upload_file(b"\x01\x02\x03", name="logo.png")
            await zap.template_assets.upload_file(b"x", filename="a.gif")
            await zap.template_assets.upload_file(b"xy")
            await zap.template_assets.upload_file(image)
            with image.open("rb") as f:
                await zap.template_assets.upload_file(f)
            await zap.template_assets.upload_file(io.BytesIO(b"raw"))
            _upload_checks(fake)
            other = zap.with_options(max_retries=1, timeout=None, api_key="k2", default_headers={"X": "1"})
            assert (other.max_retries, other.timeout, other.api_key) == (1, None, "k2")
            assert zap.with_options().timeout == 60.0
            await other.close()
            assert not zap._client.is_closed
            empty = Fake(httpx.Response(204))
            assert await async_client(empty).domains.delete("d1") is None
            r = await async_client(empty).domains.with_response.delete("d1")
            assert (r.status, r.data) == (204, None)
        assert not zap._client.is_closed  # passed in: the caller closes it
        async with zapsmtp.AsyncZapSMTP("k") as own:
            pool = own._client
        assert pool.is_closed

    asyncio.run(run())


# ---- errors -----------------------------------------------------------------

ERRORS: List[Any] = [
    (400, "validation_error", ValidationError), (401, "invalid_api_key", AuthenticationError),
    (403, "restricted_api_key", PermissionError), (404, "not_found", NotFoundError), (409, "conflict", ConflictError),
    (413, "payload_too_large", ValidationError), (422, "invalid_parameter", ValidationError),
    (429, "monthly_quota_exceeded", RateLimitError), (500, "internal_error", ServerError),
    (503, "unavailable", ServerError), (410, "link_expired", ZapSMTPError), (302, "moved", ZapSMTPError),
]


@pytest.mark.parametrize("status,code,cls", ERRORS)
def test_maps_errors(status: int, code: str, cls: Type[ZapSMTPError]) -> None:
    with pytest.raises(cls) as info:
        client(Fake(api_error(status, code)), max_retries=0).emails.get("x")
    err = info.value
    assert type(err) is cls
    assert (err.status, err.code, err.message, err.type, err.request_id) == (
        status, code, f"{code} happened", "invalid_request_error", "req_1")
    assert str(err) == f"{code} happened"
    assert err.headers is not None and err.headers["x-request-id"] == "req_1"
    assert err.body["error"]["code"] == code
    assert repr(err) == f"{cls.__name__}(message='{code} happened', status={status}, code='{code}', request_id='req_1')"


def test_reads_the_flat_error_shape_and_a_body_that_is_not_json() -> None:
    flat = Fake(json_response(422, {"statusCode": 422, "name": "missing_required_field", "message": "subject is required"}))
    with pytest.raises(ValidationError) as info:
        client(flat).emails.get("x")
    assert (info.value.code, info.value.message, info.value.type) == ("missing_required_field", "subject is required", "")
    with pytest.raises(ZapSMTPError) as text:
        client(Fake(httpx.Response(418, content=b"teapot"))).emails.get("x")
    assert (text.value.status, text.value.code, text.value.message, text.value.request_id, text.value.body) == (
        418, "", "HTTP 418", None, "teapot")
    with pytest.raises(ZapSMTPError) as listed:
        client(Fake(json_response(418, ["odd"]))).emails.get("x")
    assert listed.value.message == "HTTP 418"


def test_gives_retry_after_on_rate_limits() -> None:
    with pytest.raises(RateLimitError) as info:
        client(Fake(api_error(429, "monthly_quota_exceeded", {"Retry-After": "7"}))).emails.get("x")
    assert info.value.retry_after == 7
    assert RateLimitError("x").retry_after is None


def test_wraps_network_failures() -> None:
    with pytest.raises(ConnectionError) as info:
        client(Fake(httpx.ConnectError("connection refused")), max_retries=0).emails.get("x")
    assert info.value.status == 0
    assert info.value.message == "connection refused"
    assert isinstance(info.value.__cause__, httpx.ConnectError)
    with pytest.raises(ConnectionError, match="^RuntimeError$"):
        client(Fake(RuntimeError()), max_retries=0).emails.get("x")


def test_times_out() -> None:
    with pytest.raises(ConnectionError, match=r"^timed out after 0.02 s$"):
        client(Fake(httpx.ReadTimeout("read")), max_retries=0, timeout=0.02).emails.get("x")
    with pytest.raises(ConnectionError, match=r"^timed out$"):
        client(Fake(httpx.ReadTimeout("read")), max_retries=0, timeout=None).emails.get("x", timeout=None)
    fake = Fake(json_response(200, {}))
    client(fake).emails.get("x", timeout=5)
    assert fake.calls[0].extensions["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}


class Trickle(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A body that arrives slowly: each chunk moves the fake clock on."""

    def __init__(self, clock: List[float], chunks: List[bytes], step: float, fail: bool = False) -> None:
        self.clock, self.chunks, self.step, self.fail = clock, chunks, step, fail

    def _next(self) -> List[bytes]:
        if self.fail:
            raise httpx.ReadError("connection reset while reading body")
        return self.chunks

    def __iter__(self) -> Any:
        for chunk in self._next():
            self.clock[0] += self.step
            yield chunk

    async def __aiter__(self) -> Any:
        for chunk in self._next():
            self.clock[0] += self.step
            yield chunk


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    now = [1000.0]
    monkeypatch.setattr(_base_client, "_monotonic", lambda: now[0])
    return now


def test_the_per_attempt_timeout_also_bounds_reading_the_body(clock: List[float]) -> None:
    slow = Fake(lambda r: httpx.Response(200, stream=Trickle(clock, [b"{", b"}"], 15)))
    with pytest.raises(ConnectionError, match="timed out after 20 s"):
        client(slow, timeout=20, max_retries=0).emails.get("e1")
    assert client(slow, timeout=40).emails.get("e1").to_dict() == {}
    assert client(slow, timeout=None).emails.get("e1").to_dict() == {}

    async def run() -> None:
        with pytest.raises(ConnectionError, match="timed out after 20 s"):
            await async_client(slow, timeout=20, max_retries=0).emails.get("e1")
        assert (await async_client(slow, timeout=40).emails.get("e1")).to_dict() == {}
        silent = Fake(lambda r: httpx.Response(204, stream=Trickle(clock, [], 0)))
        assert await async_client(silent).domains.delete("d1") is None

    asyncio.run(run())


def test_a_broken_body_is_a_connection_error_and_a_safe_request_is_retried(clock: List[float]) -> None:
    def broken(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Trickle(clock, [], 0, fail=True))

    with pytest.raises(ConnectionError, match="connection reset while reading body") as info:
        client(Fake(broken), max_retries=0).emails.get("e1")
    assert isinstance(info.value.__cause__, httpx.ReadError)
    retried = Fake(broken, json_response(200, {"id": "e1"}))
    assert client(retried).emails.get("e1").id == "e1"
    assert len(retried.calls) == 2

    async def run() -> None:
        again = Fake(broken, json_response(200, {"id": "e1"}))
        assert (await async_client(again).emails.get("e1")).id == "e1"
        with pytest.raises(ConnectionError):
            await async_client(Fake(broken), max_retries=0).emails.get("e1")

    asyncio.run(run())


def test_cancellation_is_not_retried() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise asyncio.CancelledError()

    async def run() -> None:
        zap = zapsmtp.AsyncZapSMTP("k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with pytest.raises(asyncio.CancelledError):
            await zap.emails.get("x")

    asyncio.run(run())
    assert len(calls) == 1


# ---- retries ----------------------------------------------------------------


def test_retries_safe_requests_on_5xx_and_network_errors_honouring_retry_after(no_waiting: List[float]) -> None:
    fake = Fake(api_error(503, "service_unavailable", {"Retry-After": "2"}), httpx.ConnectError("reset"),
                json_response(200, {"id": "e1"}))
    assert client(fake).emails.get("e1").id == "e1"
    assert len(fake.calls) == 3
    assert no_waiting[0] == 2
    assert 0.5 <= no_waiting[1] <= 1


def test_retries_a_send_with_the_same_idempotency_key() -> None:
    fake = Fake(api_error(502, "bad_gateway"), json_response(200, {"id": "e1"}))
    client(fake).emails.send(to="b", subject="s")
    assert fake.header(0, "Idempotency-Key") == fake.header(1, "Idempotency-Key")


def test_does_not_retry_a_create_on_5xx_or_a_lost_connection_but_does_on_429() -> None:
    five = Fake(api_error(500, "internal_error"))
    with pytest.raises(ServerError):
        client(five).domains.create(name="a.test")
    assert len(five.calls) == 1
    lost = Fake(httpx.ConnectError("reset"))
    with pytest.raises(ConnectionError):
        client(lost).domains.create(name="a.test")
    assert len(lost.calls) == 1
    limited = Fake(api_error(429, "rate_limit_exceeded", {"Retry-After": "1"}), json_response(201, {"id": "d1"}))
    assert client(limited).domains.create(name="a.test").id == "d1"
    busy = Fake(api_error(409, "idempotency_request_in_progress"), json_response(200, {"id": "e1"}))
    assert client(busy).emails.send(to="b").id == "e1"


def test_gives_up_after_max_retries() -> None:
    fake = Fake(api_error(503, "service_unavailable", {"Retry-After": "0"}))
    with pytest.raises(ServerError):
        client(fake, max_retries=3).emails.get("x")
    assert len(fake.calls) == 4
    once = Fake(api_error(503, "service_unavailable"))
    with pytest.raises(ServerError):
        client(once).emails.get("x", max_retries=0)
    assert len(once.calls) == 1


def test_async_retries(no_waiting: List[float]) -> None:
    async def run() -> None:
        fake = Fake(api_error(503, "s", {"Retry-After": "3"}), httpx.ConnectError("reset"), json_response(200, {"id": "e1"}))
        assert (await async_client(fake).emails.get("e1")).id == "e1"
        assert len(fake.calls) == 3
        five = Fake(api_error(500, "internal_error"))
        with pytest.raises(ServerError):
            await async_client(five).domains.create(name="a.test")
        assert len(five.calls) == 1

    asyncio.run(run())
    assert no_waiting[0] == 3


def test_decides_what_is_worth_retrying() -> None:
    assert retryable(429, "overloaded", False)
    assert not retryable(429, "daily_quota_exceeded", True)
    assert not retryable(429, "thread_reply_limit", True)
    assert retryable(409, "idempotency_request_in_progress", False)
    assert not retryable(409, "idempotency_key_reused", True)
    assert retryable(503, "service_unavailable", True)
    assert retryable(408, "", True)
    assert not retryable(503, "service_unavailable", False)
    assert not retryable(422, "invalid_parameter", True)


def test_backs_off_exponentially_with_jitter_capped_or_as_retry_after_says() -> None:
    for attempt in range(40):
        cap = min(0.5 * 2 ** attempt, 8)
        assert cap / 2 <= backoff(attempt, None) <= cap
    assert backoff(0, 3) == 3
    assert backoff(0, 600) == 60


def test_reads_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zapsmtp._retry.time.time", lambda: 1767225600.0)  # 2026-01-01T00:00:00Z

    def ra(value: str) -> Any:
        return retry_after_seconds(httpx.Headers({"Retry-After": value}))

    assert ra("Thu, 01 Jan 2026 00:00:03 GMT") == 3
    assert ra("Thu, 01 Jan 2026 00:00:03 -0000") == 3  # no zone: UTC
    assert ra("Wed, 01 Jan 2025 00:00:00 GMT") == 0
    assert ra("1.5") == 1.5 and ra("-4") == 0
    assert ra("soon") is None and ra("inf") is None and ra("nan") is None
    assert retry_after_seconds(httpx.Headers()) is None and retry_after_seconds(None) is None


def test_a_rate_limit_error_reads_retry_after_as_a_date() -> None:
    with pytest.raises(RateLimitError) as info:
        client(Fake(api_error(429, "overloaded", {"Retry-After": "Thu, 01 Jan 2025 00:00:00 GMT"})), max_retries=0).emails.get("x")
    assert info.value.retry_after == 0


def test_the_real_sleeps_sleep() -> None:
    REAL_SLEEP(0)
    asyncio.run(REAL_ASYNC_SLEEP(0))


def test_not_given_is_a_falsy_singleton() -> None:
    import pickle

    assert zapsmtp.NotGiven() is zapsmtp.NOT_GIVEN
    assert not zapsmtp.NOT_GIVEN
    assert repr(zapsmtp.NOT_GIVEN) == "NOT_GIVEN"
    assert pickle.loads(pickle.dumps(zapsmtp.NOT_GIVEN)) is zapsmtp.NOT_GIVEN


def test_json_body_is_compact_utf8() -> None:
    fake = Fake(json_response(200, {"id": "e1"}))
    client(fake).emails.send(to="b", subject="Привет")
    assert fake.calls[0].content == json.dumps({"to": "b", "subject": "Привет"}, ensure_ascii=False, separators=(",", ":")).encode()
