"""Allowlisted failure locations/categories, never exception text or native IDs."""

STAGES = frozenset(
    {
        "unknown",
        "owner-capture-menu-refusal",
        "owner-cursor-refusal",
        "destroy-hwnd-liveness",
        "crash-hwnd-liveness",
        "drain-global-state",
        "client-spawn",
        "client-ready",
        "client-identity",
        "client-snapshot",
        "baseline-read",
        "client-foreground",
        "client-physical-hit",
        "client-thread-cursor",
        "client-class-cursor",
        "client-global-cursor",
        "client-cursor-flags",
        "client-capture-menu",
        "owner-spawn",
        "owner-ready",
        "owner-identity",
        "owned-state-read",
        "owned-policy-read",
        "observe-command",
        "observed-reply",
        "observed-policy-read",
        "destroy-command",
        "destroyed-reply",
        "destroy-state-read",
        "destroy-policy-read",
        "drain-command",
        "drained-reply",
        "drain-state-read",
        "drain-policy-read",
        "finish-command",
        "late-state-read",
        "late-policy-read",
        "done-reply",
        "close-command",
        "owner-exit",
        "crash-process",
        "crash-state-read",
        "crash-policy-read",
        "owner-cleanup",
        "client-cleanup",
        "child-bootstrap",
        "client-native-state",
        "client-window-construction",
        "client-api-bind",
        "client-load-cursor",
        "client-show",
        "client-set-foreground",
        "client-settle",
        "client-ready-report",
        "client-command",
        "client-global-snapshot",
        "client-thread-snapshot",
        "client-class-snapshot",
        "client-snapshot-report",
        "client-request-budget",
        "owner-native-state",
        "owner-baseline",
        "owner-recipients",
        "owner-broker-construction",
        "owner-surface-construction",
        "owner-broker-start",
        "owner-broker-ready",
        "owner-guard",
        "owner-show",
        "owner-natural-admission",
        "owner-owned-report",
        "owner-observe-command",
        "owner-observation",
        "owner-observed-report",
        "owner-destroy-command",
        "owner-destroy",
        "owner-first-restoration",
        "owner-destroyed-report",
        "owner-drain-command",
        "owner-release",
        "owner-retirement-wait",
        "owner-drained-report",
        "owner-finish-command",
        "owner-late-observation",
        "owner-done-report",
        "owner-close-command",
        "peer-identity-shape",
        "peer-identity",
        "missing-owned-write",
        "owned-global-state",
        "owned-client-policy",
        "crash-first-restoration",
        "crash-client-policy",
        "owned-preservation",
        "observed-client-policy",
        "destroy-first-restoration",
        "destroy-client-policy",
        "broker-drain",
        "drain-client-policy",
        "late-global-state",
        "late-client-policy",
        "owner-final-preservation",
        "disposable-only",
        "desktop-unavailable",
    }
)
KINDS = frozenset(
    {
        "timeout",
        "os-error",
        "value-error",
        "type-error",
        "check",
        "runtime-error",
        "other",
    }
)


def kind(error: Exception) -> str:
    """Map exception classes to a finite public vocabulary without using messages."""
    for cls, label in (
        (TimeoutError, "timeout"),
        (OSError, "os-error"),
        (ValueError, "value-error"),
        (TypeError, "type-error"),
        (CheckError, "check"),
        (RuntimeError, "runtime-error"),
    ):
        if isinstance(error, cls):
            return label
    return "other"


class CheckError(RuntimeError):
    """A named predicate failed; its label is restricted to reviewed constants."""

    def __init__(self, stage: str) -> None:
        self.stage = stage if stage in STAGES else "unknown"
        super().__init__(self.stage)


class ChildError(RuntimeError):
    """Only an allowlisted child failure crosses into a public parent receipt."""

    def __init__(self, stage: object, category: object) -> None:
        self.stage = stage if isinstance(stage, str) and stage in STAGES else "unknown"
        self.category = (
            category if isinstance(category, str) and category in KINDS else "other"
        )
        super().__init__("Owned child failed")


class Evidence:
    """Retain the first failure even if cleanup subsequently fails elsewhere."""

    def __init__(self) -> None:
        self.stage = "unknown"
        self.first: dict[str, str] | None = None

    def mark(self, stage: str) -> None:
        """Mark only fixed source-controlled stages."""
        if stage not in STAGES:
            raise ValueError("Unrecognized diagnostic stage")
        self.stage = stage

    def fail(self, error: Exception) -> dict[str, str]:
        """Freeze the first location and class without retaining exception text."""
        if self.first is None:
            stage = (
                error.stage
                if isinstance(error, (CheckError, ChildError))
                else self.stage
            )
            category = error.category if isinstance(error, ChildError) else kind(error)
            self.first = {"failure_stage": stage, "error_kind": category}
        return dict(self.first)
