from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Union

import httpx
import pytest

import zapsmtp
from zapsmtp import _base_client

SDKS = Path(__file__).resolve().parents[2]


def load_json(relative: str) -> Any:
    return json.loads((SDKS / relative).read_text("utf-8"))


@pytest.fixture(scope="session")
def conformance_url() -> Iterator[str]:
    """The conformance mock (sdks/conformance/server.mjs): ZAP_CONFORMANCE_URL, or one started here."""
    url = os.environ.get("ZAP_CONFORMANCE_URL")
    if url:
        yield url
        return
    node = shutil.which("node") or str(Path.home() / ".local/node/bin/node")
    proc = subprocess.Popen(
        [node, str(SDKS / "conformance/server.mjs"), "0"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.startswith("listening "), line
        yield f"http://127.0.0.1:{int(line.split()[1])}/v1"
    finally:
        proc.kill()
        proc.wait()


REAL_SLEEP = _base_client._sleep
REAL_ASYNC_SLEEP = _base_client._async_sleep


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """Backoff sleeps are recorded instead of slept."""
    slept: List[float] = []

    async def async_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_base_client, "_sleep", slept.append)
    monkeypatch.setattr(_base_client, "_async_sleep", async_sleep)
    return slept


Answer = Union[httpx.Response, Exception, Callable[[httpx.Request], httpx.Response]]


class Fake:
    """A transport that answers from a list (the last answer repeats) and records each request."""

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.calls: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.calls.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            # a fresh copy each time: a response body can be read once
            return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)
        return answer(request)

    def header(self, i: int, name: str) -> Optional[str]:
        return self.calls[i].headers.get(name)

    def json(self, i: int) -> Any:
        return json.loads(self.calls[i].content)


def json_response(status: int, body: Any, headers: Optional[dict] = None) -> httpx.Response:  # type: ignore[type-arg]
    return httpx.Response(
        status,
        headers={"Content-Type": "application/json", "X-Request-Id": "req_1", **(headers or {})},
        content=json.dumps(body).encode(),
    )


def api_error(status: int, code: str, headers: Optional[dict] = None) -> httpx.Response:  # type: ignore[type-arg]
    message = f"{code} happened"
    body = {"statusCode": status, "name": code, "message": message,
            "error": {"code": code, "message": message, "type": "invalid_request_error"}}
    return json_response(status, body, headers)


def client(fake: Fake, **options: Any) -> zapsmtp.ZapSMTP:
    options.setdefault("base_url", "https://api.test/v1/")
    return zapsmtp.ZapSMTP("zap_test_key", http_client=httpx.Client(transport=httpx.MockTransport(fake)), **options)


def async_client(fake: Fake, **options: Any) -> zapsmtp.AsyncZapSMTP:
    options.setdefault("base_url", "https://api.test/v1/")
    return zapsmtp.AsyncZapSMTP(
        "zap_test_key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake)), **options
    )
