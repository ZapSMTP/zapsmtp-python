"""Lists: one page at a time, or every item of every page."""

from __future__ import annotations

import copy
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Generator,
    Generic,
    Iterator,
    List,
    Optional,
    Type,
    TypeVar,
)

from ._models import BaseModel, construct

__all__ = ["AsyncPage", "AsyncPageRequest", "SyncPage"]

T = TypeVar("T", bound=BaseModel)

Query = Dict[str, Any]


def _id(item: Any) -> Any:
    return item.get("id") if isinstance(item, dict) else None


def next_query(query: Query, items: List[Any], next_cursor: Any, paging: Optional[str]) -> Optional[Query]:
    """The query of the page after this one, keeping the caller's filters, or None.

    ``paging`` is how the list asks for more (sdks/spec/names.json):

    - ``before``: ``before`` = the last item's id (newest first);
    - ``after``: ``after`` = the last item's id (oldest first);
    - ``logs``: paged with ``after``, the next newer page starts after the
      page's newest entry (its first); otherwise ``before`` = the last id;
    - ``thread``: ``before`` = the last thread's ``last_message_at`` and
      ``before_id`` = its id;
    - ``cursor``: ``after`` = ``next_cursor``.
    """
    first = items[0] if items else None
    last = items[-1] if items else None
    if paging == "cursor":
        return {**query, "after": next_cursor} if next_cursor else None
    if paging == "before":
        return {**query, "before": _id(last)} if _id(last) else None
    if paging == "after":
        return {**query, "after": _id(last)} if _id(last) else None
    if paging == "logs":
        if "after" in query:
            return {**query, "after": _id(first)} if _id(first) else None
        return {**query, "before": _id(last)} if _id(last) else None
    if paging == "thread":
        at = last.get("last_message_at") if isinstance(last, dict) else None
        return {**query, "before": at, "before_id": _id(last)} if _id(last) and at else None
    return None


class _Page(Generic[T]):
    object: str
    """Always ``"list"``."""
    data: List[T]
    """The items of this page."""
    has_more: bool
    """Whether the API has more items after this page."""
    next_cursor: Optional[str]
    """The cursor of the next page, for lists paged by cursor."""

    def __init__(self, body: Any, cast: Type[T], query: Query, paging: Optional[str]) -> None:
        raw: Dict[str, Any] = body if isinstance(body, dict) else {}
        items = raw.get("data")
        items = items if isinstance(items, list) else []
        self._raw = raw
        self.object = raw.get("object", "list")
        self.data = [construct(cast, item) for item in items]
        self.has_more = bool(raw.get("has_more"))
        self.next_cursor = raw.get("next_cursor")
        self._next = next_query(query, items, self.next_cursor, paging) if self.has_more else None

    def has_next_page(self) -> bool:
        """Whether there is a next page to ask for. Makes no request."""
        return self._next is not None

    def to_dict(self) -> Dict[str, Any]:
        """The page as the API sent it."""
        return copy.deepcopy(self._raw)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(data={self.data!r}, has_more={self.has_more!r})"


class SyncPage(_Page[T]):
    """One page of a list.

    ``page.data`` holds this page's items. Iterating the page walks every item
    of this page and of every page after it, asking the API as it goes::

        for email in client.emails.list(status="bounced"):
            print(email.id)
    """

    def __init__(
        self, body: Any, cast: Type[T], query: Query, paging: Optional[str], load: Callable[[Query], SyncPage[T]]
    ) -> None:
        super().__init__(body, cast, query, paging)
        self._load = load

    def get_next_page(self) -> Optional[SyncPage[T]]:
        """The next page, or None after the last one."""
        return self._load(self._next) if self._next is not None else None

    def iter_pages(self) -> Iterator[SyncPage[T]]:
        """This page and every page after it."""
        page: Optional[SyncPage[T]] = self
        while page is not None:
            yield page
            page = page.get_next_page()

    def __iter__(self) -> Iterator[T]:
        for page in self.iter_pages():
            yield from page.data


class AsyncPage(_Page[T]):
    """One page of a list, for the async client.

    ``page.data`` holds this page's items; ``async for`` over the page walks
    every item of this page and of every page after it.
    """

    def __init__(
        self,
        body: Any,
        cast: Type[T],
        query: Query,
        paging: Optional[str],
        load: Callable[[Query], Awaitable[AsyncPage[T]]],
    ) -> None:
        super().__init__(body, cast, query, paging)
        self._load = load

    async def get_next_page(self) -> Optional[AsyncPage[T]]:
        """The next page, or None after the last one."""
        return await self._load(self._next) if self._next is not None else None

    async def iter_pages(self) -> AsyncIterator[AsyncPage[T]]:
        """This page and every page after it."""
        page: Optional[AsyncPage[T]] = self
        while page is not None:
            yield page
            page = await page.get_next_page()

    async def _items(self) -> AsyncIterator[T]:
        async for page in self.iter_pages():
            for item in page.data:
                yield item

    def __aiter__(self) -> AsyncIterator[T]:
        return self._items()


class AsyncPageRequest(Generic[T]):
    """A list request of the async client.

    ``await`` it for the first page, or ``async for`` over it for every item
    of every page. Nothing is sent until then; each ``await`` asks again.
    """

    def __init__(self, load: Callable[[], Awaitable[AsyncPage[T]]]) -> None:
        self._load = load

    def __await__(self) -> Generator[Any, None, AsyncPage[T]]:
        return self._load().__await__()

    def __aiter__(self) -> AsyncIterator[T]:
        return _RequestItems(self)


class _RequestItems(AsyncIterator[T]):
    """The items of every page of a list request; the first page is asked for with the first item."""

    def __init__(self, request: AsyncPageRequest[T]) -> None:
        self._request = request
        self._items: Optional[AsyncIterator[T]] = None

    async def __anext__(self) -> T:
        if self._items is None:
            self._items = (await self._request).__aiter__()
        return await self._items.__anext__()
