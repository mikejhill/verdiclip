"""Independent startup cleanup without replacing an existing operation error."""

from __future__ import annotations

from contextlib import ExitStack
from types import TracebackType
from typing import Literal, override


class StartupLifetime(ExitStack):
    """Retire every callback; keep the operation's identity on cleanup failure.

    An ordinary cleanup failure becomes the primary error's explicit cause.
    Cleanup control exceptions continue to propagate through ExitStack with the
    operation error in their chain, rather than being caught by AppError handling.
    """

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Use the standard independent-callback semantics before propagation."""
        try:
            super().__exit__(exc_type, exc, traceback)
        except Exception as cleanup_error:
            # ExitStack's last failing callback wins. Do not bury an earlier
            # cleanup interrupt underneath a later ordinary cleanup exception.
            controls: list[BaseException] = []
            seen: list[BaseException] = []
            current = cleanup_error.__context__
            while current is not None and current is not exc:
                if any(current is previous for previous in seen):
                    break
                seen.append(current)
                if not isinstance(current, Exception):
                    controls.append(current)
                current = current.__context__
            if controls:
                failures = [exc] if exc is not None else []
                raise BaseExceptionGroup(
                    "Startup operation and cleanup controls",
                    [*failures, *controls, cleanup_error],
                ) from None
            if exc is not None:
                raise exc from cleanup_error
            raise
        return False
