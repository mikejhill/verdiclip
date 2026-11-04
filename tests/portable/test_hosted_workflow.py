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


def test_parser_exception_matches_only_documented_unknown_job_key() -> None:
    """Different keys, sections and other diagnostics remain failures."""
    config = (ROOT / ".github/actionlint.yaml").read_text(encoding="utf-8")
    assert "  .github/workflows/ci.yml:\n" in config
    assert config.count("      - '") == 1
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
