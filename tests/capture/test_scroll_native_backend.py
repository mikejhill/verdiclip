"""Production backend and binding against fictional pixels and a JSON-only peer."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_core import Documents
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.scroll.binding import (
    BindingOptions,
    BindingState,
    ScrollCaptureBinding,
)
from verdiclip.capture.scroll.models import ScrollTarget
from verdiclip.capture.scroll.native_backend import (
    NativeBackendOptions,
    UiaFrameBackend,
)
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess, UiaRequest
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.geometry import Rect


class NativeFixture:
    """A finite 96-row fictional document, with no desktop or input API calls."""

    def __init__(self, path: Path) -> None:
        self.source = Documents.image(width=96, height=96)
        source_path = path / "fictional-source.png"
        assert self.source.save(str(source_path))
        self.frames = path / "private-frames"
        self.frames.mkdir()
        self.mode = path / "peer-mode.txt"
        self.mode.write_text("normal", encoding="utf-8")
        self.trace = path / "peer-trace.txt"
        raw = Payload.reference()
        raw["view_size"] = 50
        self.reference = UiaReference.decode(raw)
        self.guard = True
        helper = path / "pixel_peer.py"
        helper.write_text(
            "import json,sys\n"
            "from pathlib import Path\n"
            "from PySide6.QtGui import QImage\n"
            "source=QImage(sys.argv[1]); position=0\n"
            "print(json.dumps({'ready':True,'protocol':1}),flush=True)\n"
            "for line in sys.stdin:\n"
            " request=json.loads(line); payload=request['payload']\n"
            " action=request['action']\n"
            " with open(sys.argv[3],'a',encoding='utf-8') as log:\n"
            "  log.write(action+'\\n')\n"
            " mode=Path(sys.argv[2]).read_text(encoding='utf-8')\n"
            " reference=payload['reference']\n"
            " if action=='step': position=min(100,position+50)\n"
            " if action=='restore': position=payload['restore_percent']\n"
            " if mode=='sample_drift' and action=='sample' and position>0:\n"
            "  position+=1\n"
            " reference['vertical_percent']=position\n"
            " result={'reference':reference,'at_end':position>=100}\n"
            " if action=='sample':\n"
            "  area=payload['viewport']; top=round(position*48/100)\n"
            "  image=source.copy(int(area[0]),top,int(area[2]),int(area[3]))\n"
            "  if mode=='sample_corrupt':\n"
            "   Path(payload['image_path']).write_bytes(b'not-png')\n"
            "  else: assert image.save(payload['image_path'])\n"
            "  result.update(viewport=area,image_path=payload['image_path'])\n"
            " reply={'request_id':request['request_id'],'ok':True,'result':result}\n"
            " print(json.dumps(reply),flush=True)\n",
            encoding="utf-8",
        )
        self.process = UiaProcess(
            helper_command=(
                sys.executable,
                str(helper),
                str(source_path),
                str(self.mode),
                str(self.trace),
            ),
            qualified=True,
        )
        self.adapter = UiaAdapter(process=self.process)
        self.backend = UiaFrameBackend(
            self.reference,
            self.adapter,
            options=NativeBackendOptions(
                preservation_qualified=True,
                input_unchanged=lambda: self.guard,
                frame_parent=self.frames,
            ),
        )
        self.target = ScrollTarget(
            self.reference.identity, "Fictional document", Rect(0, 0, 96, 48)
        )


@pytest.fixture
def native(qtbot: QtBot, tmp_path: Path) -> Iterator[NativeFixture]:
    """Own and clean every harmless peer process and private frame file."""
    fixture = NativeFixture(tmp_path)
    yield fixture
    fixture.backend.cancel()
    qtbot.waitUntil(lambda: not fixture.process.busy)
    fixture.backend.deleteLater()
    fixture.adapter.deleteLater()
    fixture.process.deleteLater()
    assert not list(fixture.frames.iterdir())


def test_production_backend_captures_stitches_restores_and_cleans(
    qtbot: QtBot, tmp_path: Path, native: NativeFixture
) -> None:
    """The real binding/backend/pixel decoder composes only verified fixture rows."""
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, native.backend, options=BindingOptions(interval_ms=5)
    )
    with qtbot.waitSignal(binding.review_ready, timeout=8000):
        assert binding.start_confirmed(native.target)
    assert binding.state is BindingState.REVIEW
    assert session.accept().image == native.source
    assert not list(native.frames.iterdir())
    assert native.process.worker_pid is None
    session.close()


def test_unqualified_backend_never_starts_pixel_or_provider_work(
    qtbot: QtBot, tmp_path: Path, native: NativeFixture
) -> None:
    """The default capability gate survives even an explicitly injectable transport."""
    backend = UiaFrameBackend(native.reference, native.adapter)
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(session, backend)
    with qtbot.waitSignal(binding.failed):
        assert not binding.start_confirmed(native.target)
    assert native.process.worker_pid is None
    assert not list(native.frames.iterdir())
    assert session.state is ScrollState.IDLE


def test_interference_is_latched_and_cannot_restore_or_scroll(
    qtbot: QtBot, tmp_path: Path, native: NativeFixture
) -> None:
    """Unchanged target geometry does not override lost native input ownership."""
    session = ScrollSession(tmp_path)
    native.guard = False
    binding = ScrollCaptureBinding(session, native.backend)
    with qtbot.waitSignal(binding.failed):
        binding.start_confirmed(native.target)
    assert binding.state is BindingState.FAILED
    native.guard = True
    with qtbot.waitSignal(native.backend.verified) as verified:
        native.backend.verify(native.target, 99)
    assert not verified.args[0].safe_to_scroll
    assert native.process.worker_pid is None
    assert not list(native.frames.iterdir())


@pytest.mark.parametrize("action", ["stop", "cancel"])
def test_mid_sample_stop_or_cancel_finishes_without_late_pixels(
    qtbot: QtBot, tmp_path: Path, native: NativeFixture, action: str
) -> None:
    """A requested stop retains only the prefix; Cancel discards it and owned files."""
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, native.backend, options=BindingOptions(interval_ms=5)
    )
    samples: list[object] = []
    moves: list[object] = []
    acted: list[bool] = []
    native.adapter.sampled.connect(lambda request, _sample: samples.append(request))

    def observed(request: UiaRequest, _result: object) -> None:
        if request.action is UiaAction.STEP:
            moves.append(request)

    def interrupt(*, busy: bool) -> None:
        if busy and len(samples) >= 3 and moves and not acted:
            acted.append(True)
            if action == "stop":
                binding.stop()
            else:
                binding.cancel()

    native.adapter.observed.connect(observed)
    native.process.busy_changed.connect(lambda busy: interrupt(busy=busy))
    signal = binding.review_ready if action == "stop" else binding.cancelled
    with qtbot.waitSignal(signal, timeout=8000):
        binding.start_confirmed(native.target)
    assert acted == [True]
    qtbot.waitUntil(lambda: not native.process.busy)
    assert not list(native.frames.iterdir())
    if action == "stop":
        assert binding.state is BindingState.REVIEW
        assert session.accept().image == native.source.copy(0, 0, 96, 48)
        session.close()
    else:
        assert binding.state is BindingState.CANCELLED
        assert session.state is ScrollState.CANCELLED


def test_programmatic_sample_drift_cannot_become_owned_or_restored(
    qtbot: QtBot, tmp_path: Path, native: NativeFixture
) -> None:
    """Unchanged physical-input latch cannot authorize overwriting external movement."""
    native.mode.write_text("sample_drift", encoding="utf-8")
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, native.backend, options=BindingOptions(interval_ms=5)
    )
    with qtbot.waitSignal(binding.review_ready, timeout=8000) as ready:
        binding.start_confirmed(native.target)
    assert "could not be safely restored" in ready.args[1]
    assert session.accept().image == native.source.copy(0, 0, 96, 48)
    assert "restore" not in native.trace.read_text(encoding="utf-8").splitlines()
    session.close()


def test_bad_pixel_file_fails_cleanly_without_false_capture(
    qtbot: QtBot, tmp_path: Path, native: NativeFixture
) -> None:
    """Malformed private PNG data cannot escape a callback or manufacture an image."""
    native.mode.write_text("sample_corrupt", encoding="utf-8")
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, native.backend, options=BindingOptions(interval_ms=5)
    )
    with qtbot.waitSignal(binding.failed, timeout=8000):
        binding.start_confirmed(native.target)
    qtbot.waitUntil(lambda: not native.process.busy)
    assert session.state is ScrollState.IDLE
    assert not list(native.frames.iterdir())
