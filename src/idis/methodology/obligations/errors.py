"""Stable error type for the obligation-register harness."""

from __future__ import annotations


class Gl02Error(Exception):
    """Failure identified only by a stable code.

    The message is the code. Callers print the code and not chained payloads,
    so a private record cannot leak through this exception.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
