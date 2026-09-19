"""The `deep-review review` command in Local mode: the Report, the table and the exit codes."""

import json
import shutil
from pathlib import Path

import pytest

from conftest import FakeReviewer, commit, git, write
from deep_review.cli import main

OFF_BY_ONE: dict[str, object] = {
    "file": "retries.py",
    "line": 1,
    "end_line": 2,
    "severity": "P1",
    "title": "Retry count is off by one",
    "evidence": "MAX_RETRIES = 3",
    "failure_scenario": "Given MAX_RETRIES=3, only two attempts are made.",
}

NO_TEST: dict[str, object] = {
    "file": "app.py",
    "line": 7,
    "severity": "P2",
    "title": "The new branch has no test",
    "evidence": "return order.id or 0",
    "failure_scenario": "Given order.id=0, the fallback is never exercised.",
}

FINDINGS: dict[str, object] = {
    "summary": "Adds retry handling. Two data-loss paths on the main flow.",
    "score": 2,
    "verified_by_execution": ["pytest -q: 1 failed"],
    "findings": [OFF_BY_ONE, NO_TEST],
}


@pytest.fixture
def branch(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout on a feature branch with one uncommitted change, ready to review."""
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    git(repo, "checkout", "-q", "-b", "feat/retries")
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    monkeypatch.chdir(repo)
    return repo


def only_git(tmp: Path) -> Path:
    """A PATH holding git and nothing else, so the checkout still works but the Reviewer is gone."""
    bin_dir = tmp / "only-git"
    bin_dir.mkdir()
    git_binary = shutil.which("git")
    assert git_binary is not None
    (bin_dir / "git").symlink_to(git_binary)
    return bin_dir


def report_on_disk(repo: Path) -> dict:
    """The Report the Run left in the checkout."""
    return json.loads((repo / ".deep-review" / "findings.json").read_text(encoding="utf-8"))


def test_a_complete_run_prints_the_findings_the_summary_and_what_it_cost(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_write(FINDINGS)

    assert main(["review"]) == 0

    out = capsys.readouterr().out
    assert "P1  retries.py:1-2  Retry count is off by one" in out
    assert "P2  app.py:7" in out
    assert "Two data-loss paths on the main flow." in out
    assert "findings  2 (P1 1, P2 1)" in out
    assert "score     2/5 (advisory)" in out
    assert "reviewer  opencode zai-coding-plan/glm-5.3 in " in out
    assert "tokens    1,200 fresh, 9,000 cached, 80 output, 40 reasoning" in out
    assert "tools     bash 1" in out
    assert "report    .deep-review/findings.json" in out
    assert report_on_disk(branch)["status"] == "ok"
    assert report_on_disk(branch)["stats"]["cache_read_tokens"] == 9000
    assert report_on_disk(branch)["stats"]["tool_calls"] == {"bash": 1}


def test_the_reviewer_reads_the_runs_inputs_in_the_checkout(
    branch: Path, reviewer: FakeReviewer
) -> None:
    reviewer.will_write(FINDINGS)

    main(["review"])

    assert reviewer.cwd == branch
    assert "findings.json" in reviewer.argv[-1]  # the prompt, as one argument
    assert (branch / ".deep-review" / "diff.patch").read_text(encoding="utf-8")


def test_json_prints_the_whole_report_and_nothing_else(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_write(FINDINGS)

    assert main(["review", "--json"]) == 0

    out = capsys.readouterr().out
    assert json.loads(out) == report_on_disk(branch)
    assert out == (branch / ".deep-review" / "findings.json").read_text(encoding="utf-8")


def test_fail_on_exits_1_only_for_a_matching_severity(branch: Path, reviewer: FakeReviewer) -> None:
    reviewer.will_write(FINDINGS)  # one P1 and one P2

    assert main(["review", "--fail-on", "P1"]) == 1
    assert main(["review", "--fail-on", "P0"]) == 0
    assert main(["review", "--fail-on", "P2"]) == 1
    assert main(["review"]) == 0


def test_a_malformed_finding_is_dropped_and_the_report_survives(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_write({**FINDINGS, "findings": [{**OFF_BY_ONE, "severity": "blocker"}, NO_TEST]})

    assert main(["review", "--fail-on", "P1"]) == 0  # the P1 was the dropped one

    out = capsys.readouterr().out
    assert "notice    dropped finding 1 (retries.py:1): severity" in out
    assert "findings  1 (P2 1)" in out
    assert report_on_disk(branch)["status"] == "ok"


def test_a_reviewer_that_crashes_fails_the_report_but_not_the_cli(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_exit(1)  # and writes no findings file

    assert main(["review", "--fail-on", "P0"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("failed: the Reviewer wrote no findings.json")
    assert "the Reviewer exited 1; see agent.err" in out
    assert "findings  none" in out
    assert report_on_disk(branch)["findings"] == []


def test_a_reviewer_that_times_out_fails_the_report_but_not_the_cli(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_hang()

    assert main(["review", "--timeout", "0.01"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("failed:")
    assert "killed on the 0.01 minute timeout" in out
    assert report_on_disk(branch)["status"] == "failed"


def test_a_reviewer_killed_after_writing_keeps_its_findings_but_fails_the_run(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewer.will_write(FINDINGS)
    reviewer.will_hang()

    assert main(["review", "--timeout", "0.01"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("failed: the Reviewer was killed on the 0.01 minute timeout")
    assert "findings  2 (P1 1, P2 1)" in out
    assert report_on_disk(branch)["status"] == "failed"
    assert len(report_on_disk(branch)["findings"]) == 2


def test_the_previous_runs_report_does_not_survive_a_failed_run(
    branch: Path, reviewer: FakeReviewer
) -> None:
    reviewer.will_write(FINDINGS)
    main(["review"])
    assert report_on_disk(branch)["findings"]

    (reviewer.record / "findings").unlink()
    reviewer.will_exit(1)
    main(["review"])

    assert report_on_disk(branch)["findings"] == []


def test_an_empty_diff_takes_the_previous_runs_report_with_it(
    branch: Path, reviewer: FakeReviewer
) -> None:
    reviewer.will_write(FINDINGS)
    main(["review"])
    commit(branch, "feat: cap retries")  # the Diff the Report described is now the Base

    assert main(["review", "--base", "HEAD"]) == 0

    assert not (branch / ".deep-review" / "findings.json").exists()


def test_a_diff_over_the_size_gate_skips_the_reviewer(
    branch: Path, reviewer: FakeReviewer, capsys: pytest.CaptureFixture[str]
) -> None:
    write(branch, "big.py", "".join(f"LINE_{index} = {index}\n" for index in range(40)))

    assert main(["review", "--max-diff-lines", "20"]) == 0

    out = capsys.readouterr().out
    assert "skipped: the Diff changes 41 lines, over the --max-diff-lines limit of 20" in out
    assert "the Reviewer did not run" in out
    assert "tokens" not in out  # a Reviewer that never ran spent nothing to say so
    assert not reviewer.ran
    assert report_on_disk(branch)["status"] == "skipped"


def test_review_of_an_unchanged_checkout_is_skipped(
    repo: Path,
    reviewer: FakeReviewer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    monkeypatch.chdir(repo)

    assert main(["review"]) == 0

    assert "skipped: nothing changed against origin/main" in capsys.readouterr().out
    assert not reviewer.ran
    assert not (repo / ".deep-review").exists()


def test_review_without_a_resolvable_base_exits_2(
    repo: Path,
    reviewer: FakeReviewer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repo, "branch", "-m", "main", "trunk")
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    monkeypatch.chdir(repo)

    assert main(["review"]) == 2

    assert "--base" in capsys.readouterr().err
    assert not reviewer.ran
    assert ".deep-review" not in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")


def test_review_takes_an_explicit_base(branch: Path, reviewer: FakeReviewer) -> None:
    start = git(branch, "rev-parse", "HEAD")
    write(branch, "app.py", "def ship(order):\n    return order.id\n")
    commit(branch, "feat: return the id")
    reviewer.will_write(FINDINGS)

    assert main(["review", "--base", start]) == 0

    written = (branch / ".deep-review" / "diff.patch").read_text(encoding="utf-8")
    assert "return order.id" in written


def test_the_model_and_variant_reach_the_reviewer(branch: Path, reviewer: FakeReviewer) -> None:
    reviewer.will_write(FINDINGS)

    main(["review", "--model", "glm-5.3-flash", "--variant", "high"])

    assert reviewer.argv[-4:-1] == ["zai-coding-plan/glm-5.3-flash", "--variant", "high"]


def test_a_missing_reviewer_binary_exits_2_before_the_run(
    branch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(only_git(branch)))

    assert main(["review"]) == 2

    assert "install it with `npm install -g opencode-ai" in capsys.readouterr().err
    assert not (branch / ".deep-review").exists()


def test_an_unknown_reviewer_is_a_usage_error(branch: Path, reviewer: FakeReviewer) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["review", "--agent", "claude"])

    assert exit_.value.code == 2


def test_review_outside_a_git_checkout_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["review"]) == 2

    assert "git" in capsys.readouterr().err
