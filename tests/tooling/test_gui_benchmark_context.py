"""Pure benchmark admission/metadata ports; no Qt, inventory or desktop calls."""

from __future__ import annotations

import json

import pytest
from scripts.gui_benchmark_context import benchmark_context, hardware_context


def environment(role: str = "hosted") -> dict[str, str]:
    """Supply explicit synthetic facts, never inspect this machine's state."""
    shared = {
        "VERDICLIP_GUI_READINESS_QUALIFICATION": "1",
        "VERDICLIP_BENCHMARK_ROLE": role,
        "VERDICLIP_BENCHMARK_SOURCE_SHA": "a" * 40,
        "VERDICLIP_EXPECTED_QT": "6.11.2",
        "VERDICLIP_EXPECTED_QPA": "windows",
    }
    if role == "hosted":
        shared.update(
            GITHUB_ACTIONS="true",
            RUNNER_OS="Windows",
            RUNNER_ENVIRONMENT="github-hosted",
        )
    else:
        shared["VERDICLIP_LOCAL_GUI_BENCHMARK"] = "1"
    return shared


@pytest.mark.parametrize("role", ["hosted", "local"])
def test_same_explicit_identity_for_both_roles(role: str) -> None:
    """Local admission needs no fabricated GitHub runner fields."""
    context = benchmark_context(environment(role), "win32")
    assert context["role"] == role
    assert context["source_sha"] == "a" * 40
    assert context["expected_qt"] == "6.11.2"
    assert context["expected_qpa"] == "windows"
    assert context["hardware"] is None


@pytest.mark.parametrize(
    "field",
    [
        "VERDICLIP_GUI_READINESS_QUALIFICATION",
        "VERDICLIP_BENCHMARK_SOURCE_SHA",
        "VERDICLIP_EXPECTED_QT",
        "VERDICLIP_EXPECTED_QPA",
        "RUNNER_ENVIRONMENT",
    ],
)
def test_missing_profile_fails_closed(field: str) -> None:
    """A source/runtime or role prerequisite cannot be inferred from defaults."""
    facts = environment()
    facts.pop(field)
    with pytest.raises(
        ValueError, match=r"admission|immutable source|Explicit Qt|Hosted role"
    ):
        benchmark_context(facts, "win32")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("VERDICLIP_BENCHMARK_SOURCE_SHA", "HEAD"),
        ("VERDICLIP_EXPECTED_QPA", "xcb"),
        ("VERDICLIP_EXPECTED_QT", "latest"),
        ("VERDICLIP_BENCHMARK_ROLE", "unknown"),
    ],
)
def test_invalid_profile_is_not_a_qualification(field: str, value: str) -> None:
    """Only immutable source and an explicit supported runtime can be reported."""
    facts = environment()
    facts[field] = value
    with pytest.raises(
        ValueError, match=r"immutable source|Explicit Qt|Benchmark role"
    ):
        benchmark_context(facts, "win32")


def test_local_requires_its_own_opt_in_and_rejects_hosted_impersonation() -> None:
    """Configuration does not replace the separate human interaction handoff."""
    facts = environment("local")
    facts.pop("VERDICLIP_LOCAL_GUI_BENCHMARK")
    with pytest.raises(ValueError, match="Local role"):
        benchmark_context(facts, "win32")
    facts = environment("local")
    facts["GITHUB_ACTIONS"] = "true"
    with pytest.raises(ValueError, match="Local role"):
        benchmark_context(facts, "win32")
    with pytest.raises(ValueError, match="Windows"):
        benchmark_context(environment(), "linux")


def hardware() -> dict[str, object]:
    """Return anonymous mock hardware metadata, not live inventory."""
    return {
        "os_version": "10.0.test",
        "cpu_models": ["Synthetic CPU"],
        "logical_processors": 4,
        "memory_bytes": 8192,
        "runner_image": None,
        "runner_image_version": None,
        "runner_arch": "X64",
    }


def test_optional_hardware_is_exact_and_anonymous() -> None:
    """Missing metadata stays null; supplied fields round-trip without identity."""
    assert hardware_context(None) is None
    value = hardware()
    facts = environment()
    facts["VERDICLIP_BENCHMARK_HARDWARE_JSON"] = json.dumps(value)
    assert benchmark_context(facts, "win32")["hardware"] == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostname", "private"),
        ("logical_processors", True),
        ("memory_bytes", -1),
        ("cpu_models", "not a list"),
        ("os_version", "bad\ntext"),
    ],
)
def test_hardware_rejects_unapproved_or_malformed_fields(
    field: str, value: object
) -> None:
    """No host/user/serial field is admitted to the optional metadata packet."""
    data = hardware()
    data[field] = value
    with pytest.raises(
        ValueError,
        match=r"Unexpected hardware|Hardware counts|CPU models|Invalid hardware",
    ):
        hardware_context(json.dumps(data))
