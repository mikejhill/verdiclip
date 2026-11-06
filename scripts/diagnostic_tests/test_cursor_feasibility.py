"""Pure coordinator controls; never touches native cursor APIs or a desktop."""

# ruff: noqa: S101, PLR2004
from __future__ import annotations

from types import SimpleNamespace
from typing import cast, override

import pytest
from scripts import cursor_actor
from scripts.cursor_feasibility import Port, observe, retire_all, run_cases, scenario


class World:
    """Model an explicitly uncounted global visibility flag and owned clients."""

    def __init__(self) -> None:
        self.visible = True
        self.supported = True
        self.observations = 0
        self.break_negative = False
        self.events: list[str] = []
        self.created: list[str] = []

    def create(self, role: str) -> Port:
        """Create an owned fake actor, with no implicit restoration on kill."""
        self.created.append(role)
        self.events.append("create:" + role)
        return Owned(self, role)

    def request(self, command: str) -> dict[str, object]:
        """Read the fake independent plane oracle, separately from API receipts."""
        if command == "arm":
            self.events.append("arm")
            return {"ok": True}
        assert command == "observe"
        self.observations += 1
        visible = self.visible and not (self.break_negative and self.observations == 2)
        self.events.append("observe:" + str(visible))
        return {
            "supported": self.supported,
            "pointer_update_after_boundary": True,
            "status": "visible" if visible else "hidden",
        }

    def terminate(self) -> None:
        """The pure oracle has no process to terminate."""

    def retire(self) -> None:
        """The pure oracle has no resource to retire."""


class Owned:
    """Minimal lease/client actor implementing the unsafe uncounted API model."""

    def __init__(self, world: World, role: str) -> None:
        self.world, self.role = world, role

    def request(self, command: str) -> dict[str, object]:
        """Only explicit show/close changes visibility; no-op remains a no-op."""
        self.world.events.append(self.role + ":" + command)
        if command in {"acquire", "hide"}:
            self.world.visible = False
        elif command == "show" or (self.role == "lease" and command == "close"):
            self.world.visible = True
        return {"ok": True, "attempted": command == "acquire"}

    def terminate(self) -> None:
        """Crash has no finally block, so the hidden state remains observable."""
        self.world.events.append("terminate:lease")

    def retire(self) -> None:
        """Retirement never acts as supervisor rescue."""
        self.world.events.append("retire:" + self.role)


def test_uncounted_interference_and_crash_are_not_hidden_by_rescue() -> None:
    """Product observations precede all rescue, and remain failures afterward."""
    world = World()
    result = run_cases(world, world.create)
    assert result["release_qualified"] is False
    assert result["negative_noop"] == "visible"
    cases = cast("list[dict[str, object]]", result["cases"])
    assert cases[0]["product_pass"] is True
    assert cases[1]["other_client_hide_preserved"] is False
    assert cases[2]["other_client_hide_preserved"] is False
    assert cases[3]["automatic_restoration_observed"] is False
    assert cases[3]["after_termination_before_rescue"] == "hidden"
    crash = world.events.index("terminate:lease")
    assert world.events[crash + 1] == "observe:False"
    assert world.events[crash + 2 : crash + 6] == [
        "retire:lease",
        "create:client",
        "arm",
        "client:show",
    ]
    assert all(case["supervisor_rescue_visible"] == "visible" for case in cases)


def test_unsupported_oracle_performs_no_cursor_mutation() -> None:
    """Unavailable independent evidence must not start even a production lease."""
    world = World()
    world.supported = False
    result = run_cases(world, world.create)
    assert result["status"] == "unsupported"
    assert not world.created
    assert not result["presentation_feasible"]


def test_negative_control_failure_stops_before_real_hide() -> None:
    """A no-op allegedly hidden by the oracle invalidates the entire experiment."""
    world = World()
    world.break_negative = True
    result = run_cases(world, world.create)
    assert result["status"] == "unsupported-negative-control"
    assert world.created == ["lease"]
    assert not any(event.endswith((":acquire", ":hide")) for event in world.events)
    assert not result["presentation_feasible"]


