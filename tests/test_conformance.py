"""Every API operation, called through the SDK against the conformance mock
(sdks/conformance/server.mjs), which checks the exact request it gets."""

from __future__ import annotations

import asyncio
import keyword
import re
from typing import Any, Dict, List, Tuple

import pytest

import zapsmtp
from zapsmtp import AsyncPage, BaseModel, SyncPage

from .conftest import load_json

CASES: List[Dict[str, Any]] = load_json("conformance/cases.json")
IR = load_json("spec/ir.json")
MODELS = {op["id"]: (op["returns"] or {}).get("model") for op in IR["operations"]}


def snake(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def py(name: str) -> str:
    return name + "_" if keyword.iskeyword(name) else name


def arguments(case: Dict[str, Any]) -> Tuple[List[Any], Dict[str, Any]]:
    call = case["call"]
    args: List[Any] = list(call["pathArgs"])
    kwargs: Dict[str, Any] = {}
    if call.get("items") is not None:
        args.append(call["items"])
        kwargs.update({py(k): v for k, v in call["options"].items()})
    elif call["params"]:
        kwargs.update({py(k): v for k, v in call["params"].items()})
    return args, kwargs


def method(root: Any, case: Dict[str, Any], with_response: bool) -> Any:
    target = root
    for part in case["call"]["resource"]:
        target = getattr(target, snake(part))
    if with_response:
        target = target.with_response
    return getattr(target, snake(case["call"]["method"]))


def check(result: Any, case: Dict[str, Any]) -> None:
    body = case["response"]["body"]
    model = MODELS[case["id"]]
    if body is None:
        assert result is None
    elif isinstance(result, (SyncPage, AsyncPage)):
        assert [item.to_dict() for item in result.data] == body["data"]
        assert all(type(item).__name__ == model for item in result.data)
        assert result.to_dict() == body
    else:
        assert isinstance(result, BaseModel)
        assert type(result).__name__ == model
        assert result.to_dict() == body
        for name in type(result)._fields:
            getattr(result, py(name))  # every declared field reads, present or not


def test_covers_every_operation_once() -> None:
    ids = [c["id"] for c in CASES]
    assert len(set(ids)) == len(ids)
    assert set(ids) == set(MODELS)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_sync(case: Dict[str, Any], conformance_url: str) -> None:
    with zapsmtp.ZapSMTP("zap_test_conformance", base_url=conformance_url, max_retries=0) as zap:
        args, kwargs = arguments(case)
        check(method(zap, case, False)(*args, **kwargs), case)
        response = method(zap, case, True)(*args, **kwargs)
        assert response.status == case["response"]["status"]
        assert response.request_id == "req_conformance"
        check(response.data, case)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_async(case: Dict[str, Any], conformance_url: str) -> None:
    async def run() -> None:
        async with zapsmtp.AsyncZapSMTP("zap_test_conformance", base_url=conformance_url, max_retries=0) as zap:
            args, kwargs = arguments(case)
            check(await method(zap, case, False)(*args, **kwargs), case)
            response = await method(zap, case, True)(*args, **kwargs)
            assert response.status == case["response"]["status"]
            assert response.request_id == "req_conformance"
            check(response.data, case)

    asyncio.run(run())
