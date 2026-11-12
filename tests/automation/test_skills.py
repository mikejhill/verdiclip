"""The skill installs where each agent looks for it, and nowhere else."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from verdiclip.automation.command import run_ctl
from verdiclip.automation.protocol import ExitCode
from verdiclip.automation.skills import (
    AGENTS,
    SkillScope,
    install_skill,
    parse_agents,
    skill_text,
)


def test_skill_describes_the_commands_it_needs() -> None:
    """The shipped skill names itself and teaches the edit workflow."""
    text = skill_text()

    assert text.startswith("---\nname: verdiclip\ndescription: ")
    for command in (
        "ctl list",
        "ctl render",
        "ctl get",
        "ctl apply",
        "undo --only-ctl",
    ):
        assert command in text


@pytest.mark.parametrize(
    ("agent", "scope", "expected"),
    [
        ("claude", SkillScope.USER, "home/.claude/skills"),
        ("codex", SkillScope.USER, "home/.codex/skills"),
        ("agents", SkillScope.USER, "home/.agents/skills"),
        ("copilot", SkillScope.USER, "home/.copilot/skills"),
        ("claude", SkillScope.PROJECT, "repo/.claude/skills"),
        ("copilot", SkillScope.PROJECT, "repo/.github/skills"),
    ],
)
def test_each_agent_gets_its_own_folder(
    tmp_path: Path, agent: str, scope: SkillScope, expected: str
) -> None:
    """User scope writes under home; project scope under the project."""
    written = install_skill(
        [agent], scope=scope, home=tmp_path / "home", project=tmp_path / "repo"
    )

    target = tmp_path / expected / "verdiclip" / "SKILL.md"
    assert [(w.agent, w.path, w.replaced) for w in written] == [
        (agent, target.resolve(), False)
    ]
    assert target.read_text(encoding="utf-8") == skill_text()


def test_reinstalling_replaces_the_old_copy(tmp_path: Path) -> None:
    """An older skill is overwritten and reported as replaced."""
    target = tmp_path / "skills" / "verdiclip" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")

    written = install_skill(["codex"], root=tmp_path / "skills")

    assert written[0].replaced
    assert target.read_text(encoding="utf-8") == skill_text()


def test_dir_takes_one_agent(tmp_path: Path) -> None:
    """A custom folder can't serve several agents at once."""
    with pytest.raises(ValueError, match="one agent"):
        install_skill(["claude", "codex"], root=tmp_path)


def test_default_folders_are_home_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without overrides, user scope is the home folder and project is cwd."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)

    user = install_skill(["agents"])
    project = install_skill(["agents"], scope=SkillScope.PROJECT)

    assert user[0].path.is_relative_to((tmp_path / "home").resolve())
    assert project[0].path == (tmp_path / ".agents/skills/verdiclip/SKILL.md").resolve()


@pytest.mark.parametrize(
    ("text", "expected"),
    [("claude", ["claude"]), (" Codex, claude ,codex", ["codex", "claude"])],
)
def test_agent_lists(text: str, expected: list[str]) -> None:
    """Agents are comma-separated, case-insensitive and deduplicated."""
    assert parse_agents(text) == expected


@pytest.mark.parametrize(
    ("text", "match"), [(" , ", "at least one"), ("gpt", "unknown")]
)
def test_bad_agent_lists(text: str, match: str) -> None:
    """Empty lists and unknown agents are refused, naming the choices."""
    with pytest.raises(ValueError, match=match) as caught:
        parse_agents(text)

    assert all(agent in str(caught.value) for agent in AGENTS)


def test_ctl_installs_without_the_app(tmp_path: Path) -> None:
    """``ctl install-skill`` writes files locally and prints where."""
    out, err = io.StringIO(), io.StringIO()

    code = run_ctl(
        "install-skill",
        {"agents": ["claude"], "scope": "user", "dir": str(tmp_path)},
        out,
        err,
    )

    result = json.loads(out.getvalue())
    assert code == ExitCode.OK
    assert result["installed"][0]["path"] == str(
        (tmp_path / "verdiclip" / "SKILL.md").resolve()
    )


def test_ctl_reports_write_failures(tmp_path: Path) -> None:
    """A folder that can't be created fails with exit code 1."""
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()

    code = run_ctl(
        "install-skill", {"agents": ["claude"], "dir": str(blocker)}, out, err
    )

    assert code == ExitCode.FAILED
    assert "Could not write the skill" in err.getvalue()
