"""Small types shared by the core and the generated code."""

from __future__ import annotations

from typing import Optional, Union

__all__ = ["NOT_GIVEN", "NotGiven", "Timeout"]


class NotGiven:
    """The type of :data:`NOT_GIVEN`: an argument the caller did not pass.

    ``None`` is a value of its own (it sends JSON ``null``), so leaving a
    parameter out is told apart with this sentinel.
    """

    _instance: Optional[NotGiven] = None

    def __new__(cls) -> NotGiven:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"

    def __reduce__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()
"""Default of every optional API parameter: the parameter is not sent."""

Timeout = Optional[float]
"""Seconds one attempt may take, the response body read included; ``None`` waits forever."""

TimeoutArg = Union[float, None, NotGiven]
