"""Install the VerdiClip skill where AI coding agents look for skills."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

SKILL_NAME = "verdiclip"
SKILL_FILE = "SKILL.md"


class SkillScope(StrEnum):
    """Install for every project of this user, or for one project."""

    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class AgentHome:
    """Where one agent reads skills: under the home folder and in a project."""

    user: str
    project: str


AGENTS: dict[str, AgentHome] = {
    "claude": AgentHome(user=".claude/skills", project=".claude/skills"),
    "codex": AgentHome(user=".codex/skills", project=".codex/skills"),
    "agents": AgentHome(user=".agents/skills", project=".agents/skills"),
    "copilot": AgentHome(user=".copilot/skills", project=".github/skills"),
}


@dataclass(frozen=True, slots=True)
class Installed:
    """One skill file written, and whether it replaced an older copy."""

    agent: str
    path: Path
    replaced: bool


def skill_text() -> str:
    """Return the skill shipped with this version of VerdiClip."""
    package = resources.files("verdiclip.automation") / "skill" / SKILL_FILE
    return package.read_text(encoding="utf-8")


def parse_agents(text: str) -> list[str]:
    """Return the agent names in a comma-separated list, in order, once each.

    Raises:
        ValueError: If the list is empty or names an unknown agent.
    """
    names = [n.strip().lower() for n in text.split(",") if n.strip()]
    if not names:
        msg = f"name at least one agent: {', '.join(AGENTS)}"
        raise ValueError(msg)
    unknown = [n for n in names if n not in AGENTS]
    if unknown:
        msg = f"unknown agent {unknown[0]!r}; choose from {', '.join(AGENTS)}"
        raise ValueError(msg)
    return list(dict.fromkeys(names))


def install_skill(
    agents: list[str],
    *,
    scope: SkillScope = SkillScope.USER,
    root: Path | None = None,
    home: Path | None = None,
    project: Path | None = None,
) -> list[Installed]:
    """Write ``<skills folder>/verdiclip/SKILL.md`` for each agent.

    ``root`` replaces the agents' own skills folder (one agent only).

    Raises:
        ValueError: If ``root`` is given for more than one agent.
    """
    if root is not None and len(agents) != 1:
        msg = "--dir installs for one agent at a time"
        raise ValueError(msg)
    text = skill_text()
    written: list[Installed] = []
    for agent in agents:
        if root is not None:
            folder = root
        elif scope is SkillScope.USER:
            folder = (home or Path.home()) / AGENTS[agent].user
        else:
            folder = (project or Path.cwd()) / AGENTS[agent].project
        target = folder / SKILL_NAME / SKILL_FILE
        replaced = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(Installed(agent, target.resolve(), replaced=replaced))
    return written
