"""Command-line parsing into typed requests."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import cast

from verdiclip import VERSION
from verdiclip.automation.protocol import DEFAULT_MAX_SIDE
from verdiclip.automation.skills import AGENTS, SkillScope, parse_agents
from verdiclip.document.annotations import JsonObject, JsonValue
from verdiclip.geometry import Rect

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class Command(StrEnum):
    """What the process was asked to do."""

    TRAY = "tray"
    CAPTURE = "capture"
    OPEN = "open"
    CTL = "ctl"


class HeadlessMode(StrEnum):
    """Capture modes available without the overlay."""

    SCREEN = "screen"
    WINDOW = "window"
    REGION = "region"


@dataclass(frozen=True, slots=True)
class CliRequest:
    """A parsed command line."""

    command: Command
    log_level: str = "INFO"
    mode: HeadlessMode = HeadlessMode.SCREEN
    output: Path | None = None
    region: Rect | None = None
    delay: float = 0.0
    to_clipboard: bool = False
    files: tuple[Path, ...] = ()
    method: str = ""
    params: JsonObject = field(default_factory=dict)


class CommandLine:
    """Parse ``verdiclip`` arguments."""

    def __init__(self) -> None:
        self._parser = self._build_parser()

    def parse(self, argv: Sequence[str] | None = None) -> CliRequest:
        """Return the typed request for ``argv``; exits with code 2 on bad input."""
        args = self._parser.parse_args(argv)
        level = str(args.log_level)
        if args.command is None:
            return CliRequest(command=Command.TRAY, log_level=level)
        if args.command == Command.CTL:
            return self._ctl_request(args, level)
        if args.command == Command.OPEN:
            return CliRequest(
                command=Command.OPEN,
                log_level=level,
                files=tuple(Path(f) for f in args.files),
            )
        mode = HeadlessMode(args.mode)
        region = self._parse_region(args.region) if args.region else None
        if mode is HeadlessMode.REGION and region is None:
            self._parser.error("capture region needs --region X,Y,WIDTH,HEIGHT")
        if args.delay < 0:
            self._parser.error(f"--delay must be >= 0, got {args.delay}")
        return CliRequest(
            command=Command.CAPTURE,
            log_level=level,
            mode=mode,
            output=Path(args.output) if args.output else None,
            region=region,
            delay=float(args.delay),
            to_clipboard=bool(args.clipboard),
        )

    def _ctl_request(self, args: argparse.Namespace, level: str) -> CliRequest:
        """Build the control request for ``verdiclip ctl <method>``."""
        method = str(args.method)
        params: JsonObject = {}
        if getattr(args, "doc", None):
            params["doc"] = str(args.doc)
        if getattr(args, "expect_state", None) is not None:
            params["expect_state"] = int(args.expect_state)
        builders = {
            "render": self._render_params,
            "apply": self._apply_params,
            "undo": self._travel_params,
            "redo": self._travel_params,
            "select": self._select_params,
            "install-skill": self._skill_params,
        }
        if method in builders:
            params |= builders[method](args)
        return CliRequest(
            command=Command.CTL, log_level=level, method=method, params=params
        )

    def _render_params(self, args: argparse.Namespace) -> JsonObject:
        """Return the parameters of ``ctl render``."""
        params: JsonObject = {}
        if args.output:
            params["output"] = str(Path(args.output).resolve())
        if args.region:
            region = self._parse_region(args.region)
            params["region"] = [region.x, region.y, region.width, region.height]
        if args.max_side < 0:
            self._parser.error(f"--max-side must be >= 0, got {args.max_side}")
        if args.grid < 0:
            self._parser.error(f"--grid must be >= 0, got {args.grid}")
        params["max_side"] = int(args.max_side)
        params["grid"] = int(args.grid)
        params["base"] = bool(args.base)
        return params

    def _apply_params(self, args: argparse.Namespace) -> JsonObject:
        """Return the parameters of ``ctl apply``."""
        params: JsonObject = {
            "ops": self._read_ops(str(args.ops)),
            "select": bool(args.select),
        }
        if args.label is not None:
            params["label"] = str(args.label)
        return params

    @staticmethod
    def _travel_params(args: argparse.Namespace) -> JsonObject:
        """Return the parameters of ``ctl undo`` and ``ctl redo``."""
        params: JsonObject = {"only_cli": bool(args.only_cli)}
        if args.steps is not None:
            params["steps"] = int(args.steps)
        if args.to is not None:
            params["to"] = int(args.to)
        return params

    def _skill_params(self, args: argparse.Namespace) -> JsonObject:
        """Return the parameters of ``ctl install-skill``."""
        try:
            agents = parse_agents(str(args.agent))
        except ValueError as err:
            self._parser.error(f"--agent: {err}")
        if args.dir is not None and len(agents) != 1:
            self._parser.error("--dir installs for one agent at a time")
        params: JsonObject = {"agents": list(agents), "scope": str(args.scope)}
        if args.dir is not None:
            params["dir"] = str(Path(args.dir).resolve())
        return params

    @staticmethod
    def _select_params(args: argparse.Namespace) -> JsonObject:
        """Return the parameters of ``ctl select``."""
        return {"ids": [str(i) for i in args.ids]}

    def _read_ops(self, source: str) -> JsonValue:
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
            self._parser.error(f"--ops: can't read JSON from {source!r}: {err}")
        if isinstance(data, dict) and "ops" in data:
            data = data["ops"]
        if not isinstance(data, list):
            self._parser.error("--ops must hold a JSON array of operations")
        return data

    def _parse_region(self, text: str) -> Rect:
        """Parse ``X,Y,W,H`` in physical desktop pixels."""
        try:
            values = [int(part) for part in text.split(",")]
        except ValueError:
            values = []
        if len(values) != 4:  # noqa: PLR2004 — x, y, width, height
            self._parser.error(
                f"--region must be X,Y,WIDTH,HEIGHT integers, got {text!r}"
            )
        x, y, w, h = values
        if w <= 0 or h <= 0:
            self._parser.error(
                f"--region width and height must be positive, got {text!r}"
            )
        return Rect(x, y, w, h)

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        """Define the arguments."""
        parser = argparse.ArgumentParser(
            prog="verdiclip",
            description=(
                "Screenshot capture and annotation. "
                "Without a command, runs in the system tray."
            ),
        )
        parser.add_argument(
            "--version", action="version", version=f"%(prog)s {VERSION}"
        )
        parser.add_argument(
            "--log-level",
            default="INFO",
            choices=LOG_LEVELS,
            help="Logging verbosity (default: %(default)s).",
        )
        # Accept global options after the subcommand too
        shared = argparse.ArgumentParser(add_help=False)
        shared.add_argument(
            "--log-level",
            default=argparse.SUPPRESS,
            choices=LOG_LEVELS,
            help="Logging verbosity.",
        )
        commands = parser.add_subparsers(dest="command")
        capture = commands.add_parser(
            "capture",
            parents=[shared],
            help="Capture without the overlay and exit.",
        )
        capture.add_argument(
            "mode", choices=[m.value for m in HeadlessMode], help="What to capture."
        )
        capture.add_argument(
            "-o",
            "--output",
            help="File to write (default: the configured folder and pattern).",
        )
        capture.add_argument(
            "--region", help="X,Y,WIDTH,HEIGHT in physical pixels (region mode)."
        )
        capture.add_argument(
            "--delay", type=float, default=0.0, help="Seconds to wait before capturing."
        )
        capture.add_argument(
            "--clipboard",
            action="store_true",
            help="Copy to the clipboard instead of saving.",
        )
        open_cmd = commands.add_parser(
            "open", parents=[shared], help="Open images in the editor."
        )
        open_cmd.add_argument("files", nargs="+", help="Image files to open.")
        CommandLine._add_ctl(commands, shared)
        return parser

    @staticmethod
    def _add_ctl(
        commands: argparse._SubParsersAction[argparse.ArgumentParser],
        shared: argparse.ArgumentParser,
    ) -> None:
        """Define ``ctl``: read and edit open documents from the command line."""
        ctl = commands.add_parser(
            "ctl",
            parents=[shared],
            help="Read and edit documents open in the running app (prints JSON).",
            description=(
                "Read and edit documents open in the running VerdiClip, including "
                "unsaved edits. Prints JSON. Exit codes: 0 ok, 1 failed, 2 bad usage, "
                "3 not running or turned off, 4 conflict, 5 not found, 6 busy."
            ),
        )
        methods = ctl.add_subparsers(dest="method", required=True)
        methods.add_parser(
            "status", parents=[shared], help="Check the app is running and reachable."
        )
        methods.add_parser(
            "list", parents=[shared], help="List open documents in every window."
        )
        doc = argparse.ArgumentParser(add_help=False)
        doc.add_argument(
            "--doc",
            default=None,
            help="Document id from `ctl list` (default: the active tab).",
        )
        methods.add_parser(
            "get",
            parents=[shared, doc],
            help="Print one document's annotations, crop, selection and state.",
        )
        render = methods.add_parser(
            "render",
            parents=[shared, doc],
            help="Write a PNG of one document as it is now, unsaved edits included.",
        )
        render.add_argument(
            "-o", "--output", help="PNG file to write (default: a preview folder)."
        )
        render.add_argument(
            "--region", help="X,Y,WIDTH,HEIGHT in image pixels to render alone."
        )
        render.add_argument(
            "--max-side",
            type=int,
            default=DEFAULT_MAX_SIDE,
            help="Downscale so the longest side fits (default: %(default)s; 0: off).",
        )
        render.add_argument(
            "--grid",
            type=int,
            default=0,
            help="Draw lines every N image pixels, labelled in image coordinates.",
        )
        render.add_argument(
            "--base",
            action="store_true",
            help="Render the image without annotations.",
        )
        CommandLine._add_ctl_edits(methods, shared, doc)

    @staticmethod
    def _add_ctl_edits(
        methods: argparse._SubParsersAction[argparse.ArgumentParser],
        shared: argparse.ArgumentParser,
        doc: argparse.ArgumentParser,
    ) -> None:
        """Define the ``ctl`` methods that change a document."""
        guard = argparse.ArgumentParser(add_help=False)
        guard.add_argument(
            "--expect-state",
            type=int,
            default=None,
            help="Fail with exit code 4 unless the document is still at this state.",
        )
        apply = methods.add_parser(
            "apply",
            parents=[shared, doc, guard],
            help="Apply edit operations as one undo step labelled 'CLI: ...'.",
        )
        apply.add_argument(
            "--ops",
            required=True,
            help="JSON file holding the operations array ('-' reads stdin).",
        )
        apply.add_argument("--label", help="Name of the undo step after 'CLI: '.")
        apply.add_argument(
            "--select", action="store_true", help="Select the annotations added."
        )
        methods.add_parser(
            "history",
            parents=[shared, doc],
            help="List the undo and redo steps with their states.",
        )
        for verb in ("undo", "redo"):
            travel = methods.add_parser(
                verb,
                parents=[shared, doc, guard],
                help=f"{verb.capitalize()} steps (default: one).",
            )
            amount = travel.add_mutually_exclusive_group()
            amount.add_argument("--steps", type=int, help="Number of steps.")
            amount.add_argument(
                "--to", type=int, help="State to reach (from `ctl history`)."
            )
            travel.add_argument(
                "--only-cli",
                action="store_true",
                help="Fail with exit code 4 instead of touching a step the user made.",
            )
        select = methods.add_parser(
            "select",
            parents=[shared, doc],
            help="Select annotations by id (no ids clears the selection).",
        )
        select.add_argument("ids", nargs="*", help="Annotation ids.")
        skill = methods.add_parser(
            "install-skill",
            parents=[shared],
            help="Install the VerdiClip skill for AI coding agents (no app needed).",
        )
        skill.add_argument(
            "--agent",
            required=True,
            help=f"Comma-separated agents: {', '.join(AGENTS)}.",
        )
        skill.add_argument(
            "--scope",
            choices=[s.value for s in SkillScope],
            default=SkillScope.USER.value,
            help="user: every project (default); project: the current folder.",
        )
        skill.add_argument(
            "--dir", help="Skills folder to use instead of the agent's own."
        )
