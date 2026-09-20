"""Invoking the Reviewer: the argv, the stdin, the time cap, and the preflight."""

import os
import time
from pathlib import Path

import pytest

from conftest import FakeReviewer
from deep_review import UsageError
from deep_review.reviewer import (
    EVENTS_NAME,
    OPENCODE,
    PI,
    STDERR_NAME,
    Reviewer,
    invoke,
    preflight,
    resolve_model,
    reviewer_home,
)

PROMPT = "Review the diff.\n\nWrite .deep-review/findings.json.\n"


def run(
    repo: Path,
    selected: Reviewer = OPENCODE,
    timeout_seconds: float = 30.0,
    variant: str | None = None,
):
    """Invoke the Reviewer against a checkout whose Run directory already exists."""
    run_dir = repo / ".deep-review"
    run_dir.mkdir(exist_ok=True)
    return invoke(
        selected,
        repo=repo,
        run_dir=run_dir,
        prompt=PROMPT,
        model=selected.default_model,
        variant=variant,
        timeout_seconds=timeout_seconds,
    )


def test_the_prompt_is_one_argument_and_stdin_is_dev_null(
    repo: Path, reviewer: FakeReviewer
) -> None:
    outcome = run(repo)

    assert outcome.exit_code == 0
    assert not outcome.timed_out
    assert reviewer.argv[-1] == PROMPT
    assert reviewer.argv[:-1] == [
        "run",
        "--format",
        "json",
        "--pure",
        "--dangerously-skip-permissions",
        "-m",
        "zai-coding-plan/glm-5.3",
    ]
    assert reviewer.stdin == "/dev/null"
    assert reviewer.cwd == repo


def test_pi_sees_only_the_review_prompt_too(repo: Path, pi_reviewer: FakeReviewer) -> None:
    outcome = run(repo, PI, variant="medium")

    assert outcome.exit_code == 0
    assert pi_reviewer.argv[-1] == PROMPT
    assert pi_reviewer.argv[:-1] == [
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--no-approve",
        "--model",
        "zai/glm-5.3",
        "--thinking",
        "medium",
    ]
    assert pi_reviewer.stdin == "/dev/null"


def test_the_reviewer_reads_its_settings_from_a_directory_of_ours(
    repo: Path, reviewer: FakeReviewer, tmp_path: Path
) -> None:
    run(repo)

    home = Path(reviewer.env["XDG_CONFIG_HOME"])
    assert home == reviewer_home(OPENCODE)
    assert home.is_relative_to(tmp_path), "the Run read the developer's own opencode config"
    # Not a claim that it stays empty — the CLIs fill it with their own state, which is the point
    # of giving them one. The claim is that a Run puts nothing in it: a Run that started seeding
    # this directory would be handing the Reviewer context again, from a new direction.
    assert list(home.glob("*")) == [], "a Run seeded the Reviewer's settings directory"
    # What a Reviewer does need: a key, and enough of a shell for its bash tool to be worth having.
    assert reviewer.env["ZHIPU_API_KEY"] == "test-key"
    assert "PATH" in reviewer.env


def test_the_context_opencode_would_load_on_its_own_is_turned_off(
    repo: Path, reviewer: FakeReviewer
) -> None:
    run(repo)

    # The project walk from cwd to the worktree root, and with it the .opencode directories whose
    # presence starts an npm install inside the checkout under review.
    assert reviewer.env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    # CLAUDE.md, from the checkout and from ~/.claude, and the skills under ~/.claude/skills and
    # .claude/skills - one of which is the Skill that asks for a Run in the first place.
    assert reviewer.env["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"


def test_the_developers_own_variables_for_the_cli_do_not_reach_it(
    repo: Path, reviewer: FakeReviewer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A whole opencode config, inline, exported by the shell the CLI was started from.
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"instructions": ["AGENTS.md"]}')
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", "/home/dev/.config/opencode")
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE", "0")

    run(repo)

    assert "OPENCODE_CONFIG_CONTENT" not in reviewer.env
    assert "OPENCODE_CONFIG_DIR" not in reviewer.env
    assert reviewer.env["OPENCODE_DISABLE_CLAUDE_CODE"] == "1", "the shell overrode a Run's flag"


def test_pi_reads_its_settings_from_a_directory_of_ours_too(
    repo: Path, pi_reviewer: FakeReviewer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))

    run(repo, PI, variant="medium")

    # pi's settings, packages, extensions, skills, prompts and project trust all resolve from here.
    home = Path(pi_reviewer.env["PI_CODING_AGENT_DIR"])
    assert home == reviewer_home(PI)
    assert home != reviewer_home(OPENCODE), "the two Reviewers shared one settings directory"
    assert home.is_relative_to(tmp_path), "the Run read the developer's own pi agent directory"
    assert list(home.glob("*")) == [], "a Run seeded the Reviewer's settings directory"
    assert pi_reviewer.env["ZAI_API_KEY"] == "test-key"


