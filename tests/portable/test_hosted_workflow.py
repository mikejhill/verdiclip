"""Keep the manual native lane isolated despite an older workflow parser."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cache_mode_is_restricted_to_owned_manual_job() -> None:
    """No other cache-mode field can silently acquire the parser exception."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    native = workflow.split("  hosted-scrollbar:\n", 1)[1].split(
        "\n  portable-baseline:", 1
    )[0]
    assert workflow.count("cache-mode:") == 1
    assert "    cache-mode: none\n" in native
    assert (
        "github.event_name == 'workflow_dispatch' && inputs.hosted_scrollbar" in native
    )
    assert "    runs-on: windows-2025\n" in native
    assert "    timeout-minutes: 5\n" in native
    assert "    permissions:\n      contents: read\n" in native
    assert "persist-credentials: false" in native
    assert "enable-cache: false" in native
    assert "actions/cache" not in native
    assert "upload-artifact" not in native
    for profile in (
        "{provider: wpf, qt: '6.11.2', scale: '1'}",
        "{provider: wpf, qt: '6.12.0', scale: '1'}",
        "{provider: wpf, qt: '6.12.0', scale: '1.5'}",
        "{provider: wpf, qt: '6.12.0', scale: '2'}",
        "{provider: winforms, qt: '6.12.0', scale: '1'}",
    ):
        assert profile in native
    assert "QT_SCALE_FACTOR: ${{ matrix.scale }}" in native
    assert "VERDICLIP_NATIVE_PROVIDER: ${{ matrix.provider }}" in native
    assert "fail-fast: false" in native


def test_parser_exception_matches_only_documented_unknown_job_key() -> None:
    """Different keys, sections and other diagnostics remain failures."""
    config = (ROOT / ".github/actionlint.yaml").read_text(encoding="utf-8")
    assert "  .github/workflows/ci.yml:\n" in config
    assert "  .github/workflows/gui-readiness-profile.yml:\n" in config
    assert config.count("      - '") == 2
    patterns = [part.split("'\n", 1)[0] for part in config.split("      - '")[1:]]
    assert len(set(patterns)) == 1
    pattern = config.split("      - '", 1)[1].split("'\n", 1)[0]
    keys = (
        "concurrency",
        "container",
        "continue-on-error",
        "defaults",
        "env",
        "environment",
        "if",
        "name",
        "needs",
        "outputs",
        "permissions",
        "runs-on",
        "secrets",
        "services",
        "snapshot",
        "steps",
        "strategy",
        "timeout-minutes",
        "uses",
        "with",
    )
    diagnostic = (
        'unexpected key "cache-mode" for "job" section. expected one of '
        + ", ".join(f'"{key}"' for key in keys)
    )
    assert re.fullmatch(pattern, diagnostic)
    for changed in (
        diagnostic.replace("cache-mode", "cache-mode-typo"),
        diagnostic.replace('"job"', '"workflow"'),
        diagnostic + " extra diagnostic",
        'invalid value "writee" for cache-mode',
    ):
        assert not re.search(pattern, changed)


def test_profile_cache_restriction_has_no_upload_or_cache_actions() -> None:
    """The extra parser exception covers only the bounded diagnostic job."""
    workflow = (ROOT / ".github/workflows/gui-readiness-profile.yml").read_text()
    assert workflow.count("cache-mode:") == 1
    assert "  diagnose:\n    cache-mode: none\n" in workflow
    assert "  contents: read\n" in workflow
    assert "    timeout-minutes: 10\n" in workflow
    assert "        timeout-minutes: 2\n" in workflow
    assert "persist-credentials: false" in workflow
    assert "enable-cache: false" in workflow
    assert "actions/cache" not in workflow
    assert "upload-artifact" not in workflow
