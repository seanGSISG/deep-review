"""Base resolution, Diff building and the exclude file."""

from pathlib import Path

import pytest

from conftest import commit, git, write
from deep_review import UsageError
from deep_review.git import build_diff, exclude_run_dir, resolve_base


def commit_on_branch(repo: Path) -> None:
    """A second commit on the current branch, so HEAD sits ahead of the base."""
    write(repo, "app.py", "def ship(order):\n    return order.id\n")
    commit(repo, "feat: return the id")


def branch_off(repo: Path, name: str) -> str:
    """Branch from HEAD and return the SHA the branch starts at."""
    start = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", name)
    return start


def test_base_is_the_merge_base_with_origin_main(repo: Path) -> None:
    start = branch_off(repo, "feat/retries")
    git(repo, "update-ref", "refs/remotes/origin/main", start)
    write(repo, "app.py", "def ship(order):\n    return order.id\n")
    commit(repo, "feat: return the id")

    base = resolve_base(repo, None)

    assert base.sha == start
    assert base.ref == "origin/main"


def test_base_falls_back_to_local_main_then_master(repo: Path) -> None:
    start = branch_off(repo, "feat/retries")
    commit_on_branch(repo)

    assert resolve_base(repo, None).ref == "main"

    git(repo, "branch", "-m", "main", "master")
    assert resolve_base(repo, None).ref == "master"
    assert resolve_base(repo, None).sha == start


def test_no_candidate_base_is_a_usage_error(repo: Path) -> None:
    git(repo, "branch", "-m", "main", "trunk")

    with pytest.raises(UsageError, match="--base"):
        resolve_base(repo, None)


def test_explicit_base_overrides_resolution(repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD")
    commit_on_branch(repo)

    base = resolve_base(repo, start)

    assert base.sha == start
    assert base.ref == start


def test_unknown_explicit_base_is_a_usage_error(repo: Path) -> None:
    with pytest.raises(UsageError, match="no-such-ref"):
        resolve_base(repo, "no-such-ref")


def test_a_base_sharing_no_history_says_so(repo: Path) -> None:
    git(repo, "checkout", "-q", "--orphan", "unrelated")
    write(repo, "other.py", "OTHER = True\n")
    commit(repo, "init: unrelated history")
    git(repo, "checkout", "-q", "main")

    with pytest.raises(UsageError, match="no history shared with HEAD"):
        resolve_base(repo, "unrelated")


def test_diff_covers_committed_uncommitted_and_untracked_work(repo: Path) -> None:
    start = branch_off(repo, "feat/retries")
    write(repo, "app.py", "def ship(order):\n    return order.id\n")
    commit(repo, "feat: return the id")
    write(repo, "app.py", "def ship(order):\n    return order.id or 0\n")
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    write(repo, "secrets/key.txt", "shhh\n")

    text = build_diff(repo, start).text

    assert "return order.id" in text  # committed since the base
    assert "return order.id or 0" in text  # uncommitted
    assert "MAX_RETRIES = 3" in text  # untracked, added as intent-to-add
    assert "secrets/key.txt" not in text  # gitignored


def test_diff_size_counts_added_plus_removed_lines(repo: Path) -> None:
    start = branch_off(repo, "feat/retries")
    write(repo, "app.py", "def ship(order):\n    return order.id\n")  # one line for one
    write(repo, "retries.py", "MAX_RETRIES = 3\nBACKOFF = 2\n")  # two added
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(64))  # binary, uncountable

    assert build_diff(repo, start).changed_lines == 4


def test_diff_leaves_the_real_index_alone(repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD")
    write(repo, "staged.py", "STAGED = True\n")
    git(repo, "add", "staged.py")
    write(repo, "loose.py", "LOOSE = True\n")
    before = git(repo, "status", "--porcelain")

    build_diff(repo, start)

    assert git(repo, "status", "--porcelain") == before


def test_diff_survives_a_file_that_is_not_utf_8(repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD")
    (repo / "latin.py").write_bytes(b"CAFE = 'caf\xe9'\n")

    assert "latin.py" in build_diff(repo, start).text


def test_diff_is_empty_when_nothing_changed(repo: Path) -> None:
    diff = build_diff(repo, git(repo, "rev-parse", "HEAD"))

    assert (diff.text, diff.changed_lines) == ("", 0)


def test_exclude_run_dir_appends_once_and_leaves_gitignore_alone(repo: Path) -> None:
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")

    exclude_run_dir(repo)
    exclude_run_dir(repo)

    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".deep-review/") == 1
    assert (repo / ".gitignore").read_text(encoding="utf-8") == gitignore


def test_exclude_run_dir_creates_the_info_directory(repo: Path) -> None:
    info = repo / ".git" / "info"
    for child in info.iterdir():
        child.unlink()
    info.rmdir()

    exclude_run_dir(repo)

    assert (info / "exclude").read_text(encoding="utf-8").splitlines() == [".deep-review/"]
