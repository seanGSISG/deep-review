"""`deep-review setup`: whether the machine is ready for a Run, and the Skill linked."""

from pathlib import Path

import pytest

from conftest import FakeReviewer
from deep_review.cli import main


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh home, so the Skill links land somewhere disposable."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def coding_agent(tmp_path: Path, name: str) -> None:
    """Put a stand-in Coding agent on the PATH the `reviewer` fixture already prepended."""
    binary = tmp_path / "bin" / name
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)


def test_a_ready_machine_is_all_ok(
    home: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup"]) == 0

    out = capsys.readouterr().out
    assert "reviewer  ok        codex 0.159.2" in out
    assert "login     ok" in out
    assert "skill     ok" in out


def test_no_codex_is_missing_with_the_install_hint(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert main(["setup"]) == 2

    out = capsys.readouterr().out
    assert "reviewer  missing   codex: install it with npm install -g @openai/codex" in out
    assert "login     missing   install codex first" in out


def test_an_old_codex_is_missing_until_updated(
    home: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_report_version("codex-cli 0.156.1")

    assert main(["setup"]) == 2

    assert "0.156.1 is older than 0.159.2" in capsys.readouterr().out


def test_a_machine_not_logged_in_is_told_to_run_codex_login(
    home: Path,
    reviewer: FakeReviewer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "never-logged-in"))

    assert main(["setup"]) == 2

    assert "login     missing   no Codex login" in capsys.readouterr().out


def test_the_skill_is_linked_only_for_the_agents_that_are_present(
    home: Path,
    reviewer: FakeReviewer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The stand-ins and git alone: the developer's own opencode and pi must not be found.
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    coding_agent(tmp_path, "opencode")

    assert main(["setup"]) == 0

    assert (home / ".claude/skills/pre-pr-review").is_symlink(), "opencode reads ~/.claude/skills"
    assert not (home / ".pi").exists(), "no pi on PATH, nothing to link for it"
    assert "skill     ok" in capsys.readouterr().out


def test_pi_present_gets_its_own_link(
    home: Path,
    reviewer: FakeReviewer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    coding_agent(tmp_path, "pi")

    assert main(["setup"]) == 0

    assert (home / ".pi/agent/skills/pre-pr-review").is_symlink()


def test_a_foreign_skill_directory_is_reported_not_clobbered(
    home: Path,
    reviewer: FakeReviewer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    coding_agent(tmp_path, "opencode")
    theirs = home / ".claude/skills/pre-pr-review"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("theirs\n", encoding="utf-8")

    assert main(["setup"]) == 2

    assert "skill     missing" in capsys.readouterr().out
    assert not theirs.is_symlink()


def test_an_opencode_installed_outside_path_is_still_linked_for(
    home: Path,
    reviewer: FakeReviewer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    target = home / ".opencode" / "bin" / "opencode"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o755)

    assert main(["setup"]) == 0

    assert (home / ".claude/skills/pre-pr-review").is_symlink()
