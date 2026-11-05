"""Immutable correlation values shared by the UIA facade and its worker lease."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UiaAction(StrEnum):
    """The worker exposes no invoke, activation, click or arbitrary script action."""

    INSPECT = "inspect"
    OBSERVE = "observe"
    SAMPLE = "sample"
    STEP = "step"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class UiaRequest:
    """One immutable, bounded JSON operation with an unpredictable correlation ID."""

    identity: str
    action: UiaAction
    encoded: bytes
