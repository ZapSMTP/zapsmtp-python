"""Webhook signature verification.

Every request ZapSMTP sends carries ``Zap-Signature: t=<unix seconds>,v1=<hex>``,
where v1 is HMAC-SHA256 over ``"<t>.<raw body>"`` keyed with the whole secret
(its ``whsec_`` prefix included). Two v1 entries are sent for 24 hours after a
secret rotation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from ._exceptions import WebhookVerificationError
from ._models import BaseModel
from ._utils import loads

__all__ = ["WebhookEvent", "verify_webhook", "verify_webhook_events"]

# unix seconds: at most 12 digits, so the arithmetic stays exact and cheap
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")

Headers = Union[Mapping[str, Any], Iterable[Tuple[str, Any]]]
"""Request headers: a mapping (dict, Django ``request.headers``, Starlette
``Headers``, Werkzeug ``EnvironHeaders``) or (name, value) pairs."""


class WebhookEvent(BaseModel):
    """A webhook request body."""

    id: Optional[str]
    """evt_...; absent on email.received, thread.created and thread.replied (see the Zap-Event-Id header)."""
    type: str
    created_at: str
    data: Dict[str, Any]

    _fields = {"id": None, "type": None, "created_at": None, "data": None}


def _header(headers: Headers, name: str) -> Optional[str]:
    items: Any = getattr(headers, "items", None)
    pairs: Any = items() if callable(items) else headers
    for key, value in pairs:
        if isinstance(key, bytes):
            key = key.decode("latin-1")
        if key.lower() != name:
            continue
        if isinstance(value, bytes):
            return value.decode("latin-1")
        if isinstance(value, (list, tuple)):
            return ",".join(value)
        return str(value)
    return None


def _verified(
    payload: Union[str, bytes, bytearray, memoryview],
    headers: Union[Headers, str],
    secret: str,
    tolerance: float,
    now: Optional[float],
) -> Any:
    """Checks the signature and returns the parsed body: an event, or a list of them for a batch."""
    signature = headers if isinstance(headers, str) else _header(headers, "zap-signature")
    if not signature:
        raise WebhookVerificationError("Missing Zap-Signature header")
    if not secret:
        raise WebhookVerificationError("Missing webhook secret")
    timestamp = ""
    v1: List[str] = []
    for part in signature.split(","):
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key == "t":
            timestamp = value
        elif key == "v1":
            v1.append(value)
    if not _TIMESTAMP.fullmatch(timestamp) or not v1:
        raise WebhookVerificationError("Malformed Zap-Signature header")
    current = int(time.time()) if now is None else now
    if abs(current - int(timestamp)) > tolerance:
        raise WebhookVerificationError("Webhook signature is too old (or from the future)")
    body = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    want = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).digest()
    matched = False
    for candidate in v1:
        # every entry is compared, in constant time
        if _HEX64.fullmatch(candidate) and hmac.compare_digest(bytes.fromhex(candidate), want):
            matched = True
    if not matched:
        raise WebhookVerificationError("Webhook signature does not match")
    try:
        return loads(body.decode("utf-8"))
    except ValueError as err:
        raise WebhookVerificationError("Webhook body is not JSON") from err


def verify_webhook(
    payload: Union[str, bytes, bytearray, memoryview],
    headers: Union[Headers, str],
    secret: str,
    tolerance: float = 300,
    now: Optional[float] = None,
) -> WebhookEvent:
    """Checks a webhook request's signature and returns its event.

    A webhook with batch delivery sends a JSON array of events: use
    :func:`verify_webhook_events` for it (it takes single events too).

    Args:
        payload: The request body exactly as received (bytes or text), before
            any JSON parsing.
        headers: The request headers, or the Zap-Signature header's value.
        secret: The webhook's signing secret, ``whsec_...``.
        tolerance: The oldest signature accepted, in seconds (default 300).
        now: The time to check against, in unix seconds (default: now).

    Raises:
        WebhookVerificationError: the header is missing or malformed, the
            signature does not match, it is older (or newer) than
            ``tolerance``, or the body is not one event.
    """
    body = _verified(payload, headers, secret, tolerance, now)
    if isinstance(body, list):
        raise WebhookVerificationError("The body is a batch of events: use verify_webhook_events")
    if not isinstance(body, dict):
        raise WebhookVerificationError("Webhook body is not an event")
    return WebhookEvent(body)


def verify_webhook_events(
    payload: Union[str, bytes, bytearray, memoryview],
    headers: Union[Headers, str],
    secret: str,
    tolerance: float = 300,
    now: Optional[float] = None,
) -> List[WebhookEvent]:
    """Checks a webhook request's signature and returns its events.

    Every event of a batch (a webhook with ``batch: true``), or the one event
    of any other request as a list of one. Arguments and errors as for
    :func:`verify_webhook`.
    """
    body = _verified(payload, headers, secret, tolerance, now)
    events = body if isinstance(body, list) else [body]
    if not all(isinstance(event, dict) for event in events):
        raise WebhookVerificationError("Webhook body is not an event")
    return [WebhookEvent(event) for event in events]
