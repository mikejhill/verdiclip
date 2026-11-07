"""Fixed owned table control page/server; no changes to the old matrix oracle."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import override

from scripts.panel_fixture_matrix import MatrixCase, render
from scripts.panel_fixture_server import FixtureServer

HOOK = """
let paintPhase = 0, paintFailure = null, paintTransition = false;
function paintEvidence() {
  const props = ['display','position','width','height','overflow-x','overflow-y',
    'transform','zoom','visibility','background-color','background-image','box-shadow',
    'border-top-width','border-right-width','border-bottom-width','border-left-width',
    'border-top-color','border-right-color','border-bottom-color','border-left-color'];
  const nodes = [target, ...target.querySelectorAll('*')];
  return {phase:paintPhase, failure:paintFailure,
    opacity:getComputedStyle(target).opacity,
    descendants:nodes.length,
    descendants_same_opacity:nodes.length<=512 && nodes.every(e =>
      getComputedStyle(e).opacity === (e===target && paintPhase===1?'0':'1')),
    styles:[target,outer,sibling].map(e =>
      props.map(p => getComputedStyle(e).getPropertyValue(p)))};
}
async function pollPaint() {
  try {
    const response = await fetch('paint-phase', {cache:'no-store'});
    const next = await response.json();
    if (!Number.isInteger(next) || next<paintPhase || next>2 || next>paintPhase+1)
      throw new Error('Invalid fixed paint transition');
    if (next!==paintPhase) {
      paintTransition=true;
      target.style.opacity = next===1 ? '0' : '1';
      await new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)));
      paintPhase=next; paintTransition=false;
    }
  } catch(error) { paintFailure=String(error).slice(0,128); }
  if (paintFailure===null && paintPhase<2) setTimeout(pollPaint,50);
}
setTimeout(pollPaint,50);
"""


def page() -> str:
    """Fixed nonzero table125 pose; opacity is the only phase mutation."""
    source = render(MatrixCase("table", "dark", width=520, reference_offset=119))
    if (
        source.count("function report() {") != 1
        or source.count("const receipt = {sequence:") != 1
    ):
        raise ValueError("Owned fixture hook changed")
    return source.replace(
        "function report() {",
        HOOK + "\nfunction report() { if (paintTransition) return;",
    ).replace(
        "const receipt = {sequence:",
        "const receipt = {paint:paintEvidence(), sequence:",
    )


class PaintServer(FixtureServer):
    """Single fixed phase endpoint in the already-private owned fixture server."""

    def __init__(self, source: Path, output: Path, command: Path) -> None:
        self.command = command
        super().__init__(source, output)

    @override
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        base = super()._handler()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 1.0

            def do_POST(self) -> None:
                base.do_POST(self)  # ty: ignore[unresolved-attribute]

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.path != f"/{owner.token}/paint-phase":
                    base.do_GET(self)  # ty: ignore[unresolved-attribute]
                    return
                try:
                    with owner.command.open("rb") as stream:
                        raw = stream.read(2)
                    if raw not in (b"0", b"1", b"2"):
                        raise ValueError("Invalid phase")  # noqa: TRY301 - HTTP boundary
                except (OSError, ValueError):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        return Handler


def request_phase(path: Path, phase: int) -> None:
    """Atomic tiny fixed progression; caller owns the private session directory."""
    if type(phase) is not int or phase not in (0, 1, 2):
        raise ValueError("Invalid paint phase")
    if (phase and path.read_bytes() != str(phase - 1).encode()) or (
        phase == 0 and path.exists() and path.read_bytes() != b"0"
    ):
        raise ValueError("Paint phase skipped or repeated")
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(phase), encoding="ascii")
    pending.replace(path)
