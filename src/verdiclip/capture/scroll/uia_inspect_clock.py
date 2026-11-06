"""Isolated diagnostic branch: fixed first-request child-clock receipts only."""

from dataclasses import dataclass

LABELS = (
    "armed",
    "request_read",
    "json_parse",
    "json_parsed",
    "dispatch",
    "admission",
    "admitted",
    "inspect_desktop",
    "inspect_root",
    "root_call",
    "root_return",
    "root_owned",
    "root_wrapped",
    "inspect_process",
)
PREFIX = b"VERDICLIP_INSPECT_CLOCK="
LF, CR, MAX_LINE, MAX_DIGITS = 10, 13, 96, 10


@dataclass(frozen=True, slots=True)
class ChildClockStamp:
    """Child-relative time, never a parent/child synchronized clock claim."""

    stage: str
    microseconds: int | None


class InspectClockReceipt:
    """First-write retention with bounded lines, labels and elapsed values."""

    def __init__(self) -> None:
        self.events: list[ChildClockStamp] = []
        self.unavailable = False
        self._line = bytearray()
        self._skip = False
        self._last = 0

    def feed(self, data: bytes) -> None:
        """Consume only the existing bounded stderr stream, without decoding data."""
        for byte in data:
            if byte == LF:
                self._accept(bytes(self._line))
                self._line.clear()
                self._skip = False
            elif byte != CR and not self._skip:
                self._line.append(byte)
                if len(self._line) > MAX_LINE:
                    if self._line.startswith(PREFIX):
                        self.unavailable = True
                    self._line.clear()
                    self._skip = True

    def _accept(self, line: bytes) -> None:
        if not line.startswith(PREFIX):
            return
        if len(self.events) >= len(LABELS):
            self.unavailable = True
            return
        payload = line[len(PREFIX) :]
        label, separator, number = payload.partition(b":")
        allowed = {name.encode(): name for name in LABELS}
        if label not in allowed or not separator:
            self.unavailable = True
            return
        stage = allowed[label]
        if any(event.stage == stage for event in self.events):
            self.unavailable = True
            return
        if number == b"-1":
            value = None
            self.unavailable = True
        elif number.isdigit() and len(number) <= MAX_DIGITS:
            value = int(number)
            if not self._last <= value <= 2**31 - 1:
                self.unavailable = True
                return
            self._last = value
        else:
            self.unavailable = True
            return
        self.events.append(ChildClockStamp(stage, value))
