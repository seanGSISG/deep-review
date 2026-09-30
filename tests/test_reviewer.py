"""Invoking the Reviewer: the argv, the hermetic home, the time cap, and the preflight."""

import json
import os
import time
from pathlib import Path

import pytest

from conftest import FakeReviewer
from deep_review import UsageError
from deep_review.report import FINDINGS_SCHEMA
from deep_review.reviewer import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    EVENTS_NAME,
    FINDINGS_NAME,
    SCHEMA_NAME,
    STDERR_NAME,
    invoke,
    preflight,
    reviewer_home,
)

PROMPT = "Review the diff.\n\nAnswer with the Report.\n"


def run(repo: Path, timeout_seconds: float = 30.0, effort: str = DEFAULT_EFFORT):
    """Invoke the Reviewer against a checkout whose Run directory already exists."""
    run_dir = repo / ".deep-review"
    run_dir.mkdir(exist_ok=True)
    return invoke(
        repo=repo,
        run_dir=run_dir,
        prompt=PROMPT,
        model=DEFAULT_MODEL,
        effort=effort,
        timeout_seconds=timeout_seconds,
    )


def _option(argv: list[str], flag: str) -> list[str]:
    """Every value given for a repeatable flag, in order."""
    return [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == flag]


def test_the_prompt_is_one_argument_and_stdin_is_dev_null(
    repo: Path, reviewer: FakeReviewer
) -> None:
    outcome = run(repo)

    assert outcome.exit_code == 0
    assert not outcome.timed_out
    assert reviewer.argv[0] == "exec"
    assert reviewer.argv[-1] == PROMPT
    assert reviewer.stdin == "/dev/null"
    assert reviewer.cwd == repo


def test_the_report_comes_back_through_the_schema_and_the_last_message(
    repo: Path, reviewer: FakeReviewer
) -> None:
    run(repo)

    run_dir = repo / ".deep-review"
    assert _option(reviewer.argv, "--output-last-message") == [str(run_dir / FINDINGS_NAME)]
    assert _option(reviewer.argv, "--output-schema") == [str(run_dir / SCHEMA_NAME)]
    schema = json.loads((run_dir / SCHEMA_NAME).read_text(encoding="utf-8"))
    assert schema == FINDINGS_SCHEMA


def test_the_findings_schema_is_in_the_strict_subset() -> None:
    """Strict Structured Outputs rejects a schema with an optional field or an open object."""

    def check(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert sorted(node["required"]) == sorted(node["properties"])
            for child in node.values():
                check(child)

    check(FINDINGS_SCHEMA)


def test_the_context_codex_would_load_on_its_own_is_turned_off(
    repo: Path, reviewer: FakeReviewer
) -> None:
    run(repo)

    argv = reviewer.argv
    for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv
    configs = _option(argv, "--config")
    assert "project_doc_max_bytes=0" in configs
    assert "skills.include_instructions=false" in configs
    assert "features.hooks=false" in configs


def test_commands_run_sandboxed_with_the_user_cache_writable(
    repo: Path, reviewer: FakeReviewer
) -> None:
    run(repo)

    assert _option(reviewer.argv, "--sandbox") == ["workspace-write"]
    roots = [c for c in _option(reviewer.argv, "--config") if c.startswith("sandbox_workspace")]
    cache = Path(os.environ["XDG_CACHE_HOME"])
    assert roots == [f"sandbox_workspace_write.writable_roots={json.dumps([str(cache)])}"]


def test_the_model_and_effort_reach_codex(repo: Path, reviewer: FakeReviewer) -> None:
    run(repo, effort="high")

    assert _option(reviewer.argv, "--model") == [DEFAULT_MODEL]
    assert 'model_reasoning_effort="high"' in _option(reviewer.argv, "--config")


def test_the_reviewer_runs_in_a_fresh_home_holding_only_the_login(
    repo: Path, reviewer: FakeReviewer
) -> None:
    run(repo)

    home = Path(reviewer.env["CODEX_HOME"])
    assert home != Path(os.environ["CODEX_HOME"])
    assert reviewer.home_entries == ["auth.json"]
    # A link, so a token refresh inside a Run lands in the developer's own file.
    assert reviewer.auth_link == Path(os.environ["CODEX_HOME"]) / "auth.json"
    assert not home.exists(), "a Run's home must not outlive it"


def test_each_run_gets_its_own_home(repo: Path, reviewer: FakeReviewer) -> None:
    run(repo)
    first = reviewer.env["CODEX_HOME"]
    run(repo)

    assert reviewer.env["CODEX_HOME"] != first


def test_homes_set_up_side_by_side_do_not_collide(reviewer: FakeReviewer) -> None:
    with reviewer_home() as one, reviewer_home() as two:
        assert one != two
        assert (one / "auth.json").readlink() == (two / "auth.json").readlink()


def test_a_relative_codex_home_still_links_to_the_real_login(
    reviewer: FakeReviewer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", "codex-home")

    with reviewer_home() as home:
        assert (home / "auth.json").resolve() == tmp_path / "codex-home" / "auth.json"
        assert (home / "auth.json").is_file()


def test_the_developers_own_codex_variables_do_not_reach_it(
    repo: Path, reviewer: FakeReviewer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_API_KEY", "sk-metered")

    run(repo)

    assert "CODEX_API_KEY" not in reviewer.env
    assert [name for name in reviewer.env if name.startswith("CODEX_")] == ["CODEX_HOME"]


def test_stdout_and_stderr_land_beside_the_runs_inputs(repo: Path, reviewer: FakeReviewer) -> None:
    run(repo)

    run_dir = repo / ".deep-review"
    assert "turn.completed" in (run_dir / EVENTS_NAME).read_text(encoding="utf-8")
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


def test_a_missing_codex_is_a_usage_error_with_the_install_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(UsageError, match="npm install -g @openai/codex"):
        preflight()


def test_an_old_codex_is_a_usage_error_naming_both_versions(reviewer: FakeReviewer) -> None:
    reviewer.will_report_version("codex-cli 0.156.1")

    with pytest.raises(UsageError, match=r"0\.156\.1 is older than 0\.159\.2"):
        preflight()


def test_no_login_file_is_a_usage_error(
    reviewer: FakeReviewer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "never-logged-in"))

    with pytest.raises(UsageError, match="codex login"):
        preflight()
    assert not reviewer.ran


def test_a_login_codex_rejects_is_a_usage_error(reviewer: FakeReviewer) -> None:
    reviewer.will_fail_login()

    with pytest.raises(UsageError, match="login status` exited 1"):
        preflight()


def test_an_api_key_login_is_a_usage_error(reviewer: FakeReviewer) -> None:
    reviewer.will_log_in_with("Logged in using an API key - sk-proj-***")

    with pytest.raises(UsageError, match="not on a ChatGPT login"):
        preflight()


def test_a_ready_machine_passes_preflight(reviewer: FakeReviewer) -> None:
    preflight()


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
