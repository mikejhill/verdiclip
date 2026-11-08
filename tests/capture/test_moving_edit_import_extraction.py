"""Fresh-interpreter proof of the packaged native extraction import closure."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "verdiclip.capture.edit_geometry",
    "verdiclip.capture.edit_native",
    "verdiclip.capture.edit_scroll_contract",
    "verdiclip.capture.edit_scroll_native",
    "verdiclip.capture.edit_samples",
)
EXPECTED_PROJECT_MODULES = {
    *MODULES,
    "verdiclip",
    "verdiclip.capture",
    "verdiclip.platform",
    "verdiclip.capture.observe_native",
    "verdiclip.capture.observe_protocol",
    "verdiclip.capture.frame_envelope",
    "verdiclip.capture.scroll_stitch",
    "verdiclip.platform.helper_protocol",
}


def test_fresh_child_import_closure_has_no_scripts_gui_or_unlisted_dependency() -> None:
    """Actual loaded project origins match the isolated fixed package list."""
    program = f"""
import importlib, json, sys
sys.path.insert(0, {str(ROOT / "src")!r})
for name in {MODULES!r}:
    importlib.import_module(name)
print(json.dumps({{
    "project": {{name: value.__file__ for name, value in sys.modules.items()
        if name == "verdiclip" or name.startswith("verdiclip.")}},
    "forbidden": [name for name in sys.modules
        if name.split(".")[0] in ("scripts", "PySide6", "shiboken6")],
}}))
"""
    result = subprocess.run(  # noqa: S603 - fixed local interpreter, fixed source-only import probe
        [sys.executable, "-I", "-c", program],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    loaded = json.loads(result.stdout)
    assert set(loaded["project"]) == EXPECTED_PROJECT_MODULES
    assert loaded["forbidden"] == []
    for name, origin in loaded["project"].items():
        path = ROOT / "src" / name.replace(".", "/")
        expected = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        assert Path(origin).resolve() == expected.resolve()
