"""The ``verdiclip ctl`` command line: its arguments, parameters and help.

``verdiclip ctl --help`` and ``verdiclip ctl <command> --help`` are the main
usage reference for people and AI agents; the skill points here.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Final, NoReturn, cast

from verdiclip.automation.capturing import CaptureKind, CaptureSpec
from verdiclip.automation.client import DEFAULT_TIMEOUT_S
from verdiclip.automation.protocol import DEFAULT_MAX_SIDE, AutomationError
from verdiclip.automation.skills import AGENTS, SkillScope, parse_agents
from verdiclip.document.annotations import JsonObject, JsonValue
from verdiclip.geometry import Rect

type Fail = Callable[[str], NoReturn]
type Subparsers = argparse._SubParsersAction[argparse.ArgumentParser]

# A person may take a while to choose; scrolling captures can run for minutes
CHOOSING_TIMEOUT_S: Final = 300.0

CTL_DESCRIPTION: Final = """\
Drive the running VerdiClip: capture, open, read, edit, save and close images,
unsaved edits included. Every command prints JSON on stdout.

workflow:
  verdiclip ctl capture window --title Edge  # or: open FILE; prints the new "id"
  verdiclip ctl render --grid 100            # PNG path to look at
  verdiclip ctl get                          # annotations, crop, "state"
  verdiclip ctl apply --ops ops.json --expect-state STATE --label "what"
  verdiclip ctl render                       # look again before saying done
  verdiclip ctl save --path C:/out/shot.png  # only when asked
  verdiclip ctl close                        # only when asked

Commands act on the active tab unless given --doc ID (ids from `list`,
`open` and `capture`). Image coordinates are image pixels; capture and
targets use physical desktop pixels. Each edit is one undo step named
"CLI: ...". `verdiclip ctl COMMAND --help` explains a command.
"""

CTL_EPILOG: Final = """\
exit codes:
  0 ok, 1 failed, 2 bad usage, 3 VerdiClip not running or automation off
  (Settings > General), 4 conflict (image changed, unsaved, or step not
  yours), 5 not found, 6 busy (user typing or dragging, or another capture
  running: wait and retry), 7 cancelled by the user
