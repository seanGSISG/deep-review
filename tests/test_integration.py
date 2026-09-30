"""One real Run against a planted bug. It spends the Codex login's usage, so it is opt in."""

import shutil
from pathlib import Path

import pytest

from conftest import commit, git, write
from deep_review.cli import main
from deep_review.report import Report
from deep_review.reviewer import BINARY, login_problem

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which(BINARY) is None, reason=f"{BINARY} is not installed"),
]

GUARDED = """def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
"""

# The planted bug: the guard is gone, so the main path divides by zero.
UNGUARDED = """def average(values: list[float]) -> float:
    return sum(values) / len(values)
"""


def test_a_real_run_produces_a_valid_report(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if (problem := login_problem()) is not None:
        pytest.skip(problem)
    write(repo, "stats.py", GUARDED)
    commit(repo, "feat: average a list")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    git(repo, "checkout", "-q", "-b", "feat/drop-the-guard")
    write(repo, "stats.py", UNGUARDED)
    commit(repo, "refactor: drop the empty-list guard")
    monkeypatch.chdir(repo)

    assert main(["review", "--timeout", "10"]) == 0

    report = Report.model_validate_json(
        (repo / ".deep-review" / "findings.json").read_text(encoding="utf-8")
    )
    assert report.status == "ok", report.notice
    assert report.stats.seconds is not None
    assert report.stats.agent == BINARY
    # A Run whose stream went unread would report a Report and no numbers, which is the one way
    # this can pass while the parser is reading the wrong shape.
    assert report.stats.input_tokens > 0, "the Run reported no tokens"
    assert any(found.file == "stats.py" for found in report.findings), report.summary
    with capsys.disabled():
        print(f"\ncodex: {report.stats.seconds:g}s, {len(report.findings)} findings")
