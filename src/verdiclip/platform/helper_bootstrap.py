"""Fixed isolated runtime proof, unused by the app until provider integration.

The small stdlib-only prefix must run before installed package roots are added.
No variable source, module name, site hook, GUI or worker invocation is accepted.
"""

from __future__ import annotations

import sys
from pathlib import Path

from verdiclip.platform import helper_peer, helper_protocol
from verdiclip.platform.helper_runtime import deserialize_manifest, inspect_runtime

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
data = json.loads(payload)
if (not isinstance(data, dict) or data.get("schema_version") != 2
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
from verdiclip.platform.helper_bootstrap import verify_runtime
verify_runtime(payload)
"""


def verify_runtime(payload: str) -> None:
    """Fail before worker entry if isolated child identity differs from parent."""
    if helper_protocol.VERSION != 1:
        raise RuntimeError("Unsupported fixed peer protocol")
    expected = deserialize_manifest(payload)
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("Isolated no-site interpreter required")
    if Path(sys.executable).resolve(strict=True) != Path(expected.executable):
        raise RuntimeError("Child executable differs from selected base Python")
    if inspect_runtime() != expected:
        raise RuntimeError("Child runtime differs from installed parent")
    if any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules):
        raise RuntimeError("GUI imported into helper runtime")


def proof_arguments(payload: str) -> tuple[str, ...]:
    """Build isolated proof argv only; this does not launch a provider or process."""
    deserialize_manifest(payload)
    return ("-I", "-S", "-c", BOOTSTRAP_SOURCE, payload)


# Fixed dispatch only, after all installed module identities have been verified.
PEER_BOOTSTRAP_SOURCE = (
    BOOTSTRAP_SOURCE
    + "\nfrom verdiclip.platform.helper_peer import run\nrun(sys.argv[2])\n"
)


def peer_arguments(runtime_payload: str, launch_payload: str) -> tuple[str, ...]:
    """Build only the fixed inert peer invocation; launch fields remain argv data."""
    deserialize_manifest(runtime_payload)
    helper_peer.deserialize_launch(launch_payload)
    return ("-I", "-S", "-c", PEER_BOOTSTRAP_SOURCE, runtime_payload, launch_payload)
