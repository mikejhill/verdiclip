"""Idle production scrolling composition with explicit runtime eligibility.

Eligibility checks are not evidence that a target/provider is supported. Existing
discovery and preservation guards still decide each user-requested capture.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QGuiApplication

from verdiclip.capture.scroll.native_backend import (
    NativeBackendOptions,
    UiaFrameBackend,
)
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaProcess
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.exceptions import CaptureError

if TYPE_CHECKING:
    from verdiclip.capture.scroll.workflow import ScrollWorkflow
    from verdiclip.shell.controller import AppController

HOST_PLATFORM = sys.platform
HELPER_ASSETS = (
    "uia_helper.ps1",
    "uia_native_request.ps1",
    "uia_nofocus_interop.cs",
    "uia_nofocus.cs",
    "uia_desktop.cs",
)


class ScrollingStorageError(CaptureError):
    """Private spool allocation failed before any adapter was constructed."""


def scrolling_unavailable_reason() -> str | None:
    """Require native Windows Qt and the installed helper files, without launch."""
    if HOST_PLATFORM != "win32":
        return "Scrolling capture requires Windows."
    if (
        QGuiApplication.instance() is None
        or QGuiApplication.platformName() != "windows"
    ):
        return "Scrolling capture requires the native Windows Qt platform."
    try:
        command = UiaProcess._default_command()
        helper = Path(command[-1])
        if not all((helper.parent / asset).is_file() for asset in HELPER_ASSETS):
            return "Scrolling capture helper assets are unavailable."
    except (CaptureError, OSError):
        return "Scrolling capture helper assets are unavailable."
    return None


def compose_scrolling(
    controller: AppController, *, data_root: Path
) -> ScrollWorkflow | None:
    """Attach idle owned services; this function never starts native work.

    Call before controller.start. No runtime
    override, preview manifest, driver or settings migration supplies eligibility.
    """
    if scrolling_unavailable_reason() is not None:
        return None
    root = data_root.resolve()
    spool = root / "scroll-spool"
    if not spool.resolve().is_relative_to(root):
        raise ValueError("Scrolling spool must remain inside application data")
    try:
        spool.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ScrollingStorageError(
            "Could not prepare private scrolling storage"
        ) from error
    process = UiaProcess(qualified=True, use_local_pipe=True)
    adapter = UiaAdapter(process=process)
    process.setParent(adapter)

    def backend(reference: UiaReference, owner: UiaAdapter) -> UiaFrameBackend:
        if owner is not adapter:
            raise ValueError("Scrolling backend requires its own discovery adapter")
        return UiaFrameBackend(
            reference,
            owner,
            options=NativeBackendOptions(
                preservation_qualified=True,
                input_unchanged=lambda: controller.capture.input_active,
            ),
        )

    try:
        return controller.configure_scrolling(adapter, backend, spool)
    except ValueError:
        # Only the declared pre-attachment rejection proves adapter is unowned.
        if adapter.parent() is None and not adapter.busy:
            adapter.deleteLater()
        raise
