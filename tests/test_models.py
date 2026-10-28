"""Response models: attribute access, unknown fields kept, never failing on a missing or extra field."""

from __future__ import annotations

import copy
import pickle
from typing import Any, Dict, List, Optional

import pytest

from zapsmtp import BaseModel
from zapsmtp._models import construct
from zapsmtp.types import Email, Template, Webhook


class Leaf(BaseModel):
    name: Optional[str]
    _fields = {"name": None}


class Tree(BaseModel):
    leaf: Optional[Leaf]
    leaves: Optional[List[Leaf]]
    by_key: Optional[Dict[str, List[Leaf]]]
    _fields = {"leaf": ("ref", "Leaf"), "leaves": ("list", ("ref", "Leaf")), "by_key": ("map", ("list", ("ref", "Leaf")))}


def test_converts_nested_objects_and_tolerates_anything() -> None:
    t = Tree({"leaf": {"name": "a"}, "leaves": [{"name": "b"}, 3], "by_key": {"k": [{"name": "c"}]}})
    assert isinstance(t.leaf, Leaf) and t.leaf.name == "a"
    assert t.leaves is not None and t.leaves[0] == Leaf({"name": "b"}) and t.leaves[1] == 3
    assert t.by_key is not None and t.by_key["k"][0].name == "c"
    odd = Tree({"leaf": "x", "leaves": {"not": "a list"}, "by_key": ["not a map"]})
    assert (odd.leaf, odd.leaves, odd.by_key) == ("x", {"not": "a list"}, ["not a map"])
    empty = Tree({})
    assert (empty.leaf, empty.leaves, empty.by_key) == (None, None, None)
    assert construct(Leaf, [1]) == [1]


def test_keeps_unknown_fields_and_renames_keywords() -> None:
    t = Template({"id": "t1", "from": "a@x.test", "brand_new": 1, "to_dict": "shadow", "_private": 2, "two words": 3,
                  "class": 4})
    assert t.from_ == "a@x.test"
    assert t["from"] == "a@x.test"
    assert t.brand_new == 1  # type: ignore[attr-defined]
    assert callable(t.to_dict)
    assert t["to_dict"] == "shadow" and t["_private"] == 2 and t["two words"] == 3 and t["class"] == 4
    assert not hasattr(t, "_private") and not hasattr(t, "class")
    assert "brand_new" in t and "nope" not in t
    assert list(t)[:2] == ["id", "from"]
    with pytest.raises(KeyError):
        t["nope"]


def test_is_read_only_comparable_and_printable() -> None:
    e = Email({"id": "e1", "to": ["a@x.test"]})
    with pytest.raises(AttributeError, match="read-only"):
        e.id = "x"
    with pytest.raises(AttributeError, match="read-only"):
        del e.id
    assert e == Email({"id": "e1", "to": ["a@x.test"]})
    assert e != Email({"id": "e2"})
    assert e != Template({"id": "e1", "to": ["a@x.test"]})
    assert e != {"id": "e1"}
    assert repr(e) == "Email(id='e1', to=['a@x.test'])"
    assert e.to_json() == '{"id": "e1", "to": ["a@x.test"]}'
    assert Email({"subject": "é"}).to_json(indent=1) == '{\n "subject": "é"\n}'


def test_to_dict_is_a_deep_copy_and_models_copy_and_pickle() -> None:
    e = Email({"id": "e1", "metadata": {"k": "v"}})
    d = e.to_dict()
    d["metadata"]["k"] = "changed"
    assert e.to_dict()["metadata"]["k"] == "v"
    for other in (copy.copy(e), copy.deepcopy(e), pickle.loads(pickle.dumps(e))):
        assert other == e and other is not e


def test_echoed_request_objects_stay_dicts() -> None:
    w = Webhook({"id": "w", "filter": {"domains": ["a"]}})
    assert w.filter == {"domains": ["a"]}


def test_construct_returns_non_objects_as_they_came() -> None:
    value: Any = "plain"
    assert construct(Email, value) == "plain"
