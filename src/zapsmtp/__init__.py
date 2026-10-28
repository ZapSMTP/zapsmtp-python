"""The official Python library for the ZapSMTP email API.

    from zapsmtp import ZapSMTP

    client = ZapSMTP()  # reads ZAP_API_KEY
    client.emails.send(from_="Acme <hello@acme.com>", to="jane@example.org", subject="Hi", text="Hello")
"""

from . import types
from ._attachments import attachment, attachment_from_file
from ._base_client import AsyncAPIClient, SyncAPIClient
from ._exceptions import (
    AuthenticationError,
    ConflictError,
    ConnectionError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    ServerError,
    ValidationError,
    WebhookVerificationError,
    ZapSMTPError,
)
from ._generated.client import AsyncZapSMTP, ZapSMTP
from ._models import BaseModel
from ._pagination import AsyncPage, AsyncPageRequest, SyncPage
from ._response import APIResponse
from ._types import NOT_GIVEN, NotGiven
from ._version import __version__
from ._webhooks import WebhookEvent, verify_webhook, verify_webhook_events

__all__ = [
    "APIResponse",
    "AsyncAPIClient",
    "AsyncPage",
    "AsyncPageRequest",
    "AsyncZapSMTP",
    "AuthenticationError",
    "BaseModel",
    "ConflictError",
    "ConnectionError",
    "NOT_GIVEN",
    "NotFoundError",
    "NotGiven",
    "PermissionError",
    "RateLimitError",
    "ServerError",
    "SyncAPIClient",
    "SyncPage",
    "ValidationError",
    "WebhookEvent",
    "WebhookVerificationError",
    "ZapSMTP",
    "ZapSMTPError",
    "__version__",
    "attachment",
    "attachment_from_file",
    "types",
    "verify_webhook",
    "verify_webhook_events",
]
