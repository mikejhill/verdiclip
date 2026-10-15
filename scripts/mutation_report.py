"""Summarize a mutmut run: score per module, surviving mutants, and a gate.

A mutant is a small change to the code (``<`` to ``<=``, ``True`` to
``False``, a dropped call). The tests should fail for each one; a mutant they
let through ("survived") marks behavior no test checks.

Reads the results of the last ``mutmut run`` from ``mutmut results --all
true``, prints a Markdown report, and exits non-zero when the score is below
``--fail-under``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DETECTED: Final = frozenset({"killed", "timeout", "caught by type check", "segfault"})
UNDETECTED: Final = frozenset({"survived", "no tests", "suspicious"})


@dataclass(slots=True)
class ModuleScore:
    """Mutant outcomes for one module."""

    detected: int = 0
    undetected: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Mutants that count toward the score."""
        return self.detected + len(self.undetected)

    @property
    def score(self) -> float:
        """Share of mutants the tests caught, from 0 to 100."""
        return 100.0 if self.total == 0 else 100 * self.detected / self.total


@dataclass(slots=True)
class MutationReport:
    """Scores by module, parsed from ``mutmut results --all true``."""

    modules: dict[str, ModuleScore] = field(
        default_factory=lambda: defaultdict(ModuleScore)
    )
    statuses: Counter[str] = field(default_factory=Counter)

    @classmethod
    def parse(cls, text: str) -> MutationReport:
        """Return the report for mutmut's ``name: status`` lines."""
        report = cls()
        for line in text.splitlines():
            name, sep, status = line.strip().rpartition(": ")
            if not sep or "__mutmut_" not in name:
                continue
            report.statuses[status] += 1
            module = report.modules[name.rpartition(".")[0]]
            if status in DETECTED:
                module.detected += 1
            elif status in UNDETECTED:
                module.undetected.append(name)
        return report

    @property
    def overall(self) -> ModuleScore:
        """All modules combined."""
        total = ModuleScore()
        for module in self.modules.values():
            total.detected += module.detected
            total.undetected += module.undetected
        return total

    def markdown(self, *, show_diffs: int) -> str:
        """Return the report as Markdown, with diffs for the first survivors."""
        overall = self.overall
        lines = [
            "### Mutation testing",
            "",
            f"**Score: {overall.score:.1f}%** — {overall.detected} of "
            f"{overall.total} mutants caught by the tests.",
            "",
            "Outcomes: "
            + ", ".join(f"{n} {s}" for s, n in sorted(self.statuses.items())),
            "",
            "| Module | Score | Caught | Missed |",
            "| --- | ---: | ---: | ---: |",
        ]
        ranked = sorted(self.modules.items(), key=lambda item: item[1].score)
        lines += [
            f"| `{name}` | {m.score:.1f}% | {m.detected} | {len(m.undetected)} |"
            for name, m in ranked
        ]
        missed = overall.undetected
        if missed and show_diffs > 0:
            lines += ["", f"#### First {min(show_diffs, len(missed))} missed mutants"]
            lines += ["", "Each diff is a change no test noticed.", ""]
            for name in missed[:show_diffs]:
                lines += ["```diff", self.diff(name), "```"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def diff(name: str) -> str:
        """Return mutmut's diff for mutant ``name``."""
        shown = subprocess.run(  # noqa: S603 — fixed program, name from mutmut
            ["mutmut", "show", name],  # noqa: S607 — run inside the uv environment
            capture_output=True,
            text=True,
            check=False,
        )
        return shown.stdout.strip() or shown.stderr.strip()


def main() -> int:
    """Print the report; return 1 if the score is under ``--fail-under``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "results",
        type=Path,
        help="File holding the output of `mutmut results --all true`.",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="Minimum overall score in percent (default: no gate).",
    )
    parser.add_argument(
        "--show-diffs",
        type=int,
        default=0,
        help="Include the diffs of this many missed mutants.",
    )
    args = parser.parse_args()
    report = MutationReport.parse(args.results.read_text(encoding="utf-8"))
    sys.stdout.write(report.markdown(show_diffs=args.show_diffs))
    score = report.overall.score
    if score < args.fail_under:
        sys.stderr.write(
            f"Mutation score {score:.1f}% is below the {args.fail_under}% minimum\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
