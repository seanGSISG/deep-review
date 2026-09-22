"""`deep-review setup`: the machine made ready for a Run, installing what the CLI can."""

from pathlib import Path

import pytest

import deep_review.setup as dr_setup
from conftest import FakeReviewer
from deep_review.cli import main
from deep_review.credentials import store_key
from deep_review.reviewer import OPENCODE, Reviewer, api_key


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh home, so the Skill links land somewhere disposable."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def no_reviewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PATH with git alone on it, and the key exported: the binary is the only thing missing."""
    bin_dir = tmp_path / "only-git"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to("/usr/bin/git")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("Z_AI_API_KEY", "test-key")
    return bin_dir


@pytest.fixture
def installer(monkeypatch: pytest.MonkeyPatch, no_reviewer: Path) -> list[Reviewer]:
    """A stand-in for the official installer that puts a binary on PATH and records the call."""
    calls: list[Reviewer] = []

    def fake(reviewer: Reviewer) -> None:
        calls.append(reviewer)
        (no_reviewer / reviewer.binary).write_text("#!/bin/sh\n", encoding="utf-8")
        (no_reviewer / reviewer.binary).chmod(0o755)

    monkeypatch.setattr(dr_setup, "install_from_script", fake)
    return calls


def test_a_ready_machine_is_all_ok_and_installs_nothing(
    home: Path,
    reviewer: FakeReviewer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[Reviewer] = []
    monkeypatch.setattr(dr_setup, "install_from_script", lambda r: calls.append(r))

    assert main(["setup"]) == 0

    out = capsys.readouterr().out
    assert "reviewer  ok" in out
    assert "key       ok" in out
    assert "skill     ok" in out
    assert calls == []


def test_yes_installs_the_missing_reviewer_from_its_script(
    home: Path, installer: list[Reviewer], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "--yes"]) == 0

    assert installer == [OPENCODE]
    assert "reviewer  installed" in capsys.readouterr().out


def test_without_yes_the_install_is_asked_first_and_no_means_no(
    home: Path,
    installer: list[Reviewer],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(dr_setup, "ask", lambda prompt: asked.append(prompt) or False)

    assert main(["setup"]) == 2

    assert installer == []
    assert len(asked) == 1 and "opencode.ai/install" in asked[0], (
        "the command is shown before it runs"
    )
    assert "reviewer  missing" in capsys.readouterr().out


def test_pi_is_not_installed_by_us_because_it_needs_node(
    home: Path, installer: list[Reviewer], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "--agent", "pi", "--yes"]) == 2

    assert installer == []
    assert "npm install -g @earendil-works/pi-coding-agent" in capsys.readouterr().out


@pytest.fixture
def no_key(home: Path, reviewer: FakeReviewer, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    No key anywhere: not in the environment, not stored. Depends on the Reviewer stand-in so it
    runs after the one that exports a test key. Returns where a stored one would go.
    """
    for name in ("ZHIPU_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home / ".config" / "deep-review" / "zai-api-key"


def test_a_missing_key_is_asked_for_hidden_and_stored_for_this_user_only(
    no_key: Path,
    reviewer: FakeReviewer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dr_setup, "terminal_present", lambda: True)
    monkeypatch.setattr(dr_setup, "ask_hidden", lambda prompt: " sk-secret \n")

    assert main(["setup"]) == 0

    out = capsys.readouterr().out
    assert "key       stored" in out and "sk-secret" not in out
    assert no_key.read_text(encoding="utf-8") == "sk-secret\n"
    assert no_key.stat().st_mode & 0o777 == 0o600
    assert no_key.parent.stat().st_mode & 0o777 == 0o700
    assert main(["setup"]) == 0, "and the stored key satisfies the next run"
    assert "key       ok        stored in" in capsys.readouterr().out


def test_an_empty_answer_or_no_terminal_leaves_the_key_missing(
    no_key: Path,
    reviewer: FakeReviewer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dr_setup, "terminal_present", lambda: True)
    monkeypatch.setattr(dr_setup, "ask_hidden", lambda prompt: "")
    assert main(["setup"]) == 2
    assert "key       missing" in capsys.readouterr().out

    def closed(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(dr_setup.getpass, "getpass", closed)
    monkeypatch.setattr(dr_setup, "ask_hidden", dr_setup.ask_hidden)
    assert main(["setup"]) == 2, "Ctrl-D at the prompt is a skip, not a traceback"
    capsys.readouterr()

    monkeypatch.setattr(dr_setup, "terminal_present", lambda: False)
    monkeypatch.setattr(
        dr_setup, "ask_hidden", lambda prompt: pytest.fail("no terminal, no prompt")
    )
    assert main(["setup"]) == 2
    out = capsys.readouterr().out
    assert "key       missing" in out and "Z_AI_API_KEY" in out
    assert not no_key.exists()


def test_the_stored_key_reaches_the_reviewer_unless_the_shell_overrides_it(
    no_key: Path, reviewer: FakeReviewer, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_key("from-file")
    assert api_key(OPENCODE) == "from-file"
    monkeypatch.setenv("Z_AI_API_KEY", "from-shell")
    assert api_key(OPENCODE) == "from-shell"


def test_the_skill_is_linked_only_for_the_agents_that_are_present(
    home: Path,
    reviewer: FakeReviewer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The stand-in opencode and git alone: the developer's own pi must not be found.
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")

    assert main(["setup"]) == 0

    assert (home / ".claude/skills/pre-pr-review").is_symlink(), "opencode reads ~/.claude/skills"
    assert not (home / ".pi").exists(), "no pi on PATH, nothing to link for it"
    assert "skill     ok" in capsys.readouterr().out


def test_pi_present_gets_its_own_link(
    home: Path, pi_reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "--agent", "pi"]) == 0

    assert (home / ".pi/agent/skills/pre-pr-review").is_symlink()


def test_a_foreign_skill_directory_is_reported_not_clobbered(
    home: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    theirs = home / ".claude/skills/pre-pr-review"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("theirs\n", encoding="utf-8")

    assert main(["setup"]) == 2

    assert "skill     missing" in capsys.readouterr().out
    assert not theirs.is_symlink()


def test_a_reviewer_installed_outside_path_is_still_found_and_linked_for(
    home: Path,
    no_reviewer: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def into_home(reviewer: Reviewer) -> None:
        target = home / ".opencode" / "bin" / reviewer.binary
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\n", encoding="utf-8")
        target.chmod(0o755)

    monkeypatch.setattr(dr_setup, "install_from_script", into_home)

    assert main(["setup", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "reviewer  installed opencode 1.18.31; open a new shell" in out
    assert (home / ".claude/skills/pre-pr-review").is_symlink()