def test_a_variant_reaches_the_reviewers_own_flag(repo: Path, reviewer: FakeReviewer) -> None:
    run(repo, variant="high")

    assert reviewer.argv[-3:-1] == ["--variant", "high"]


def test_stdout_and_stderr_land_beside_the_runs_inputs(repo: Path, reviewer: FakeReviewer) -> None:
    run(repo)

    run_dir = repo / ".deep-review"
    assert "step-finish" in (run_dir / EVENTS_NAME).read_text(encoding="utf-8")
    assert (run_dir / STDERR_NAME).exists()


def test_a_reviewer_that_fails_reports_its_exit_code(repo: Path, reviewer: FakeReviewer) -> None:
    reviewer.will_exit(3)

    outcome = run(repo)

    assert outcome.exit_code == 3
    assert not outcome.timed_out


def test_the_time_cap_kills_the_reviewer_and_its_children(
    repo: Path, reviewer: FakeReviewer
) -> None:
    reviewer.will_hang()

    outcome = run(repo, timeout_seconds=0.5)

    assert outcome.timed_out
    assert outcome.exit_code is None
    assert outcome.seconds < 30
    assert _is_gone(reviewer.child), "the Reviewer's own child outlived the time cap"


def test_a_missing_reviewer_binary_is_a_usage_error_with_its_own_install_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(UsageError, match="npm install -g opencode-ai"):
        preflight(OPENCODE)
    with pytest.raises(UsageError, match="npm install -g @earendil-works/pi-coding-agent"):
        preflight(PI)


def test_a_missing_key_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, reviewer: FakeReviewer
) -> None:
    monkeypatch.delenv("ZHIPU_API_KEY")
    monkeypatch.delenv("Z_AI_API_KEY", raising=False)

    with pytest.raises(UsageError, match="Z_AI_API_KEY"):
        preflight(OPENCODE)

    monkeypatch.setenv("Z_AI_API_KEY", "fallback")
    preflight(OPENCODE)


def test_a_missing_key_is_a_usage_error_for_pi_too(
    monkeypatch: pytest.MonkeyPatch, pi_reviewer: FakeReviewer
) -> None:
    monkeypatch.delenv("ZAI_API_KEY")
    monkeypatch.delenv("Z_AI_API_KEY", raising=False)

    with pytest.raises(UsageError, match="set ZAI_API_KEY or Z_AI_API_KEY"):
        preflight(PI)


def test_a_bare_model_gains_the_selected_reviewers_provider_prefix() -> None:
    assert resolve_model(OPENCODE, None) == "zai-coding-plan/glm-5.3"
    assert resolve_model(OPENCODE, "glm-5.3-flash") == "zai-coding-plan/glm-5.3-flash"
    assert resolve_model(OPENCODE, "openrouter/z-ai/glm-5.3") == "openrouter/z-ai/glm-5.3"
    assert resolve_model(PI, None) == "zai/glm-5.3"
    assert resolve_model(PI, "glm-5.3-flash") == "zai/glm-5.3-flash"
    assert resolve_model(PI, "openrouter/z-ai/glm-5.3") == "openrouter/z-ai/glm-5.3"


def _is_gone(pid: int) -> bool:
    """True once the process is neither running nor waiting to be reaped."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False
