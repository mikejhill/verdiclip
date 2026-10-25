"""Typed UIA operations against a harmless local JSON peer, never Windows UIA."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaProcess
from verdiclip.capture.scroll.uia_protocol import (
    UiaDiscovery,
    UiaObservation,
    UiaReference,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point


@pytest.fixture
def adapter(tmp_path: Path) -> UiaAdapter:
    """Use a fake provider with real asynchronous transport and typed decoding."""
    helper = tmp_path / "typed_peer.py"
    helper.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        f"discovery=json.loads({json.dumps(Payload.discovery(Payload.nodes()))!r})\n"
        "action=request['action']; payload=request['payload']\n"
        "if payload['window_handle']==99: sys.exit(3)\n"
        "if action=='inspect': result=discovery['result']\n"
        "else:\n"
        " reference=payload['reference']\n"
        " if action=='step': reference['vertical_percent']+=12.5\n"
        " if action=='restore':\n"
        "  reference['vertical_percent']=payload['restore_percent']\n"
        " if reference['runtime_id']=='bad': reference['runtime_id']='replaced'\n"
        " result={'reference':reference,'at_end':reference['vertical_percent']>=100}\n"
        "print(json.dumps({'request_id':request['request_id'],'ok':True,'result':result}))\n",
        encoding="utf-8",
    )
    process = UiaProcess(helper_command=(sys.executable, str(helper)), qualified=True)
    client = UiaAdapter(process=process)
    client.failed.connect(lambda _request, message: sys.stderr.write(message + "\n"))
    return client


def test_discovery_is_read_only_and_returns_typed_candidates(
    qtbot: QtBot, adapter: UiaAdapter
) -> None:
    """A point can propose a container without implicitly starting movement."""
    with qtbot.waitSignal(adapter.discovered) as found:
        request = adapter.inspect(1, Point(92, 10))
    assert found.args[0] is request
    assert isinstance(found.args[1], UiaDiscovery)
    assert len(found.args[1].references) == 1
    assert not adapter.busy


def test_explicit_step_and_restore_preserve_typed_target_identity(
    qtbot: QtBot, adapter: UiaAdapter
) -> None:
    """Movement metrics update while identity and geometry remain immutable."""
    original = UiaReference.decode(Payload.reference())
    with qtbot.waitSignal(adapter.observed) as moved:
        adapter.step(original, confirmed=True)
    observation = moved.args[1]
    assert isinstance(observation, UiaObservation)
    assert observation.reference.identity == original.identity
    assert observation.reference.vertical_percent == 12.5
    with qtbot.waitSignal(adapter.observed) as restored:
        adapter.restore(observation.reference, 0, confirmed=True)
    assert restored.args[1].reference.vertical_percent == 0


def test_unconfirmed_movement_never_launches_transport(adapter: UiaAdapter) -> None:
    """A valid target reference is not consent to operate its control."""
    reference = UiaReference.decode(Payload.reference())
    with pytest.raises(CaptureError, match="explicit Start"):
        adapter.step(reference, confirmed=False)
    with pytest.raises(CaptureError, match="confirmation"):
        adapter.restore(reference, 0, confirmed=False)
    assert not adapter.busy


def test_same_handle_replaced_provider_fails_visibly(
    qtbot: QtBot, adapter: UiaAdapter
) -> None:
    """A provider that returns different runtime identity cannot pass observation."""
    value = Payload.reference()
    value["runtime_id"] = "bad"
    reference = UiaReference.decode(value)
    with qtbot.waitSignal(adapter.failed) as failed:
        adapter.observe(reference)
    assert "changed" in failed.args[1]
    assert not adapter.busy


@pytest.mark.parametrize("percent", [-1.0, 101.0, float("nan"), float("inf")])
def test_invalid_restore_position_does_not_issue_native_work(
    adapter: UiaAdapter, percent: float
) -> None:
    """Invalid restoration cannot become an accidental large scroll."""
    with pytest.raises(ValueError, match="finite percentage"):
        adapter.restore(
            UiaReference.decode(Payload.reference()), percent, confirmed=True
        )
    assert not adapter.busy


def test_transport_failure_is_visible_and_retry_waits_for_cleanup(
    qtbot: QtBot, adapter: UiaAdapter
) -> None:
    """A failed provider cannot disappear behind a cleared progress state."""
    with qtbot.waitSignal(adapter.failed) as failed:
        adapter.inspect(99, Point(1, 1))
    assert "did not complete" in failed.args[1]
    qtbot.waitUntil(lambda: not adapter.busy)
    with qtbot.waitSignal(adapter.discovered):
        adapter.inspect(1, Point(92, 1))


def test_adapter_cancel_invalidates_queued_work_before_dispatch(
    adapter: UiaAdapter,
) -> None:
    """Stop before helper startup delivers neither data nor an unexpected error."""
    callbacks: list[object] = []
    adapter.observed.connect(lambda request, _result: callbacks.append(request))
    adapter.failed.connect(lambda request, _message: callbacks.append(request))
    reference = UiaReference.decode(Payload.reference())
    adapter.observe(reference)
    with pytest.raises(CaptureError, match="in flight"):
        adapter.observe(reference)
    adapter.cancel()
    assert not callbacks
    assert not adapter.busy
