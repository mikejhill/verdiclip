"""Fixed isolated runtime proof, unused by the app until provider integration.

The small stdlib-only prefix must run before installed package roots are added.
No variable source, module name, site hook, GUI or worker invocation is accepted.
"""

from __future__ import annotations

import sys
from pathlib import Path

from verdiclip.platform import moving_edit_peer, moving_edit_protocol
from verdiclip.platform.moving_edit_runtime import (
    deserialize_manifest,
    inspect_child_runtime,
)

# Data remains argv, never interpolated into executable source. The full schema
# is checked immediately after loading this fixed installed module. Early bounds
# limit parsing and root manipulation before any package can be imported.
BOOTSTRAP_SOURCE = r"""
import json
from pathlib import Path
import struct
import sys
if not sys.flags.isolated or not sys.flags.no_site:
    raise RuntimeError("Isolated no-site interpreter required")
payload = sys.argv[1]
if len(payload) > 16384 or len(payload.encode("utf-8")) > 16384:
    raise ValueError("Runtime manifest exceeds byte bound")
def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate runtime manifest field")
        result[key] = value
    return result
data = json.loads(payload, object_pairs_hook=unique_object)
if (not isinstance(data, dict) or set(data) != {
    "schema_version", "executable", "roots", "origins", "versions",
    "python_version", "pointer_bits"}):
    raise ValueError("Invalid runtime manifest fields")
if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
    or data.get("schema_version") != 6
    or data.get("python_version") != list(sys.version_info[:3])
    or data.get("pointer_bits") != struct.calcsize("P") * 8
    or type(data.get("executable")) is not str
    or str(Path(sys.executable).resolve()) != data["executable"]):
    raise RuntimeError("Unexpected isolated interpreter identity")
roots = data.get("roots") if isinstance(data, dict) else None
if (not isinstance(roots, list) or not 1 <= len(roots) <= 2
    or any(type(root) is not str or not root or len(root) > 2048
           or "\0" in root or not Path(root).is_absolute()
           or not Path(root).is_dir() for root in roots)):
    raise ValueError("Invalid explicit runtime roots")
sys.path.extend(roots)
import mss
from verdiclip.platform.moving_edit_runtime import deserialize_manifest, _MODULES
from importlib import import_module
deserialize_manifest(payload)
for name in _MODULES:
    if name != "verdiclip.platform.moving_edit_channel":
        import_module(name)
from verdiclip.platform.moving_edit_bootstrap import verify_runtime
verify_runtime(payload)
"""


def verify_runtime(payload: str) -> None:
    """Fail before worker entry if isolated child identity differs from parent."""
    if moving_edit_protocol.VERSION != 4:  # noqa: PLR2004 - fixed protocol attestation
        raise RuntimeError("Unsupported fixed peer protocol")
    expected = deserialize_manifest(payload)
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("Isolated no-site interpreter required")
    if Path(sys.executable).resolve(strict=True) != Path(expected.executable):
        raise RuntimeError("Child executable differs from selected base Python")
    if inspect_child_runtime() != expected:
        raise RuntimeError("Child runtime differs from installed parent")
    if any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules):
        raise RuntimeError("GUI imported into helper runtime")


def proof_arguments(payload: str) -> tuple[str, ...]:
    """Build isolated proof argv only; this does not launch a provider or process."""
    deserialize_manifest(payload)
    return ("-I", "-S", "-c", BOOTSTRAP_SOURCE, payload)


# Fixed dispatch only, after all installed module identities have been verified.
MOVING_BOOTSTRAP_SOURCE = (
    BOOTSTRAP_SOURCE
    + "\nfrom verdiclip.platform.moving_edit_peer import run\nrun(sys.argv[2])\n"
)


def moving_arguments(runtime_payload: str, launch_payload: str) -> tuple[str, ...]:
    """Build only the fixed peer invocation; launch fields remain argv data."""
    deserialize_manifest(runtime_payload)
    moving_edit_peer.deserialize_launch(launch_payload)
    return (
        "-I",
        "-S",
        "-c",
        MOVING_BOOTSTRAP_SOURCE,
        runtime_payload,
        launch_payload,
    )


EVIDENCE_READER_BOOTSTRAP_SOURCE = (
    BOOTSTRAP_SOURCE
    + "\nfrom verdiclip.platform.moving_edit_peer import run_evidence_reader\n"
    + "run_evidence_reader(sys.argv[2])\n"
)


def evidence_reader_arguments(
    runtime_payload: str, launch_payload: str
) -> tuple[str, ...]:
    """Launch only the fixed verified research reader, never arbitrary dispatch."""
    deserialize_manifest(runtime_payload)
    moving_edit_peer.deserialize_reader_launch(launch_payload)
    return (
        "-I",
        "-S",
        "-c",
        EVIDENCE_READER_BOOTSTRAP_SOURCE,
        runtime_payload,
        launch_payload,
    )
