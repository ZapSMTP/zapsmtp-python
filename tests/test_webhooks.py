"""Webhook signatures: every shared vector (sdks/spec/webhook-vectors.json) and the header shapes frameworks use."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List

import pytest

import zapsmtp
from zapsmtp import WebhookEvent, WebhookVerificationError, verify_webhook, verify_webhook_events

from .conftest import load_json

VECTORS = load_json("spec/webhook-vectors.json")["vectors"]
V = VECTORS[0]


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(v: Dict[str, Any]) -> None:
    def one() -> WebhookEvent:
        return verify_webhook(v["payload"], {"Zap-Signature": v["header"]}, v["secret"], v["tolerance"], v["now"])

    def all_() -> List[WebhookEvent]:
        return verify_webhook_events(v["payload"], {"Zap-Signature": v["header"]}, v["secret"], v["tolerance"], v["now"])

    if not v["valid"]:
        with pytest.raises(WebhookVerificationError):
            one()
        with pytest.raises(WebhookVerificationError):
            all_()
    elif v.get("batch"):
        with pytest.raises(WebhookVerificationError, match="batch of events: use verify_webhook_events"):
            one()
        assert [e.to_dict() for e in all_()] == json.loads(v["payload"])
    else:
        assert one().to_dict() == json.loads(v["payload"])
        assert [e.to_dict() for e in all_()] == [json.loads(v["payload"])]


def test_has_the_batch_and_oversized_timestamp_vectors() -> None:
    names = {v["name"] for v in VECTORS}
    assert any(v.get("batch") for v in VECTORS) and "oversized timestamp" in names


class FrameworkHeaders:
    """Like Werkzeug's EnvironHeaders: items(), but not a Mapping."""

    def items(self) -> Any:
        return [("Content-Type", "application/json"), ("Zap-Signature", V["header"])]


def test_takes_every_header_shape_bytes_and_the_client_method() -> None:
    payload, secret, now = V["payload"], V["secret"], V["now"]
    want = json.loads(payload)
    shapes: Any = [
        V["header"],
        {"zap-signature": V["header"]},
        {"Zap-Signature": [V["header"]]},
        [(b"zap-signature", V["header"].encode())],
        [("x-other", "1"), ("ZAP-SIGNATURE", V["header"])],
        FrameworkHeaders(),
    ]
    for headers in shapes:
        assert verify_webhook(payload, headers, secret, now=now).to_dict() == want
    for body in (payload.encode(), bytearray(payload.encode()), memoryview(payload.encode())):
        event = verify_webhook(body, V["header"], secret, now=now)
        assert (event.id, event.type, event.data["subject"]) == ("evt_1", "email.delivered", "Привет ✓")
    zap = zapsmtp.ZapSMTP("k")
    assert zap.webhooks.verify(payload, V["header"], secret, now=now).id == "evt_1"
    assert zapsmtp.AsyncZapSMTP("k").webhooks.verify(payload, V["header"], secret, 300, now).id == "evt_1"


def test_checks_against_the_clock_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zapsmtp._webhooks.time.time", lambda: V["now"] + 10.7)
    assert verify_webhook(V["payload"], V["header"], V["secret"]).id == "evt_1"
    monkeypatch.setattr("zapsmtp._webhooks.time.time", lambda: V["now"] + 301.0)
    with pytest.raises(WebhookVerificationError, match="too old"):
        verify_webhook(V["payload"], V["header"], V["secret"])


def sign(body: str, secret: str = "whsec_x", t: int = 1700000000) -> str:
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()


def test_refuses_what_it_cannot_verify() -> None:
    with pytest.raises(WebhookVerificationError, match="Missing Zap-Signature"):
        verify_webhook("{}", {}, "s")
    with pytest.raises(WebhookVerificationError, match="Missing Zap-Signature"):
        verify_webhook("{}", {"zap-signature": ""}, "s")
    with pytest.raises(WebhookVerificationError, match="Missing webhook secret"):
        verify_webhook("{}", "t=1,v1=00", "")
    with pytest.raises(WebhookVerificationError, match="Malformed"):
        verify_webhook("{}", "t=١٢,v1=00", "s", now=12)  # digits outside ASCII are not a timestamp
    with pytest.raises(WebhookVerificationError, match="Malformed"):
        verify_webhook("{}", "garbage,v1", "s")
    with pytest.raises(WebhookVerificationError, match="not JSON") as info:
        verify_webhook("not json", sign("not json"), "whsec_x", now=1700000000)
    assert isinstance(info.value.__cause__, ValueError)
    for body in ("42", "null", "[1]", "[[]]", '[{}, 2]'):
        with pytest.raises(WebhookVerificationError, match="not an event"):
            verify_webhook_events(body, sign(body), "whsec_x", now=1700000000)
        if not body.startswith("["):
            with pytest.raises(WebhookVerificationError, match="not an event"):
                verify_webhook(body, sign(body), "whsec_x", now=1700000000)
    for long in ("9" * 13, "9" * 5000):  # unix seconds have at most 12 digits
        with pytest.raises(WebhookVerificationError, match="Malformed"):
            verify_webhook(b"{}", f"t={long},v1=" + "0" * 64, "whsec_x", now=1700000000.0)
    assert [e.to_dict() for e in zapsmtp.ZapSMTP("k").webhooks.verify_events("{}", sign("{}"), "whsec_x", now=1700000000)] == [{}]
    batch = '[{"id":"evt_1"},{"id":"evt_2"}]'
    events = zapsmtp.AsyncZapSMTP("k").webhooks.verify_events(batch, sign(batch), "whsec_x", 300, 1700000000)
    assert [e.id for e in events] == ["evt_1", "evt_2"]
    with pytest.raises(WebhookVerificationError, match="does not match"):
        verify_webhook("{}", sign("{}") + ",v1=zz", "whsec_y", now=1700000000)
