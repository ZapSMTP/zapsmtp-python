"""Response objects: attribute access over the JSON the API answered."""

from __future__ import annotations

import copy
import json
import keyword
from typing import Any, ClassVar, Dict, Iterator, Mapping, Optional, Tuple, Type, TypeVar

__all__ = ["BaseModel", "construct"]

# How a field's value turns into models: None (kept as it is), ("ref", "Name"),
# ("list", spec) or ("map", spec).
FieldSpec = Optional[Tuple[str, Any]]

_registry: Dict[str, Type[BaseModel]] = {}

M = TypeVar("M", bound="BaseModel")


def attribute_name(field: str) -> str:
    """The attribute of an API field: ``from`` is ``from_``."""
    return field + "_" if keyword.iskeyword(field) else field


def _convert(value: Any, spec: FieldSpec) -> Any:
    if spec is None or value is None:
        return value
    kind, inner = spec
    if kind == "ref":
        return construct(_registry[inner], value)
    if kind == "list":
        return [_convert(v, inner) for v in value] if isinstance(value, list) else value
    return {k: _convert(v, inner) for k, v in value.items()} if isinstance(value, dict) else value


def construct(cls: Type[M], value: Any) -> Any:
    """``cls(value)`` for a JSON object; anything else is returned as it came."""
    return cls(value) if isinstance(value, dict) else value


class BaseModel:
    """An object the API answered, read-only.

    Fields are attributes (``email.id``); a field the answer left out is
    ``None``. Fields this SDK version does not know yet are kept: read them
    with ``model["field"]`` (or as attributes), and :meth:`to_dict` gives the
    JSON exactly as received. A Python keyword gets a trailing underscore:
    ``template.from_``.
    """

    _fields: ClassVar[Mapping[str, FieldSpec]] = {}
    _raw: Dict[str, Any]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _registry[cls.__name__] = cls

    def __init__(self, data: Mapping[str, Any]) -> None:
        raw = dict(data)
        object.__setattr__(self, "_raw", raw)
        for name, spec in self._fields.items():
            object.__setattr__(self, attribute_name(name), _convert(raw.get(name), spec))
        known = {attribute_name(name) for name in self._fields}
        for name, value in raw.items():
            unusable = not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_")
            if name in self._fields or name in known or unusable or hasattr(type(self), name):
                continue  # read it with model[name]
            object.__setattr__(self, name, value)

    def to_dict(self) -> Dict[str, Any]:
        """The JSON object as the API sent it (a deep copy), unknown fields included."""
        return copy.deepcopy(self._raw)

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """The object as JSON text."""
        return json.dumps(self._raw, indent=indent, ensure_ascii=False)

    def __getitem__(self, key: str) -> Any:
        if key in self._fields:
            return getattr(self, attribute_name(key))
        return self._raw[key]

    def __contains__(self, key: object) -> bool:
        return key in self._raw

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BaseModel):
            return NotImplemented
        return type(self) is type(other) and self._raw == other._raw

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in self._raw.items())
        return f"{type(self).__name__}({fields})"

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is read-only: use to_dict() for a mutable copy")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is read-only: use to_dict() for a mutable copy")

    def __copy__(self: M) -> M:
        return type(self)(self._raw)

    def __deepcopy__(self: M, memo: Dict[int, Any]) -> M:
        return type(self)(copy.deepcopy(self._raw, memo))

    def __reduce__(self) -> Tuple[Any, ...]:
        return (type(self), (self._raw,))
