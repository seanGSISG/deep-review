"""Git plumbing for a Local mode Run: the Base, the Diff, and keeping the Run out of git."""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from deep_review import UsageError

# The Run directory, written inside the checkout under review.
RUN_DIR_NAME = ".deep-review"

# Tried in order when --base is not given.
BASE_CANDIDATES = ("origin/main", "main", "master")


@dataclass(frozen=True, slots=True)
class Diff:
    """The change under review: the text the Reviewer reads, and the size the Size gate measures."""

    text: str
    changed_lines: int


@dataclass(frozen=True, slots=True)
class Base:
    """The commit the Diff is measured from, and the ref it was resolved through."""

    sha: str
    ref: str


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """
    Run a git command in the checkout and return its stdout verbatim. Output is decoded leniently
    because a Diff carries whatever encoding the source files use, and one latin-1 file in the
    change must not crash the Run.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.stdout


def try_git(repo: Path, *args: str) -> str | None:
    """Run a git command that is allowed to fail, returning its stripped stdout or None."""
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, errors="replace"
    )
    return result.stdout.strip() if result.returncode == 0 else None


def repo_root(start: Path) -> Path:
    """The top level of the checkout containing `start`, or a usage error outside one."""
    root = try_git(start, "rev-parse", "--show-toplevel")
    if root is None:
        raise UsageError(f"{start} is not inside a git checkout")
    return Path(root)


def resolve_base(repo: Path, ref: str | None) -> Base:
    """
    The Base for this Run: the merge-base of HEAD with `ref`, or with the first of origin/main,
    main and master that exists. With none of them the caller has to say, so this is a usage error.
    """
    if ref is not None:
        sha = try_git(repo, "merge-base", "HEAD", ref)
        if sha is None:
            raise UsageError(
                f"cannot measure from {ref!r}: no such ref, or no history shared with HEAD"
            )
        return Base(sha=sha, ref=ref)
    for candidate in BASE_CANDIDATES:
        sha = try_git(repo, "merge-base", "HEAD", candidate)
        if sha is not None:
            return Base(sha=sha, ref=candidate)
    raise UsageError(
        "no origin/main, main or master to measure from; pass --base REF to say what to diff from"
    )


def build_diff(repo: Path, base_sha: str) -> Diff:
    """
    The Diff: the Base against the working tree, so committed, uncommitted and untracked work are
    all reviewed. Its size comes from --numstat rather than counting the diff's own +/- lines,
    which a removed line reading "--" would throw off.
    """
    with _scratch_index(repo) as env:
        return Diff(
            text=run_git(repo, "diff", base_sha, env=env),
            changed_lines=_changed_lines(run_git(repo, "diff", "--numstat", base_sha, env=env)),
        )


@contextmanager
def _scratch_index(repo: Path) -> Iterator[dict[str, str]]:
    """
    An environment pointing git at a throwaway copy of the index with untracked files added as
    intent-to-add, so new files are part of the Diff and the developer's staging area is untouched.
    """
    with tempfile.TemporaryDirectory(prefix="deep-review-") as scratch:
        index = Path(scratch) / "index"
        real_index = _git_path(repo, "index")
        if real_index.exists():
            shutil.copy(real_index, index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        run_git(repo, "add", "--intent-to-add", "--all", env=env)
        yield env


def _changed_lines(numstat: str) -> int:
    """
    Added plus removed lines across the Diff, which is what the Size gate measures. A binary file's
    counts are "-" and add nothing: the gate is about how much code the Reviewer has to read.
    """
    total = 0
    for row in numstat.splitlines():
        added, _, rest = row.partition("\t")
        removed, _, _ = rest.partition("\t")
        total += sum(int(count) for count in (added, removed) if count.isdigit())
    return total


def exclude_run_dir(repo: Path) -> None:
    """
    Keep the Run directory out of git through .git/info/exclude, which is the developer's own file
    rather than the repo's: .gitignore is never touched.
    """
    exclude = _git_path(repo, "info/exclude")
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    line = f"{RUN_DIR_NAME}/"
    if line in existing.splitlines():
        return
    separator = "" if existing == "" or existing.endswith("\n") else "\n"
    exclude.write_text(f"{existing}{separator}{line}\n", encoding="utf-8")


def current_branch(repo: Path) -> str:
    """The checked-out branch, or a note naming the commit when HEAD is detached."""
    branch = try_git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    return branch or f"detached at {run_git(repo, 'rev-parse', '--short', 'HEAD').strip()}"


def commit_log(repo: Path, base_sha: str) -> str:
    """The commit messages from the Base to HEAD, oldest first, as markdown sections."""
    return run_git(repo, "log", "--reverse", f"{base_sha}..HEAD", "--format=### %h %s%n%n%b")


def is_dirty(repo: Path) -> bool:
    """True when the working tree holds uncommitted or untracked work."""
    return bool(run_git(repo, "status", "--porcelain").strip())


def _git_path(repo: Path, relative: str) -> Path:
    """
    Resolve a path inside this checkout's git directory. git knows which of them live in the
    common directory, so info/exclude lands in the right place inside a linked worktree.
    """
    resolved = Path(run_git(repo, "rev-parse", "--git-path", relative).strip())
    return resolved if resolved.is_absolute() else repo / resolved
