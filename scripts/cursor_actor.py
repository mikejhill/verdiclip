"""Owned disposable-host cursor actor; no UIA, hooks, saved pixels, or real users."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import sys

from verdiclip.platform.cursor import CursorVisibilityLease


def require_host() -> None:
    """Refuse all cursor writes outside the explicitly selected disposable job."""
    if sys.platform != "win32" or any(
        os.environ.get(name) != value
        for name, value in (
            ("GITHUB_ACTIONS", "true"),
            ("RUNNER_ENVIRONMENT", "github-hosted"),
            ("VERDICLIP_CURSOR_FEASIBILITY", "1"),
        )
    ):
        raise RuntimeError("Disposable hosted cursor fixture required")


class Client:
    """Separate owned Magnification client, not a product preservation mechanism."""

    def __init__(self) -> None:
        self.api = ctypes.WinDLL("Magnification", use_last_error=True)
        self.api.MagInitialize.argtypes = []
        self.api.MagInitialize.restype = w.BOOL
        self.api.MagUninitialize.argtypes = []
        self.api.MagUninitialize.restype = w.BOOL
        self.api.MagShowSystemCursor.argtypes = [w.BOOL]
        self.api.MagShowSystemCursor.restype = w.BOOL
        self.initialized = bool(self.api.MagInitialize())
        if not self.initialized:
            raise RuntimeError("Owned client initialization refused")

    def show(self, *, visible: bool) -> bool:
        """Change only the explicitly owned test baseline, without getter claims."""
        return bool(self.api.MagShowSystemCursor(visible))

    def close(self) -> bool:
        """Uninitialize once; showing/restoring requires an explicit separate step."""
        if not self.initialized:
            return True
        self.initialized = False
        return bool(self.api.MagUninitialize())


class Session:
    """A single role owns a single production lease or independent test client."""

    def __init__(self, role: str) -> None:
        if role not in {"lease", "client"}:
            raise ValueError("Unknown actor role")
        self.lease = CursorVisibilityLease() if role == "lease" else None
        self.client: Client | None = None

    def command(self, command: str) -> dict[str, object]:
        """Return fixed API metadata, never a claim of rendered visibility."""
        ok, attempted = True, False
        if command == "acquire" and self.lease is not None:
            self.lease.acquire()
            attempted = self.lease.suppression_attempted
        elif command == "close" and self.lease is not None:
            ok = self.lease.close()
        elif command in {"hide", "show"} and self.lease is None:
            if self.client is None:
                self.client = Client()
            ok = self.client.show(visible=command == "show")
        elif command == "close" and self.client is not None:
            ok = self.client.close()
        elif command not in {"noop", "quit"}:
            raise ValueError("Invalid owned command")
        return {"ok": ok, "attempted": attempted}

    def close(self) -> None:
        """Ordinary child cleanup, deliberately bypassed by forced termination."""
        try:
            if self.lease is not None:
                self.lease.close()
        finally:
            if self.client is not None:
                self.client.close()


def main() -> int:
    """Bound command count; supervisor termination intentionally bypasses finally."""
    require_host()
    session = Session(next(iter(sys.argv[1:]), ""))
    try:
        for _ in range(12):
            command = sys.stdin.readline(32).strip()
            if not command:
                return 0
            try:
                receipt = session.command(command)
            except Exception:  # noqa: BLE001 - fixed failure receipt, nonzero exit
                receipt = {"ok": False, "attempted": False}
            sys.stdout.write(json.dumps(receipt) + "\n")
            sys.stdout.flush()
            if command == "quit" or receipt["ok"] is not True:
                return 0 if receipt["ok"] is True else 1
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
