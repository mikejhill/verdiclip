"""Validate the documented cache-mode extension missing in actionlint 1.7.12.

No general rule suppression: only exact, source-correlated diagnostics for the
two owned job-level `cache-mode: none` lines are accepted. All other diagnostics
and invalid extension values remain failures. Remove when upstream supports it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CACHE_KEY_COLUMN = 5


def validate(
    source: str, diagnostics: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Validate both owned extension keys, retaining unrelated lint failures."""
    allowed: set[int] = set()
    job = ""
    seen: set[str] = set()
    for number, line in enumerate(source.splitlines(), 1):
        match = re.fullmatch(r"  ([a-z][a-z0-9-]+):", line)
        if match:
            job = match[1]
        if re.match(r"\s*cache-mode\s*:", line):
            if (
                line != "    cache-mode: none"
                or job not in {"driver-staging", "runtime-model"}
                or job in seen
            ):
                raise ValueError("Unexpected cache-mode location, duplicate, or value")
            seen.add(job)
            allowed.add(number)
    if seen != {"driver-staging", "runtime-model"}:
        raise ValueError("Required cache-mode restriction missing")

    def is_supported_extension(diagnostic: dict[str, object]) -> bool:
        path, message = diagnostic.get("filepath"), diagnostic.get("message")
        return (
            isinstance(path, str)
            and isinstance(message, str)
            and path.replace("\\", "/") == ".github/workflows/ci.yml"
            and type(diagnostic.get("line")) is int
            and diagnostic["line"] in allowed
            and diagnostic.get("column") == CACHE_KEY_COLUMN
            and diagnostic.get("kind") == "syntax-check"
            and message.startswith(
                'unexpected key "cache-mode" for "job" section. expected one of '
            )
        )

    return [item for item in diagnostics if not is_supported_extension(item)]


def main() -> int:
    """Check a complete structured actionlint result and its process status."""
    source = Path(".github/workflows/ci.yml").read_text(encoding="utf-8-sig")
    diagnostics: list[dict[str, object]] = []
    for line in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            record = json.loads(line)
            if not isinstance(record, list) or any(
                not isinstance(item, dict) for item in record
            ):
                raise ValueError("Malformed actionlint diagnostic stream")
            diagnostics.extend(record)
    remaining = validate(source, diagnostics)
    status = int(sys.argv[2])
    if status not in (0, 1) or (status == 1 and not diagnostics):
        raise ValueError("Failed actionlint returned no usable diagnostic evidence")
    for diagnostic in remaining:
        sys.stdout.write(json.dumps(diagnostic) + "\n")
    sys.stdout.write(
        f"Cache extension checked; {len(remaining)} remaining actionlint diagnostics.\n"
    )
    return int(bool(remaining))


if __name__ == "__main__":
    sys.exit(main())
