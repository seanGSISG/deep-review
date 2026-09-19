"""Preparing a Local mode Run's inputs: everything the Reviewer reads before it starts."""

from dataclasses import dataclass
from pathlib import Path

from deep_review.git import (
    RUN_DIR_NAME,
    Base,
    commit_log,
    current_branch,
    exclude_run_dir,
    is_dirty,
)
from deep_review.resources import review_prompt

DIFF_NAME = "diff.patch"
DESCRIPTION_NAME = "pr.md"
PROMPT_NAME = "prompt.md"


@dataclass(frozen=True, slots=True)
class RunInputs:
    """Where a Run's inputs landed inside the checkout."""

    run_dir: Path
    diff_path: Path
    description_path: Path
    prompt_path: Path
    diff_lines: int


def local_description(repo: Path, base: Base) -> str:
    """
    The PR-equivalent description for a Local Run. There is no pull request to read a title and
    body from, so the branch name and the commit messages since the Base stand in for them.
    """
    scope = f"Local mode Run against {base.ref} ({base.sha[:8]})"
    if is_dirty(repo):
        scope += ", including uncommitted and untracked work in the checkout"
    log = commit_log(repo, base.sha).strip()
    return f"# {current_branch(repo)}\n\n{scope}.\n\n{log or 'No commits since the Base.'}\n"


def write_run_inputs(repo: Path, base: Base, diff: str, description: str) -> RunInputs:
    """
    Write the Run's inputs under .deep-review/: the patch, the description and the prompt. Creating
    the Run directory is also what keeps it out of git, so the two cannot drift apart.
    """
    run_dir = repo / RUN_DIR_NAME
    run_dir.mkdir(parents=True, exist_ok=True)
    exclude_run_dir(repo)
    inputs = RunInputs(
        run_dir=run_dir,
        diff_path=run_dir / DIFF_NAME,
        description_path=run_dir / DESCRIPTION_NAME,
        prompt_path=run_dir / PROMPT_NAME,
        diff_lines=diff.count("\n"),
    )
    inputs.diff_path.write_text(diff, encoding="utf-8")
    inputs.description_path.write_text(description, encoding="utf-8")
    inputs.prompt_path.write_text(review_prompt(base.sha), encoding="utf-8")
    return inputs