def test_unsupported_midcase_stops_after_owned_baseline_rescue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oracle loss cannot become diagnostic completion or start further cases."""
    world = World()
    original = world.request

    def request(command: str) -> dict[str, object]:
        receipt = original(command)
        if world.observations == 3:
            receipt["supported"] = False
        return receipt

    monkeypatch.setattr(world, "request", request)
    result = run_cases(world, world.create)
    assert result["status"] == "unsupported-phase"
    cases = cast("list[dict[str, object]]", result["cases"])
    assert len(cases) == 1
    assert cases[0]["supervisor_rescue_visible"] == "visible"
    assert cases[0]["product_pass"] is False


@pytest.mark.parametrize(
    "missing", ["GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "VERDICLIP_CURSOR_FEASIBILITY"]
)
def test_guard_refuses_before_native_calls(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """A generic CI host or unrequested local run is never sufficient."""
    monkeypatch.setattr(cursor_actor, "sys", SimpleNamespace(platform="win32"))
    for key, value in (
        ("GITHUB_ACTIONS", "true"),
        ("RUNNER_ENVIRONMENT", "github-hosted"),
        ("VERDICLIP_CURSOR_FEASIBILITY", "1"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match="Disposable"):
        cursor_actor.require_host()


def test_retirement_control_still_attempts_every_owned_actor() -> None:
    """A control signal cannot leave later owned actors unattempted."""
    world = World()
    original = KeyboardInterrupt()

    class Interrupted(Owned):
        @override
        def retire(self) -> None:
            """Raise a control signal after recording the owned retirement attempt."""
            super().retire()
            raise original

    with pytest.raises(KeyboardInterrupt) as caught:
        retire_all([Interrupted(world, "first"), Owned(world, "second")])
    assert caught.value is original
    assert world.events == ["retire:first", "retire:second"]


@pytest.mark.parametrize("status", ["visible", "hidden"])
@pytest.mark.parametrize("fresh", [False, True])
def test_transition_requires_post_boundary_pointer_metadata(
    status: str, *, fresh: bool
) -> None:
    """Cached or delayed pre-operation metadata cannot establish either outcome."""

    class Receipt(World):
        @override
        def request(self, command: str) -> dict[str, object]:
            return {
                "supported": True,
                "status": status,
                "pointer_update_after_boundary": fresh,
            }

    assert observe(Receipt(), transition=True) == (status if fresh else "unsupported")
    assert observe(Receipt()) == status


def test_rescue_show_survives_unconsumed_boundary_after_effective_hide() -> None:
    """A failed API receipt after hiding cannot prevent rescue via a stale arm."""

    class InterruptedWorld(World):
        armed = False

        @override
        def request(self, command: str) -> dict[str, object]:
            if command == "arm":
                if self.armed:
                    return {"ok": False}
                self.armed = True
            else:
                self.armed = False
            return super().request(command)

        @override
        def create(self, role: str) -> Port:
            world = self

            class InterruptedLease(Owned):
                @override
                def request(self, command: str) -> dict[str, object]:
                    receipt = super().request(command)
                    if command == "acquire":
                        raise RuntimeError("Effective hide but missing receipt")
                    return receipt

            if role == "lease":
                return InterruptedLease(world, role)
            return super().create(role)

    world = InterruptedWorld()
    result: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="Effective hide"):
        run_cases(world, world.create, result)
    assert world.visible is True
    assert "client:show" in world.events
    cases = cast("list[dict[str, object]]", result["cases"])
    assert cases[0]["supervisor_rescue_visible"] == "unsupported"


def test_unsupported_during_stops_before_concurrent_mutation() -> None:
    """Only cleanup may continue after missing suppression evidence."""
    world = World()
    world.supported = False
    receipt = scenario("concurrent-hide", world, world.create, [])
    assert receipt["during"] == "unsupported"
    assert "client:hide" not in world.events
    assert "lease:close" not in world.events


@pytest.mark.parametrize("case", ["forced-termination", "concurrent-hide"])
def test_unknown_preservation_or_restoration_is_null(case: str) -> None:
    """Unobserved results are not misreported as measured failures."""

    class MissingFinal(World):
        @override
        def request(self, command: str) -> dict[str, object]:
            receipt = super().request(command)
            if command == "observe" and self.observations == (
                2 if case == "forced-termination" else 3
            ):
                receipt["supported"] = False
            return receipt

    world = MissingFinal()
    receipt = scenario(case, world, world.create, [])
    key = (
        "automatic_restoration_observed"
        if case == "forced-termination"
        else "other_client_hide_preserved"
    )
    assert receipt[key] is None
    assert receipt["product_pass"] is False
