"""Base classes of the generated API resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base_client import AsyncAPIClient, SyncAPIClient

__all__ = ["AsyncResource", "SyncResource"]


class SyncResource:
    """A group of API operations, e.g. ``client.emails``."""

    def __init__(self, client: SyncAPIClient) -> None:
        self._client = client


class AsyncResource:
    """A group of API operations of the async client, e.g. ``client.emails``."""

    def __init__(self, client: AsyncAPIClient) -> None:
        self._client = client
