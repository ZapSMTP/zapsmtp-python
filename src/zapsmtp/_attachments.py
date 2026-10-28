"""Attachment helpers: the content is sent base64-encoded."""

from __future__ import annotations

import base64
import os
from typing import Optional, Union

from ._generated.params import OutgoingAttachment

__all__ = ["attachment", "attachment_from_file"]

_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".html": "text/html",
    ".ics": "text/calendar",
    ".json": "application/json",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def attachment(
    filename: str,
    content: Union[bytes, bytearray, memoryview, str],
    content_type: Optional[str] = None,
    content_id: Optional[str] = None,
) -> OutgoingAttachment:
    """An attachment from bytes or text (text is sent as UTF-8).

    The content type follows the file name's extension unless given.
    ``content_id`` makes it inline: reference it as ``cid:<content_id>``.
    """
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    result: OutgoingAttachment = {"filename": filename, "content": base64.b64encode(data).decode("ascii")}
    kind = content_type if content_type is not None else _TYPES.get(os.path.splitext(filename)[1].lower())
    if kind:
        result["content_type"] = kind
    if content_id:
        result["content_id"] = content_id
    return result


def attachment_from_file(
    path: Union[str, "os.PathLike[str]"],
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    content_id: Optional[str] = None,
) -> OutgoingAttachment:
    """An attachment read from a file; ``filename`` defaults to the file's name."""
    with open(path, "rb") as f:
        data = f.read()
    return attachment(filename if filename is not None else os.path.basename(os.fspath(path)), data, content_type, content_id)
