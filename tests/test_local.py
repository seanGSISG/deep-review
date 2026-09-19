"""Writing a Local mode Run's inputs under .deep-review/."""

from pathlib import Path

from conftest import commit, git, write
from deep_review.git import Base
from deep_review.local import local_description, write_run_inputs


def test_inputs_land_in_the_run_directory(repo: Path) -> None:
    base = Base(sha=git(repo, "rev-parse", "HEAD"), ref="origin/main")

    inputs = write_run_inputs(repo, base, "diff --git a/app.py b/app.py\n", "# main\n")

    assert inputs.diff_path == repo / ".deep-review" / "diff.patch"
    assert inputs.diff_path.read_text(encoding="utf-8") == "diff --git a/app.py b/app.py\n"
    assert inputs.description_path.read_text(encoding="utf-8") == "# main\n"
    assert inputs.patch_lines == 1


def test_the_prompt_has_the_base_sha_substituted(repo: Path) -> None:
    base = Base(sha="0123456789abcdef0123456789abcdef01234567", ref="main")

    inputs = write_run_inputs(repo, base, "diff\n", "# main\n")

    prompt = inputs.prompt_path.read_text(encoding="utf-8")
    assert base.sha in prompt
    assert "{{BASE_SHA}}" not in prompt


def test_the_description_carries_the_branch_and_commit_messages(repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat/retries")
    write(repo, "retries.py", "MAX_RETRIES = 3\n")
    commit(repo, "feat: cap retries\n\nThe loop used to run forever.")

    description = local_description(repo, Base(sha=start, ref="origin/main"))

    assert description.startswith("# feat/retries")
    assert "feat: cap retries" in description
    assert "The loop used to run forever." in description


def test_the_description_says_when_the_tree_is_dirty(repo: Path) -> None:
    base = Base(sha=git(repo, "rev-parse", "HEAD"), ref="main")
    assert "uncommitted" not in local_description(repo, base)

    write(repo, "app.py", "def ship(order):\n    return order.id\n")

    assert "uncommitted" in local_description(repo, base)
