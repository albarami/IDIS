"""Typed transaction requirements for staged run-boundary writes.

This module is deliberately dependency-free.  Producers may request transaction
semantics with immutable values, while the persistence boundary remains the only
owner of SQL rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class TransactionIsolationLevel(StrEnum):
    """Isolation levels that a staged producer may describe."""

    REPEATABLE_READ = "REPEATABLE_READ"


class TransactionAccessMode(StrEnum):
    """Whether the requested transaction may mutate durable state."""

    READ_WRITE = "READ_WRITE"
    READ_ONLY = "READ_ONLY"


@dataclass(frozen=True, slots=True)
class TransactionCharacteristics:
    """Immutable, SQL-free transaction characteristics."""

    isolation_level: TransactionIsolationLevel
    access_mode: TransactionAccessMode


REPEATABLE_READ_WRITE: Final = TransactionCharacteristics(
    isolation_level=TransactionIsolationLevel.REPEATABLE_READ,
    access_mode=TransactionAccessMode.READ_WRITE,
)


class TransactionCharacteristicsError(ValueError):
    """A staged-write marker is malformed, conflicting, or unsafe."""


__all__ = [
    "REPEATABLE_READ_WRITE",
    "TransactionAccessMode",
    "TransactionCharacteristics",
    "TransactionCharacteristicsError",
    "TransactionIsolationLevel",
]
