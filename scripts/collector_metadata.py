"""Inspect fixed installed collector files only; never attach or execute them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

TARGETS = ("cdb", "clr", "sos", "mscordacwks")
STAGES = (
    "exists",
    "pe",
    "version",
    "signature",
    "publisher",
    "trust-close",
    "complete",
)
STATUSES = {"started", "ok", "missing", "refused", "unknown", "probeerror"}
SIGNATURES = {
    "cached-valid",
    "cache-unavailable",
    "unsigned",
    "bad-signature",
    "untrusted",
    "revoked",
    "error",
}
VERSION_PARTS = 4
MAX_VERSION_PART = 65535
LIMIT = 4096  # Polled rejection threshold, not an OS-enforced output cap.


def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous helper records rather than guessing their evidence."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def valid_value(stage: str, status: str, value: object) -> bool:
    """Permit only fixed categories and bounded numerical metadata."""
    if status == "started":
        return value is None
    if stage == "signature":
        return isinstance(value, str) and value in SIGNATURES
    if value is None:
        return status in {"probeerror", "unknown", "refused"}
    if stage in {"exists", "publisher", "trust-close", "complete"}:
        return type(value) is bool
    if stage == "pe":
        return isinstance(value, str) and value in {"x64", "x86", "arm64", "other"}
    return (
        stage == "version"
        and isinstance(value, list)
        and len(value) == VERSION_PARTS
        and all(type(part) is int and 0 <= part <= MAX_VERSION_PART for part in value)
    )


def decode(data: bytes) -> dict[str, object]:
    """Retain a valid bounded prefix even when a helper stops during a stage."""
    result: dict[str, object] = {
        "schema_valid": True,
        "last_stage": None,
        "records": [],
    }
    records: list[dict[str, object]] = []
    result["records"] = records
    if len(data) > LIMIT:
        return {**result, "schema_valid": False}
    previous = -1
    pending: str | None = None
    try:
        for line in data.decode("ascii").splitlines():
            item = json.loads(line, object_pairs_hook=unique)
            if not isinstance(item, dict) or set(item) != {"stage", "status", "value"}:
                return {**result, "schema_valid": False}
            stage, status, value = item["stage"], item["status"], item["value"]
            if (
                not isinstance(stage, str)
                or stage not in STAGES
                or not isinstance(status, str)
                or status not in STATUSES
                or not valid_value(stage, status, value)
            ):
                return {**result, "schema_valid": False}
            index = STAGES.index(stage)
            if status == "started":
                if pending is not None or index <= previous:
                    return {**result, "schema_valid": False}
                pending = stage
                previous = index
            elif pending != stage:
                return {**result, "schema_valid": False}
            else:
                pending = None
            records.append(item)
            result["last_stage"] = stage
    except (ValueError, UnicodeError):
        result["schema_valid"] = False
    return result


def retire(process: subprocess.Popen[bytes]) -> dict[str, object]:
    """Retain fixed cleanup outcomes; reap independently and propagate controls."""
    outcome: dict[str, object] = {
        "poll": "unconfirmed",
        "kill": "not-attempted",
        "reap": "unconfirmed",
        "retired": False,
    }
    try:
        try:
            running = process.poll() is None
            outcome["poll"] = "running" if running else "exited"
        except (OSError, subprocess.SubprocessError, RuntimeError):
            outcome["poll"] = "error"
        else:
            if running:
                try:
                    process.kill()
                    outcome["kill"] = "sent"
                except (OSError, subprocess.SubprocessError, RuntimeError):
                    outcome["kill"] = "error"
            else:
                outcome["kill"] = "not-needed"
    finally:
        try:
            process.wait(timeout=2)
            outcome["reap"] = "confirmed"
            outcome["retired"] = True
        except subprocess.TimeoutExpired:
            outcome["reap"] = "timeout"
        except (OSError, subprocess.SubprocessError, RuntimeError):
            outcome["reap"] = "error"
    return outcome


def inspect(helper: Path, target: str, directory: Path) -> dict[str, object]:
    """One bounded helper invocation per fixed file, with file-backed private IO."""
    if target not in TARGETS:
        raise ValueError("unknown target")
    output, errors = directory / (target + ".out"), directory / (target + ".err")
    receipt: dict[str, object] = {
        "execution": "spawn-error",
        "exit_code": None,
        "retired": True,
        "retirement": {
            "poll": "not-started",
            "kill": "not-attempted",
            "reap": "not-started",
            "retired": True,
        },
    }
    process = None
    with output.open("xb") as stdout, errors.open("xb") as stderr:
        try:
            process = subprocess.Popen(  # noqa: S603 - compiled repository helper and fixed target enum
                [str(helper), target], stdout=stdout, stderr=stderr
            )
            receipt["retired"] = False
            deadline = time.monotonic() + 10
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    receipt["execution"] = "timeout"
                    break
                if output.stat().st_size > LIMIT or errors.stat().st_size > LIMIT:
                    receipt["execution"] = "output-limit"
                    break
                time.sleep(0.02)
            else:
                receipt["execution"] = "completed"
                code = process.returncode
                receipt["exit_code"] = (
                    code if type(code) is int and -(2**31) <= code < 2**32 else None
                )
        except (OSError, subprocess.SubprocessError, RuntimeError):
            receipt["execution"] = "probe-error"
        finally:
            if process is not None:
                retirement = retire(process)
                receipt["retirement"] = retirement
                receipt["retired"] = retirement["retired"]
    try:
        with output.open("rb") as stream:
            evidence = decode(stream.read(LIMIT + 1))
    except OSError:
        evidence = {"schema_valid": False, "last_stage": None, "records": []}
    if errors.stat().st_size > LIMIT:
        receipt["execution"] = "output-limit"
    return {**receipt, **evidence}


def matching_runtime(tools: dict[str, dict[str, object]]) -> str:
    """Equal file versions are candidates only, never proof DAC load will work."""
    versions = []
    for target in ("clr", "sos", "mscordacwks"):
        item = tools[target]
        records = item.get("records", [])
        if (
            not isinstance(records, list)
            or item.get("execution") != "completed"
            or item.get("exit_code") != 0
            or item.get("schema_valid") is not True
            or item.get("retired") is not True
        ):
            return "unverified"
        outcomes = {
            r["stage"]: r
            for r in records
            if isinstance(r, dict) and r.get("status") != "started"
        }
        if (
            outcomes.get("exists") != {"stage": "exists", "status": "ok", "value": True}
            or outcomes.get("trust-close")
            != {"stage": "trust-close", "status": "ok", "value": True}
            or outcomes.get("complete")
            != {"stage": "complete", "status": "ok", "value": True}
            or outcomes.get("signature")
            != {"stage": "signature", "status": "ok", "value": "cached-valid"}
            or outcomes.get("publisher")
            != {"stage": "publisher", "status": "ok", "value": True}
            or outcomes.get("pe") != {"stage": "pe", "status": "ok", "value": "x64"}
        ):
            return "unverified"
        version = outcomes.get("version", {}).get("value")
        if not isinstance(version, list):
            return "unverified"
        versions.append(version)
    return (
        "equal-file-version-candidate"
        if versions[0] == versions[1] == versions[2]
        else "file-version-difference"
    )


def collect(helper: Path) -> dict[str, object]:
    """Stop after uncertain retirement; sanitize all ordinary failure paths."""
    tools: dict[str, dict[str, object]] = {}
    receipt: dict[str, object] = {
        "schema": 1,
        "scope": "installed-file-metadata-only",
        "tools": tools,
        "clrmd": "not-located-no-fixed-installed-path",
        "runtime_match": "unverified",
        "private_files_removed": False,
        "cleanup_confirmed": False,
        "failure_stage": None,
        "stack_capability": "unverified",
        "attachment_authorized": False,
    }
    temporary = None
    stage = "private-directory"
    try:
        temporary = tempfile.TemporaryDirectory(prefix="collector-metadata-")
        for target in TARGETS:
            stage = "inspect"
            item = inspect(helper, target, Path(temporary.name))
            tools[target] = item
            if item["retired"] is not True:
                receipt["failure_stage"] = "retirement"
                break
        else:
            receipt["cleanup_confirmed"] = True
            stage = "reduce"
            receipt["runtime_match"] = matching_runtime(tools)
    except Exception:  # noqa: BLE001 - fixed diagnostic receipt, never raw private path errors
        receipt["failure_stage"] = stage
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
                receipt["private_files_removed"] = True
            except Exception:  # noqa: BLE001 - deletion errors must not leak temporary paths
                receipt["failure_stage"] = "private-cleanup"
                receipt["cleanup_confirmed"] = False
    return receipt


def main() -> int:
    """Emit a single sanitized aggregate; never leak exception paths to CI."""
    try:
        if os.name != "nt" or os.environ.get("VERDICLIP_COLLECTOR_METADATA") != "1":
            receipt = {"schema": 1, "failure_stage": "gate-refused"}
        else:
            helper = Path(__file__).resolve().parent / "collector_metadata.exe"
            receipt = collect(helper)
        sys.stdout.write(json.dumps(receipt, separators=(",", ":")) + "\n")
        return 0 if receipt.get("cleanup_confirmed") is True else 1
    except Exception:  # noqa: BLE001 - last-resort output/path failure must stay private
        with suppress(Exception):
            sys.stdout.write('{"schema":1,"failure_stage":"reporting"}\n')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
