"""Explicit private-preview composition; construction never starts native work."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from verdiclip.capture.scroll.native_backend import (
    NativeBackendOptions,
    UiaFrameBackend,
)
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaProcess
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.capture.scroll.workflow import ScrollWorkflow

if TYPE_CHECKING:
    from verdiclip.shell.controller import AppController


def native_preview_adapter() -> UiaAdapter:
    """Own one idle pipe transport; no process or endpoint exists until inspect."""
    process = UiaProcess(qualified=True, use_local_pipe=True)
    adapter = UiaAdapter(process=process)
    process.setParent(adapter)
    return adapter


def compose_preview_scrolling(
    controller: AppController,
    *,
    qualified: bool,
    bundle_root: Path,
    adapter_factory: Callable[[], UiaAdapter] = native_preview_adapter,
) -> ScrollWorkflow | None:
    """Wire prepared services only for an explicitly qualified private profile.

    The manifest is produced after qualification; this function is not evidence
    that native preservation works. The default preview keeps this path disabled.
    """
    if not qualified:
        return None
    root = bundle_root.resolve()
    spool = root / "spool"
    if not spool.resolve().is_relative_to(root):
        raise ValueError("Preview spool must remain inside its bundle")
    spool.mkdir(parents=True, exist_ok=True)
    adapter = adapter_factory()

    def backend(reference: UiaReference, owner: UiaAdapter) -> UiaFrameBackend:
        if owner is not adapter:
            raise ValueError("Preview backend requires its own discovery adapter")
        return UiaFrameBackend(
            reference,
            owner,
            options=NativeBackendOptions(
                preservation_qualified=True,
                input_unchanged=lambda: controller.capture.input_active,
                frame_parent=spool,
            ),
        )

    try:
        return controller.configure_scrolling(adapter, backend, spool)
    except ValueError:
        # A declared pre-attachment rejection leaves a newly constructed idle
        # adapter unowned. Unexpected/partial Qt attachment retains its graph.
        if adapter.parent() is None and not adapter.busy:
            adapter.deleteLater()
        raise
