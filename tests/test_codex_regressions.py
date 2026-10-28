"""Regressions Codex found in the Python SDK (.codex-test/20261028-091307-codex-sdk-python)."""

from __future__ import annotations

import asyncio
from typing import Any, List

import httpx
import pytest

from zapsmtp import ConnectionError, ValidationError, WebhookVerificationError, _base_client, verify_webhook
from zapsmtp.types import Email, Template

from .conftest import Fake, async_client, client


@pytest.mark.parametrize("identifier", [".", ".."])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_dot_ids_cannot_change_the_request_path(identifier: str, asynchronous: bool) -> None:
    fake = Fake(httpx.Response(204))

    async def run() -> None:
        await async_client(fake).domains.delete(identifier)

    with pytest.raises(ValidationError, match=f'id must not be "{identifier}"'):
        if asynchronous:
            asyncio.run(run())
        else:
            client(fake).domains.delete(identifier)
    assert not fake.calls


@pytest.mark.parametrize("model", [Email, Template])
@pytest.mark.parametrize("include_from", [False, True])
def test_an_unknown_field_cannot_replace_a_keyword_alias(model: Any, include_from: bool) -> None:
    raw = {"id": "one", "from_": "future field"}
    if include_from:
        raw["from"] = "sender@example.test"
    value = model(raw)
    assert value.to_dict() == raw
    assert value["from_"] == "future field"
    assert value.from_ == raw.get("from")
    assert value["from"] == raw.get("from")


class DelayedEOF(httpx.SyncByteStream, httpx.AsyncByteStream):
    """The body arrives promptly, but the end of the stream comes too late."""

    def __init__(self, clock: List[float], body: bytes) -> None:
        self.clock, self.body, self.closed = clock, body, False

    def __iter__(self) -> Any:
        if self.body:
            yield self.body
        self.clock[0] += 2

    async def __aiter__(self) -> Any:
        if self.body:
            yield self.body
        self.clock[0] += 2

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("body", [b"", b"{}"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_the_deadline_includes_waiting_for_the_end_of_the_body(
    monkeypatch: pytest.MonkeyPatch, body: bytes, asynchronous: bool
) -> None:
    clock = [100.0]
    monkeypatch.setattr(_base_client, "_monotonic", lambda: clock[0])
    stream = DelayedEOF(clock, body)
    fake = Fake(lambda request: httpx.Response(200, stream=stream))

    async def run() -> None:
        await async_client(fake, timeout=1, max_retries=0).emails.get("one")

    with pytest.raises(ConnectionError, match="timed out after 1 s"):
        if asynchronous:
            asyncio.run(run())
        else:
            client(fake, timeout=1, max_retries=0).emails.get("one")
    assert stream.closed
    assert len(fake.calls) == 1


def test_an_oversized_timestamp_is_a_verification_error() -> None:
    signature = "t=" + "9" * 5000 + ",v1=" + "0" * 64
    with pytest.raises(WebhookVerificationError):
        verify_webhook(b"{}", signature, "whsec_test", now=1700000000.0)
