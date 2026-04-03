"""Memory usage tests for VerdiClip."""

from __future__ import annotations

import tracemalloc


class TestIdleMemory:
    def test_idle_memory(self) -> None:
        tracemalloc.start()

        import verdiclip
        import verdiclip.config  # noqa: F401

        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        # Importing core modules should not exceed 50 MB
        assert peak_mb < 50, f"Peak memory {peak_mb:.1f} MB exceeds 50 MB limit"
