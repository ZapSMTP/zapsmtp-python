"""Rules every SDK shares, found in reviews of the other SDKs."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List

import httpx
import pytest

from zapsmtp import RateLimitError, ValidationError
from zapsmtp._retry import retry_after_seconds

from .conftest import Fake, async_client, client, json_response

EMPTY_KEYS = ["", " ", "\t\n"]


@pytest.mark.parametrize("key", EMPTY_KEYS)
def test_an_empty_idempotency_key_is_refused_before_sending(key: str) -> None:
    fake = Fake(json_response(200, {"id": "e1", "data": []}))
    zap = client(fake)
    calls: List[Callable[[], Any]] = [
        lambda: zap.emails.send(to="b", idempotency_key=key),
        lambda: zap.emails.send_batch([{"to": "b"}], idempotency_key=key),
        lambda: zap.emails.send(to="b", extra_headers={"idempotency-key": key}),
        lambda: zap.emails.send(to="b", idempotency_key="ok", extra_headers={"IDEMPOTENCY-KEY": key}),
        lambda: zap.domains.create(name="a.test", extra_headers={"Idempotency-Key": key}),
    ]
    for call in calls:
        with pytest.raises(ValidationError) as info:
            call()
        assert (info.value.code, info.value.message, info.value.status) == (
            "invalid_parameter", "idempotency_key must not be empty", 0)

    async def run() -> None:
        with pytest.raises(ValidationError, match="idempotency_key must not be empty"):
            await async_client(fake).emails.send(to="b", idempotency_key=key)

    asyncio.run(run())
    assert fake.calls == []


def test_the_callers_idempotency_key_header_replaces_the_sdks_whatever_its_case() -> None:
    fake = Fake(json_response(200, {"id": "e1"}))
    client(fake).emails.send(to="b", extra_headers={"IDEMPOTENCY-key": "mine"})
    client(fake).emails.send(to="b", idempotency_key="arg", extra_headers={"idempotency-key": "header"})
    assert fake.calls[0].headers.get_list("idempotency-key") == ["mine"]
    assert fake.calls[1].headers.get_list("idempotency-key") == ["header"]


def test_cancellation_between_items_of_a_loaded_async_page_sends_nothing_more() -> None:
    fake = Fake(json_response(200, {"object": "list", "has_more": True, "data": [{"id": "a"}, {"id": "b"}]}))
    seen: List[str] = []

    async def walk() -> None:
        page = await async_client(fake).emails.list()
        async for email in page:
            seen.append(email.id)
            await asyncio.sleep(3600)  # cancelled here, between items

    async def run() -> None:
        task = asyncio.ensure_future(walk())
        while not seen:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert seen == ["a"]
    assert len(fake.calls) == 1


def test_a_number_json_cannot_hold_becomes_none() -> None:
    text = b'{"id": "e1", "big": 1e999, "small": -1e999, "nan": NaN, "inf": Infinity, "ninf": -Infinity, ' \
           b'"ok": 1.5, "list": [1e999, 2], "nested": {"x": NaN}}'
    email = client(Fake(httpx.Response(200, content=text))).emails.get("e1")
    raw: Dict[str, Any] = email.to_dict()
    assert raw == {"id": "e1", "big": None, "small": None, "nan": None, "inf": None, "ninf": None, "ok": 1.5,
                   "list": [None, 2], "nested": {"x": None}}
    assert email["big"] is None


ABSURD = ["1e999", "-1e999", "9" * 5000, "-" + "9" * 5000, "inf", "NaN", "1_000", "0x10", "\x00", "  ",
          "Thu, 01 Jan 99999 00:00:00 GMT", "Fri, 31 Dec 9999 23:59:59 -2359", "Mon, 01 Jan 0001 00:00:00 +2359",
          "Mon, 32 Jan 2026 00:00:00 GMT", "Mon, 01 Jan 2026 99:00:00 GMT", "é"]


@pytest.mark.parametrize("value", ABSURD, ids=[repr(v[:20]) for v in ABSURD])
def test_retry_after_never_raises(value: str) -> None:
    seconds = RateLimitError("x", headers=httpx.Headers({"retry-after": value.encode("utf-8")})).retry_after
    assert seconds is None or seconds >= 0


def test_retry_after_reads_numbers_as_javascript_does() -> None:
    def ra(value: str) -> Any:
        return retry_after_seconds(httpx.Headers({"Retry-After": value}))

    assert (ra(" 7 "), ra("+2"), ra(".5"), ra("3."), ra("1e1")) == (7, 2, 0.5, 3, 10)
    assert ra("1_000") is None and ra("inf") is None
