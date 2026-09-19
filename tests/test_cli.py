"""The `deep-review review` command in Local mode."""

from pathlib import Path

import pytest

from conftest import commit, git, write
from deep_review.cli import main


def test_review_prepares_the_run_and_says_where_it_landed(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    start = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", start)
    git(repo, "checkout", "-q", "-b", "feat/retries")
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    monkeypatch.chdir(repo)

    assert main(["review"]) == 0

    out = capsys.readouterr().out
    assert ".deep-review/diff.patch" in out
    assert ".deep-review/pr.md" in out
    assert ".deep-review/prompt.md" in out
    assert start[:8] in out
    assert (repo / ".deep-review" / "diff.patch").read_text(encoding="utf-8")


def test_review_of_an_unchanged_checkout_is_skipped(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    monkeypatch.chdir(repo)

    assert main(["review"]) == 0

    assert "skipped" in capsys.readouterr().out
    assert not (repo / ".deep-review").exists()


def test_review_without_a_resolvable_base_exits_2(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "branch", "-m", "main", "trunk")
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    monkeypatch.chdir(repo)

    assert main(["review"]) == 2

    assert "--base" in capsys.readouterr().err


def test_review_takes_an_explicit_base(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    start = git(repo, "rev-parse", "HEAD")
    write(repo, "app.py", "def ship(order):\n    return order.id\n")
    commit(repo, "feat: return the id")
    monkeypatch.chdir(repo)

    assert main(["review", "--base", start]) == 0

    assert "return order.id" in (repo / ".deep-review" / "diff.patch").read_text(encoding="utf-8")


def test_review_excludes_the_run_directory_from_git(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    monkeypatch.chdir(repo)

    main(["review"])

    assert ".deep-review" not in git(repo, "status", "--porcelain")


def test_review_outside_a_git_checkout_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["review"]) == 2

    assert "git" in capsys.readouterr().err
