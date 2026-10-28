"""Lists: one page, the next page by each paging kind, every item (sdks/node/test/core.test.ts, codex-regressions)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx

from zapsmtp import AsyncPage, AsyncPageRequest, SyncPage

from .conftest import Fake, async_client, client, json_response


def page(ids: List[str], more: bool = True, **extra: Any) -> httpx.Response:
    return json_response(200, {"object": "list", "has_more": more, "data": [{"id": i} for i in ids], **extra})


def params(fake: Fake, i: int) -> Dict[str, str]:
    return dict(fake.calls[i].url.params)


def test_walks_every_page_with_before_the_last_id_keeping_the_filters() -> None:
    fake = Fake(page(["a", "b"]), page(["c"], False))
    ids = [e.id for e in client(fake).emails.list(limit=2, after="2026-01-01T00:00:00Z")]
    assert ids == ["a", "b", "c"]
    assert str(fake.calls[1].url) == "https://api.test/v1/emails?limit=2&after=2026-01-01T00%3A00%3A00Z&before=b"


def test_walks_suppressions_with_next_cursor() -> None:
    fake = Fake(page(["a"], next_cursor="cur1"), page(["b"], next_cursor=None))
    assert [s.id for s in client(fake).suppressions.list()] == ["a", "b"]
    assert str(fake.calls[1].url) == "https://api.test/v1/suppressions?after=cur1"
    assert len(fake.calls) == 2


def test_gives_one_page() -> None:
    first = client(Fake(page(["a"], extra_field=1))).emails.list()
    assert isinstance(first, SyncPage)
    assert [e.to_dict() for e in first.data] == [{"id": "a"}]
    assert first.has_next_page()
    assert (first.object, first.has_more, first.next_cursor) == ("list", True, None)
    assert first.to_dict()["extra_field"] == 1
    assert repr(first) == "SyncPage(data=[Email(id='a')], has_more=True)"


def test_has_next_page_makes_no_request() -> None:
    fake = Fake(page(["email-1"]))
    p = client(fake).emails.list()
    assert p.has_next_page() and p.has_next_page()
    assert len(fake.calls) == 1


def test_threads_page_by_last_activity_and_the_tie_breaking_id() -> None:
    last = {"id": "019aaa00-0000-7000-8000-000000000001", "last_message_at": "2026-10-28T08:00:00Z"}
    fake = Fake(json_response(200, {"object": "list", "has_more": True, "data": [last]}))
    client(fake).threads.list(limit=1, unread=True).get_next_page()
    assert params(fake, 1) == {"limit": "1", "unread": "true", "before": last["last_message_at"], "before_id": last["id"]}
    assert not client(Fake(page(["x"]))).threads.list().has_next_page()  # no last_message_at
    assert not client(Fake(page([]))).threads.list().has_next_page()


def test_received_attachments_page_forward_after_the_last_id() -> None:
    fake = Fake(page(["a1", "a2"]))
    client(fake).emails.receiving.attachments.list("m1", limit=2).get_next_page()
    assert params(fake, 1) == {"limit": "2", "after": "a2"}
    assert not client(Fake(page([]))).emails.receiving.attachments.list("m1").has_next_page()


def test_logs_page_back_with_before_or_forward_after_the_newest_entry() -> None:
    back = Fake(page(["l3", "l2"]))
    client(back).logs.list().get_next_page()
    assert params(back, 1) == {"before": "l2"}
    fwd = Fake(page(["l9", "l8"]))
    client(fwd).logs.list(after="l7").get_next_page()
    assert params(fwd, 1) == {"after": "l9"}
    assert not client(Fake(page([]))).logs.list(after="l7").has_next_page()
    assert not client(Fake(page([]))).logs.list().has_next_page()
    assert not client(Fake(page([]))).emails.receiving.list().has_next_page()


def test_stops_where_no_next_page_can_be_asked_for() -> None:
    no_id = client(Fake(json_response(200, {"object": "list", "has_more": True, "data": [{}, "odd"]}))).emails.list()
    assert not no_id.has_next_page()
    assert no_id.get_next_page() is None
    assert no_id.data[1] == "odd"  # type: ignore[comparison-overlap]
    unpaged = client(Fake(page(["a"]))).domains.list()
    assert unpaged.get_next_page() is None
    assert client(Fake(page(["a"], False))).emails.list().get_next_page() is None
    assert not client(Fake(page([], True, next_cursor=""))).suppressions.list().has_next_page()
    empty = client(Fake(json_response(200, {"object": "list"}))).emails.list()
    assert (empty.data, empty.has_more) == ([], False)
    odd = client(Fake(httpx.Response(200, content=b"not json"))).emails.list()
    assert (odd.data, odd.object, odd.to_dict()) == ([], "list", {})
    nulls = client(Fake(json_response(200, {"data": None, "has_more": True}))).emails.list()
    assert nulls.data == [] and not nulls.has_next_page()


def test_iter_pages() -> None:
    fake = Fake(page(["a"]), page(["b"], False))
    pages = list(client(fake).emails.list().iter_pages())
    assert [[e.id for e in p.data] for p in pages] == [["a"], ["b"]]


def test_with_response_gives_the_first_page() -> None:
    fake = Fake(page(["a"], False))
    r = client(fake).emails.with_response.list()
    assert r.status == 200 and [e.id for e in r.data] == ["a"]


def test_async_paging() -> None:
    async def run() -> None:
        fake = Fake(page(["a", "b"]), page(["c"], False))
        request = async_client(fake).emails.list(limit=2)
        assert isinstance(request, AsyncPageRequest)
        assert len(fake.calls) == 0  # nothing is sent before it is awaited
        assert [e.id async for e in request] == ["a", "b", "c"]
        assert str(fake.calls[1].url) == "https://api.test/v1/emails?limit=2&before=b"

        fake2 = Fake(page(["a"]), page(["b"], False))
        first = await async_client(fake2).emails.list()
        assert isinstance(first, AsyncPage) and first.has_next_page()
        assert [[e.id for e in p.data] async for p in first.iter_pages()] == [["a"], ["b"]]
        second = await async_client(Fake(page(["z"], False))).emails.list()
        assert await second.get_next_page() is None
        assert [e.id async for e in second] == ["z"]

        cursor = Fake(page(["a"], next_cursor="c1"), page(["b"], False))
        assert [s.id async for s in async_client(cursor).suppressions.list()] == ["a", "b"]
        r = await async_client(Fake(page(["q"], False))).emails.with_response.list()
        assert r.request_id == "req_1" and r.data.data[0].id == "q"

    asyncio.run(run())
