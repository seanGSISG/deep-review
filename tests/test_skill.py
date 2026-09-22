"""The Skill: what it tells the Coding agent, and `install-skill` putting it in place."""

import json
import re
from pathlib import Path

import pytest

from deep_review import __version__
from deep_review.cli import main
from deep_review.resources import skill_dir

SKILL = skill_dir() / "SKILL.md"
ROOT = Path(__file__).resolve().parents[1]


def frontmatter() -> dict[str, str]:
    """The YAML block at the top of SKILL.md, as the loaders read it: one `key: value` per line."""
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in block.splitlines() if line)


def test_the_skill_is_named_after_its_directory() -> None:
    assert frontmatter()["name"] == skill_dir().name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", frontmatter()["name"])
    assert "review" in frontmatter()["description"].lower()


def test_the_skill_states_the_rules_of_the_round_loop() -> None:
    body = SKILL.read_text(encoding="utf-8")

    assert "two Rounds" in body, "the cap on Runs per task"
    assert "never `--json`" in body, "the whole Report must not land in the Coding agent's context"
    assert ".deep-review/findings.json" in body, "the Verifier reads the file, not a paste of it"
    assert "quote the code or run something" in body, "dismissal takes the Finding's own standard"
    assert "Uncertain counts as real for P0 and P1" in body
    assert "different model family" in body


def test_the_plugin_manifest_carries_the_package_version() -> None:
    """
    Claude Code pins a marketplace plugin to this version string, so a release that bumps the
    package but not the manifest strands plugin users on the old Skill.
    """
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["version"] == __version__
    assert manifest["name"] == skill_dir().name, "one Skill, one plugin, one name"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory with no skills in it, so the install has somewhere fresh to link into."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_install_links_the_skill_into_both_agent_directories(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-skill"]) == 0

    for link in (home / ".claude/skills/pre-pr-review", home / ".pi/agent/skills/pre-pr-review"):
        assert link.is_symlink()
        assert link.resolve() == skill_dir().resolve()
        assert (link / "SKILL.md").is_file()
    out = capsys.readouterr().out
    assert ".claude/skills/pre-pr-review" in out
    assert ".pi/agent/skills/pre-pr-review" in out


def test_target_picks_one_directory(home: Path) -> None:
    assert main(["install-skill", "--target", "claude"]) == 0

    assert (home / ".claude/skills/pre-pr-review").is_symlink()
    assert not (home / ".pi").exists()


def test_install_is_idempotent(home: Path) -> None:
    assert main(["install-skill", "--target", "pi"]) == 0
    assert main(["install-skill", "--target", "pi"]) == 0

    assert (home / ".pi/agent/skills/pre-pr-review").resolve() == skill_dir().resolve()


def test_a_target_that_is_not_our_symlink_is_refused_without_force(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    theirs = home / ".claude/skills/pre-pr-review"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("---\nname: pre-pr-review\n---\ntheirs\n", encoding="utf-8")

    assert main(["install-skill", "--target", "claude"]) == 2

    assert "--force" in capsys.readouterr().err
    assert not theirs.is_symlink()
    assert (theirs / "SKILL.md").read_text(encoding="utf-8").endswith("theirs\n")


def test_a_symlink_pointing_elsewhere_is_also_refused(home: Path) -> None:
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    link = home / ".pi/agent/skills/pre-pr-review"
    link.parent.mkdir(parents=True)
    link.symlink_to(elsewhere)

    assert main(["install-skill", "--target", "pi"]) == 2

    assert link.resolve() == elsewhere.resolve()


def test_force_replaces_whatever_was_there(home: Path) -> None:
    theirs = home / ".claude/skills/pre-pr-review"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("theirs\n", encoding="utf-8")

    assert main(["install-skill", "--target", "claude", "--force"]) == 0

    assert theirs.is_symlink()
    assert theirs.resolve() == skill_dir().resolve()
