"""One real Run per Reviewer against a planted bug. It spends Z.AI credits, so it is opt in."""

import os
import shutil
from pathlib import Path

import pytest

from conftest import commit, git, write
from deep_review.cli import main
from deep_review.report import Report
from deep_review.reviewer import REVIEWERS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("Z_AI_API_KEY"), reason="no Z_AI_API_KEY in the env"),
]

# One Run per selectable Reviewer, each skipped where its own binary is not installed.
SELECTABLE = [
    pytest.param(
        name,
        marks=pytest.mark.skipif(
            shutil.which(reviewer.binary) is None, reason=f"{reviewer.binary} is not installed"
        ),
    )
    for name, reviewer in REVIEWERS.items()
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


@pytest.mark.parametrize("agent", SELECTABLE)
def test_a_real_run_produces_a_valid_report(
    agent: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "stats.py", GUARDED)
    commit(repo, "feat: average a list")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    git(repo, "checkout", "-q", "-b", "feat/drop-the-guard")
    write(repo, "stats.py", UNGUARDED)
    commit(repo, "refactor: drop the empty-list guard")
    monkeypatch.chdir(repo)

    assert main(["review", "--agent", agent, "--timeout", "6"]) == 0

    report = Report.model_validate_json(
        (repo / ".deep-review" / "findings.json").read_text(encoding="utf-8")
    )
    assert report.status == "ok", report.notice
    assert report.stats.seconds is not None
    assert report.stats.agent == agent
    # A Run whose stream went unread would report a Report and no numbers, which is the one way
    # this can pass while the Reviewer's own parser is reading the wrong shape.
    assert report.stats.input_tokens > 0, "the Run reported no tokens"
    with capsys.disabled():
        print(f"\n{agent}: {report.stats.seconds:g}s, {len(report.findings)} findings")
