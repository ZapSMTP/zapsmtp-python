"""Request building helpers shared by the client and the generated resources."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import IO, Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote, urlencode

from ._exceptions import ValidationError
from ._models import BaseModel
from ._types import NotGiven

__all__ = ["FileContent", "MultipartForm", "dump_json", "encode_query", "expand_path", "loads", "multipart_form", "path_segment", "strip"]

FileContent = Union[bytes, bytearray, memoryview, IO[bytes], "os.PathLike[str]"]
"""An upload: bytes, a file opened in binary mode, or a path (``pathlib.Path``)."""


_PATH_PARAM = re.compile(r"\{(\w+)\}")


def path_segment(name: str, value: Any) -> str:
    """One path segment, escaped.

    An empty value, "." or ".." can be no id: URL handling would resolve them
    into another path, so they are refused before anything is sent.
    """
    text = "" if value is None else str(value)
    if text in ("", ".", ".."):
        what = "empty" if text == "" else f'"{text}"'
        raise ValidationError(f"{name} must not be {what}", code="invalid_parameter")
    return quote(text, safe="!'()*")


def expand_path(template: str, params: Mapping[str, Any]) -> str:
    """The path of a template such as ``/emails/{id}``, each parameter escaped."""
    return _PATH_PARAM.sub(lambda m: path_segment(m.group(1), params.get(m.group(1))), template)


def strip(values: Mapping[str, Any]) -> Dict[str, Any]:
    """``values`` without the parameters the caller did not pass."""
    return {k: v for k, v in values.items() if not isinstance(v, NotGiven)}


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def encode_query(query: Mapping[str, Any]) -> str:
    """The query string: lists repeat their key, maps become ``key[name]=value``."""
    pairs: List[Tuple[str, str]] = []
    for key, value in query.items():
        if isinstance(value, Mapping):
            pairs.extend((f"{key}[{k}]", _query_value(v)) for k, v in value.items())
        elif isinstance(value, (list, tuple, Set)):
            pairs.extend((key, _query_value(v)) for v in value)
        elif value is not None and not isinstance(value, NotGiven):
            pairs.append((key, _query_value(value)))
    return urlencode(pairs)


def _finite(text: str) -> Optional[float]:
    value = float(text)
    return value if math.isfinite(value) else None


def loads(text: str) -> Any:
    """JSON from the API. A number JSON cannot hold (1e999, NaN, Infinity) becomes None."""
    return json.loads(text, parse_float=_finite, parse_constant=lambda _: None)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (Sequence, Set)) and not isinstance(value, (bytes, bytearray)):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dump_json(body: Any) -> bytes:
    """The JSON request body."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")


@dataclass(frozen=True)
class MultipartForm:
    """A multipart/form-data body: one file and plain fields."""

    field: str
    filename: str
    content: bytes
    fields: Dict[str, str]


def multipart_form(
    field: str, file: FileContent, *, name: Optional[str] = None, filename: Optional[str] = None
) -> MultipartForm:
    """The form of an upload. The file name sent is ``filename``, else
    ``name``, else the file's own name, else ``"image"``."""
    own: Optional[str] = None
    if isinstance(file, (bytes, bytearray, memoryview)):
        content = bytes(file)
    elif isinstance(file, os.PathLike):
        path = os.fspath(file)
        with open(path, "rb") as f:
            content = f.read()
        own = os.path.basename(path)
    elif isinstance(file, str):
        raise TypeError("file must be bytes, a file opened in binary mode, or a pathlib.Path, not str")
    else:
        content = file.read()
        if not isinstance(content, bytes):
            raise TypeError("file must be opened in binary mode")
        file_name = getattr(file, "name", None)
        own = os.path.basename(file_name) if isinstance(file_name, str) else None
    chosen = next((n for n in (filename, name, own) if n is not None), "image")
    fields = {"name": name} if name is not None else {}
    return MultipartForm(field=field, filename=chosen, content=content, fields=fields)
