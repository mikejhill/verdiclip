"""Pure bootstrap shared by both isolated IPC regression processes."""

CHILD_IMPORT_PREFIX = r"""import sys
from pathlib import Path

expected_source = Path(sys.argv[1]).resolve(strict=True)
if expected_source.parts[-3:] != ("verdiclip", "shell", "instance.py"):
    raise RuntimeError("Unexpected IPC candidate source layout")
sys.path.insert(0, str(expected_source.parents[2]))
"""
