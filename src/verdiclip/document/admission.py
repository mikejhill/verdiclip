"""Small metadata for an ordinary screenshot admitted to an editor tab."""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from verdiclip.capture.models import Capture


@dataclass(frozen=True, slots=True)
class CaptureProvenance:
    """Identify a completed screenshot without retaining another pixel payload."""

    capture_id: str
    title: str
    created_at: datetime

    @classmethod
    def from_capture(cls, capture: Capture) -> "CaptureProvenance":
        """Assign tab identity after the original capture lifecycle has completed."""
        return cls(uuid4().hex, capture.title, capture.taken_at)
