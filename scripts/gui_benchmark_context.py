"""Pure shared admission/context for explicitly scheduled GUI benchmarks.

Local opt-in configures the harness; it does not grant permission to operate a
desktop. A local run still requires the parent's fresh interactive handoff.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import cast

MAX_HARDWARE_JSON = 8192
MAX_CPU_MODELS = 16
MAX_HARDWARE_TEXT = 200


def hardware_context(raw: str | None) -> dict[str, object] | None:
    """Accept only optional anonymous hardware fields provided by the runner."""
    if raw is None:
        return None
    if len(raw) > MAX_HARDWARE_JSON:
        raise ValueError("Hardware metadata is oversized")
    value: object = json.loads(raw)
    fields = {
        "os_version",
        "cpu_models",
        "logical_processors",
        "memory_bytes",
        "runner_image",
        "runner_image_version",
        "runner_arch",
    }
    if type(value) is not dict or set(value) != fields:
        raise ValueError("Unexpected hardware metadata fields")
    data = cast("dict[str, object]", value)
    for name in ("logical_processors", "memory_bytes"):
        item = data[name]
        if type(item) is not int or item <= 0:
            raise ValueError("Hardware counts must be positive integers")
    models = data["cpu_models"]
    if type(models) is not list or len(models) > MAX_CPU_MODELS:
        raise ValueError("CPU models must be a bounded list")
    strings = [data["os_version"], *models]
    strings.extend(
        data[name]
        for name in ("runner_image", "runner_image_version", "runner_arch")
        if data[name] is not None
    )
    if any(
        type(item) is not str
        or not item
        or len(item) > MAX_HARDWARE_TEXT
        or "\n" in item
        or "\r" in item
        for item in strings
    ):
        raise ValueError("Invalid hardware text metadata")
    return data


def benchmark_context(
    environment: Mapping[str, str], platform: str
) -> dict[str, object]:
    """Require a declared role, verified source and explicit runtime profile."""
    role = environment.get("VERDICLIP_BENCHMARK_ROLE")
    if (
        platform != "win32"
        or environment.get("VERDICLIP_GUI_READINESS_QUALIFICATION") != "1"
    ):
        raise ValueError("Explicit Windows GUI benchmark admission required")
    if role == "hosted":
        if (
            environment.get("GITHUB_ACTIONS") != "true"
            or environment.get("RUNNER_OS") != "Windows"
            or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
        ):
            raise ValueError("Hosted role requires a standard GitHub Windows runner")
    elif role == "local":
        if (
            environment.get("VERDICLIP_LOCAL_GUI_BENCHMARK") != "1"
            or environment.get("GITHUB_ACTIONS") == "true"
        ):
            raise ValueError(
                "Local role requires its own opt-in, not hosted impersonation"
            )
    else:
        raise ValueError("Benchmark role must be hosted or local")
    source = environment.get("VERDICLIP_BENCHMARK_SOURCE_SHA", "")
    qt = environment.get("VERDICLIP_EXPECTED_QT", "")
    qpa = environment.get("VERDICLIP_EXPECTED_QPA", "")
    if re.fullmatch(r"[0-9a-f]{40}", source) is None:
        raise ValueError("A verified immutable source SHA is required")
    if re.fullmatch(r"\d+\.\d+\.\d+", qt) is None or qpa not in {
        "windows",
        "offscreen",
    }:
        raise ValueError("Explicit Qt version and supported QPA required")
    return {
        "role": role,
        "source_sha": source,
        "source_identity_provider": "externally_verified_checkout_HEAD",
        "expected_qt": qt,
        "expected_qpa": qpa,
        "hardware": hardware_context(
            environment.get("VERDICLIP_BENCHMARK_HARDWARE_JSON")
        ),
    }