"""

CAPTURE_DESCRIPTION: Final = """\
Capture the screen into a new editor tab and print its summary (with "id").
Coordinates are physical desktop pixels; `verdiclip ctl targets` lists
monitors and windows with their bounds.

  universal   the user chooses in the Universal capture overlay (PrtSc):
              drag a region, click a window, or Ctrl+click a scrolling panel
  region      --region X,Y,W,H
  window      the active window, or --title TEXT, --handle N or --at X,Y;
              a named window is brought to the front first
  fullscreen  the monitor under the mouse, --monitor N, or --all monitors
  scrolling   a tall image of a scrolling panel: --at X,Y on the panel, or
              --title/--handle (the window's center); --from here starts at
              the current position instead of the top. With no target the
              user clicks the panel. The user's Esc stops early and keeps it.

examples:
  verdiclip ctl capture fullscreen --monitor 2 -o C:/shots/screen.png
  verdiclip ctl capture window --title "Visual Studio Code" --copy
  verdiclip ctl capture scrolling --at 900,600 --from here
  verdiclip ctl capture region --region 0,0,800,600 --delay 2

Without -o the capture isn't saved (`ctl save` saves it later); the reply's
"warning" notes anything that went partly wrong. Captures that wait for the
user answer when they finish; exit 7 means the user cancelled.
"""

APPLY_EPILOG: Final = """\
operations (JSON array, or {"ops": [...]}; '-' reads stdin; run in order as
one undo step; if one fails nothing changes and the error names it):
  {"op": "add", "annotation": {...}}              new annotation; "id" optional
  {"op": "update", "id": ID, "set": {...}}        merge fields (style merges too)
  {"op": "delete", "ids": [ID, ...]}
  {"op": "restack", "ids": [ID], "to": "top"}     or "bottom"
  {"op": "crop", "rect": [x, y, w, h]}
  {"op": "rotate", "direction": "right"}          or "left"
  {"op": "flip", "axis": "horizontal"}            or "vertical"
  {"op": "resize", "width": W, "height": H}
  {"op": "set_image", "path": "C:/x.png"}         replace the pixels beneath

annotation kinds (rects [x, y, w, h], points [x, y], image pixels):
  rectangle, ellipse  rect, optional text (label inside)
  line, arrow         start, end
  text                rect, text, optional auto_fit (default true: grows from
                      the rect's top-left to fit; false keeps the rect, wraps)
  counter             center; label defaults to the next number
  highlight           rect (translucent marker)
  obfuscate           rect (pixelates beneath: hide secrets)
  freehand            points [[x, y], ...]

style (optional; omitted fields use the user's tool style): stroke, fill,
outline (#rrggbb or #rrggbbaa; #00000000 is clear), width, font_family,
font_size, bold, italic, shadow, h_align (left/center/right), v_align
(top/middle/bottom).

After rotate or resize, later coordinates are in the new image. To edit
pixels: `render --base --max-side 0 -o base.png`, change it, then set_image
it (same size keeps the crop). Example:
  [{"op": "add", "annotation": {"kind": "arrow", "start": [300, 40],
    "end": [245, 95], "style": {"stroke": "#e53935", "width": 4}}}]
"""

RENDER_DESCRIPTION: Final = """\
Write a PNG of one image as it is now, unsaved edits included, and print its
"path" (view it: Claude Code Read, Codex view_image). --grid labels lines in
image pixels, the coordinates every edit uses. With --region or downscaling,
image = origin + preview / scale, from the reply's "origin" and "scale".
"""


def add_ctl(commands: Subparsers, shared: argparse.ArgumentParser) -> Subparsers:
    """Define ``verdiclip ctl`` and its commands; return the commands."""
    ctl = commands.add_parser(
        "ctl",
        parents=[shared],
        help="Drive the running app: capture, open, edit, save, close (JSON).",
        description=CTL_DESCRIPTION,
        epilog=CTL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    methods = ctl.add_subparsers(dest="method", required=True, metavar="COMMAND")
    doc = argparse.ArgumentParser(add_help=False)
    doc.add_argument(
        "--doc", default=None, help="Document id (default: the active tab)."
    )
    guard = argparse.ArgumentParser(add_help=False)
    guard.add_argument(
        "--expect-state",
        type=int,
        default=None,
        help="Exit 4 unless the image is still at this state (from get or list).",
    )
    command = _Commands(methods, shared, doc, guard)
    command.add("status", "Check that the app is running and reachable.")
    command.add("list", "List open images in every window, with ids and states.")
    command.add("targets", "List monitors and windows to capture, in desktop pixels.")
    _add_capture(command)
    _add_files(command)
    _add_reading(command)
    _add_edits(command)
    _add_skill(command)
    return methods


def command_names() -> tuple[str, ...]:
    """Return every ``verdiclip ctl`` command, in help order."""
    root = argparse.ArgumentParser(add_help=False)
    methods = add_ctl(root.add_subparsers(), argparse.ArgumentParser(add_help=False))
    return tuple(methods.choices)


class _Commands:
    """Adds ``ctl`` commands with their shared option groups."""

    def __init__(
        self,
        methods: Subparsers,
        shared: argparse.ArgumentParser,
        doc: argparse.ArgumentParser,
        guard: argparse.ArgumentParser,
    ) -> None:
        self.doc, self.guard = doc, guard
        self._methods, self._shared = methods, shared

    def add(
        self,
        name: str,
        summary: str,
        *groups: argparse.ArgumentParser,
        description: str | None = None,
        epilog: str | None = None,
    ) -> argparse.ArgumentParser:
        return self._methods.add_parser(
            name,
            parents=[self._shared, *groups],
            help=summary,
            description=_wrapped(description or summary),
            epilog=epilog,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )


def _wrapped(text: str) -> str:
    """Wrap one-paragraph descriptions; keep laid-out ones as they are."""
    return text if "\n" in text else textwrap.fill(text, 79)


def _add_capture(command: _Commands) -> None:
    capture = command.add(
        "capture",
        "Capture a region, window, monitor or scrolling panel into a new tab.",
        description=CAPTURE_DESCRIPTION,
    )
    capture.add_argument("mode", choices=[k.value for k in CaptureKind])
    capture.add_argument(
        "--region", metavar="X,Y,W,H", help="X,Y,W,H to capture (region)."
    )
    capture.add_argument(
        "--title", metavar="TEXT", help="Window whose title contains TEXT."
    )
    capture.add_argument(
        "--handle", type=int, metavar="N", help="Window handle from targets."
    )
    capture.add_argument(
        "--at", metavar="X,Y", help="X,Y: the window or panel at this point."
    )
    capture.add_argument(
        "--monitor", type=int, metavar="N", help="Monitor number from targets."
    )
    capture.add_argument("--all", action="store_true", help="Every monitor.")
    capture.add_argument(
        "--from",
        dest="start",
        choices=["top", "here"],
        default="top",
        help="Scrolling: start at the top (default) or the current position.",
    )
    capture.add_argument(
        "-o", "--output", metavar="FILE", help="Also save to this file."
    )
    capture.add_argument(
        "--copy", action="store_true", help="Also copy to the clipboard."
    )
    capture.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Seconds to wait first.",
    )
    capture.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            f"Seconds to wait for the result (default {DEFAULT_TIMEOUT_S:g}; "
            f"{CHOOSING_TIMEOUT_S:g} when the user chooses or it scrolls)."
        ),
    )


def _add_files(command: _Commands) -> None:
    opened = command.add(
        "open",
        "Open an image file in a new tab; prints its summary with 'id'.",
        description=(
            "Open an image file in a new editor tab and print its summary. Use "
            "its 'id' with --doc: the tab isn't brought to the front while the "
            "user has a menu or dialog open."
        ),
    )
    opened.add_argument("path", help="Image file to open.")
    save = command.add(
        "save",
        "Save as the editor's Save would (its file, or a new automatic name).",
        command.doc,
        command.guard,
        description=(
            "Save as the editor's Save would. Without --path: the image's own "
            "file, or the next automatic name in the user's save folder. With "
            "--path the image is saved there from now on (Save As). The "
            "extension sets the format: png, jpg, bmp, gif, tiff or webp. "
            "Prints 'path'."
        ),
    )
    save.add_argument("--path", help="File to write instead (Save As).")
    export = command.add(
        "export",
        "Write a copy to a file; the image keeps its own file.",
        command.doc,
        command.guard,
        description=(
            "Write the image, annotations flattened, to --path. Unlike save "
            "--path, a later save still goes to the image's own file. The "
            "extension sets the format."
        ),
    )
    export.add_argument("--path", required=True, help="File to write.")
    command.add(
        "copy",
        "Copy the image to the clipboard as the editor's Copy would.",
        command.doc,
        command.guard,
    )
    close = command.add(
        "close",
        "Close a tab without asking; unsaved changes need --discard.",
        command.doc,
        command.guard,
        description=(
            "Close an image's tab without the unsaved-changes prompt. If it "
            "wasn't saved or copied since its last change, this exits 4 "
            "unless --discard is given. Close only what the user asked to."
        ),
    )
    close.add_argument(
        "--discard",
        action="store_true",
        help="Throw away changes that weren't saved or copied.",
    )


def _add_reading(command: _Commands) -> None:
    command.add(
        "get",
        "Print one image's annotations (with ids), crop, selection and state.",
        command.doc,
    )
    render = command.add(
        "render",
        "Write a PNG of one image as it is now and print its path.",
        command.doc,
        description=RENDER_DESCRIPTION,
    )
    render.add_argument("-o", "--output", help="PNG to write (default: temp).")
    render.add_argument(
        "--region", metavar="X,Y,W,H", help="X,Y,W,H in image pixels to render."
    )
    render.add_argument(
        "--max-side",
        type=int,
        default=DEFAULT_MAX_SIDE,
        help="Downscale to fit this longest side (default: %(default)s; 0: off).",
    )
    render.add_argument(
        "--grid", type=int, default=0, help="Labelled lines every N image pixels."
    )
    render.add_argument(
        "--base", action="store_true", help="The whole image, no annotations/crop."
    )
    command.add(
        "history",
        "List undo and redo steps with their labels, states and 'cli' flag.",
        command.doc,
    )


def _add_edits(command: _Commands) -> None:
    apply = command.add(
        "apply",
        "Apply edit operations as one undo step labelled 'CLI: ...'.",
        command.doc,
        command.guard,
        description=(
            "Apply edit operations to one image as a single undo step named "
            "'CLI: LABEL'. Prints the new state and the ids added. Pass "
            "--expect-state from get so a user's change in between exits 4."
        ),
        epilog=APPLY_EPILOG,
    )
    apply.add_argument(
        "--ops", required=True, help="JSON file of operations ('-': stdin)."
    )
    apply.add_argument("--label", help="Undo step name after 'CLI: '.")
    apply.add_argument(
        "--select", action="store_true", help="Select the annotations added."
    )
    for verb in ("undo", "redo"):
        travel = command.add(
            verb,
            f"{verb.capitalize()} steps (default: one).",
            command.doc,
            command.guard,
        )
        amount = travel.add_mutually_exclusive_group()
        amount.add_argument("--steps", type=int, help="Number of steps.")
        amount.add_argument("--to", type=int, help="State to reach (from history).")
        travel.add_argument(
            "--only-cli",
            action="store_true",
            help="Exit 4 rather than touch a step the user made in the editor.",
        )
    select = command.add(
        "select", "Select annotations by id (no ids clears).", command.doc
    )
    select.add_argument("ids", nargs="*", help="Annotation ids.")


def _add_skill(command: _Commands) -> None:
    skill = command.add(
        "install-skill",
        "Install the VerdiClip skill for AI coding agents (no app needed).",
        description=(
            "Write the VerdiClip skill where AI coding agents find it. Also in "
            "VerdiClip: Settings > General > Install AI agent skill."
        ),
    )
    skill.add_argument(
        "--agent", required=True, help=f"Comma-separated: {', '.join(AGENTS)}."
    )
    skill.add_argument(
        "--scope",
        choices=[s.value for s in SkillScope],
        default=SkillScope.USER.value,
        help="user: every project (default); project: the current folder.",
    )
    skill.add_argument("--dir", help="Skills folder to use instead.")


# Parameters


def ctl_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    """Return the request parameters of ``verdiclip ctl <method>``."""
    method = str(args.method)
    params: JsonObject = {}
    if getattr(args, "doc", None):
        params["doc"] = str(args.doc)
    if getattr(args, "expect_state", None) is not None:
        params["expect_state"] = int(args.expect_state)
    builders: dict[str, Callable[[argparse.Namespace, Fail], JsonObject]] = {
        "render": _render_params,
        "apply": _apply_params,
        "undo": _travel_params,
        "redo": _travel_params,
        "select": _select_params,
        "open": _path_params,
        "save": _path_params,
        "export": _path_params,
        "close": _close_params,
        "capture": _capture_params,
        "install-skill": _skill_params,
    }
    if method in builders:
        params |= builders[method](args, fail)
    return params


def parse_rect(text: str, fail: Fail, option: str = "--region") -> Rect:
    """Parse ``X,Y,W,H`` integers."""
    values = _integers(text, 4, option, "X,Y,WIDTH,HEIGHT", fail)
    x, y, w, h = values
    if w <= 0 or h <= 0:
        fail(f"{option} width and height must be positive, got {text!r}")
    return Rect(x, y, w, h)


def _integers(text: str, count: int, option: str, shape: str, fail: Fail) -> list[int]:
    try:
        values = [int(part) for part in text.split(",")]
    except ValueError:
        values = []
    if len(values) != count:
        fail(f"{option} must be {shape} integers, got {text!r}")
    return values


def _capture_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    params = _capture_target(args, fail)
    try:
        spec = CaptureSpec.from_params(params)
    except AutomationError as err:
        fail(err.message.replace("from here", "--from here"))
    if args.delay < 0:
        fail(f"--delay must be >= 0, got {args.delay}")
    if args.timeout is not None and args.timeout <= 0:
        fail(f"--timeout must be > 0, got {args.timeout}")
    params["delay"] = float(args.delay)
    waits = spec.interactive or spec.kind is CaptureKind.SCROLLING
    default = CHOOSING_TIMEOUT_S if waits else DEFAULT_TIMEOUT_S
    params["timeout"] = float(args.timeout or default) + float(args.delay)
    return params


def _capture_target(args: argparse.Namespace, fail: Fail) -> JsonObject:
    """Return what to capture and where the result goes."""
    params: JsonObject = {"mode": str(args.mode)}
    if args.region:
        r = parse_rect(str(args.region), fail)
        params["region"] = [r.x, r.y, r.width, r.height]
    if args.at:
        params["at"] = list(_integers(str(args.at), 2, "--at", "X,Y", fail))
    options: dict[str, JsonValue] = {
        "title": args.title,
        "handle": args.handle,
        "monitor": args.monitor,
        "all": True if args.all else None,
        "from": None if args.start == "top" else str(args.start),
        "output": str(Path(args.output).resolve()) if args.output else None,
        "copy": True if args.copy else None,
    }
    params |= {key: value for key, value in options.items() if value is not None}
    return params


def _render_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    params: JsonObject = {}
    if args.output:
        params["output"] = str(Path(args.output).resolve())
    if args.region:
        region = parse_rect(str(args.region), fail)
        params["region"] = [region.x, region.y, region.width, region.height]
    if args.max_side < 0:
        fail(f"--max-side must be >= 0, got {args.max_side}")
    if args.grid < 0:
        fail(f"--grid must be >= 0, got {args.grid}")
    params["max_side"] = int(args.max_side)
    params["grid"] = int(args.grid)
    params["base"] = bool(args.base)
    return params


def _apply_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    params: JsonObject = {
        "ops": _read_ops(str(args.ops), fail),
        "select": bool(args.select),
    }
    if args.label is not None:
        params["label"] = str(args.label)
    return params


def _travel_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    del fail
    params: JsonObject = {"only_cli": bool(args.only_cli)}
    if args.steps is not None:
        params["steps"] = int(args.steps)
    if args.to is not None:
        params["to"] = int(args.to)
    return params


def _close_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    del fail
    return {"discard": bool(args.discard)}


def _skill_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    try:
        agents = parse_agents(str(args.agent))
    except ValueError as err:
        fail(f"--agent: {err}")
    if args.dir is not None and len(agents) != 1:
        fail("--dir installs for one agent at a time")
    params: JsonObject = {"agents": list(agents), "scope": str(args.scope)}
    if args.dir is not None:
        params["dir"] = str(Path(args.dir).resolve())
    return params


def _path_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    """Return ``path`` made absolute, since the app runs in another folder."""
    del fail
    path = getattr(args, "path", None)
    return {} if path is None else {"path": str(Path(path).resolve())}


def _select_params(args: argparse.Namespace, fail: Fail) -> JsonObject:
    del fail
    return {"ids": [str(i) for i in args.ids]}


def _read_ops(source: str, fail: Fail) -> JsonValue:
    """Read the operations array from a JSON file, or stdin for ``-``.

    Accepts either the array itself or an object with an ``ops`` array.
    """
    try:
        if source == "-":
            text = sys.stdin.read()
        else:
            text = Path(source).read_text(encoding="utf-8-sig")
        # json.loads only ever produces JSON-compatible values
        data = cast(JsonValue, json.loads(text))
    except (OSError, json.JSONDecodeError) as err:
        fail(f"--ops: can't read JSON from {source!r}: {err}")
    if isinstance(data, dict) and "ops" in data:
        data = data["ops"]
    if not isinstance(data, list):
        fail("--ops must hold a JSON array of operations")
    for operation in data:
        # The app runs in another folder, so image paths become absolute here
        if isinstance(operation, dict) and isinstance(operation.get("path"), str):
            operation["path"] = str(Path(str(operation["path"])).resolve())
    return data
