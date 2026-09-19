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
    STDERR_NAME,
    invoke,
    preflight,
    resolve_model,
)

PROMPT = "Review the diff.\n\nWrite .deep-review/findings.json.\n"


def run(repo: Path, timeout_seconds: float = 30.0, variant: str | None = None):
    """Invoke the Reviewer against a checkout whose Run directory already exists."""
    run_dir = repo / ".deep-review"
    run_dir.mkdir(exist_ok=True)
    return invoke(
        OPENCODE,
        repo=repo,
        run_dir=run_dir,
        prompt=PROMPT,
        model="zai-coding-plan/glm-5.3",
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


def test_a_missing_reviewer_binary_is_a_usage_error_with_an_install_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(UsageError, match="npm install -g opencode-ai"):
        preflight(OPENCODE)


def test_a_missing_key_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, reviewer: FakeReviewer
) -> None:
    monkeypatch.delenv("ZHIPU_API_KEY")
    monkeypatch.delenv("Z_AI_API_KEY", raising=False)

    with pytest.raises(UsageError, match="Z_AI_API_KEY"):
        preflight(OPENCODE)

    monkeypatch.setenv("Z_AI_API_KEY", "fallback")
    preflight(OPENCODE)


def test_a_bare_model_gains_the_providers_prefix() -> None:
    assert resolve_model(OPENCODE, None) == "zai-coding-plan/glm-5.3"
    assert resolve_model(OPENCODE, "glm-5.3-flash") == "zai-coding-plan/glm-5.3-flash"
    assert resolve_model(OPENCODE, "openrouter/z-ai/glm-5.3") == "openrouter/z-ai/glm-5.3"


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
