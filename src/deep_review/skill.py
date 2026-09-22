"""Putting the Skill where the Coding agents look for it."""

import shutil
from pathlib import Path

from deep_review import UsageError
from deep_review.resources import skill_dir

# Where each Coding agent reads skills from, relative to the home directory. Claude Code and
# opencode share ~/.claude/skills; pi has its own tree.
TARGETS: dict[str, Path] = {
    "claude": Path(".claude", "skills"),
    "pi": Path(".pi", "agent", "skills"),
}


def install(targets: list[str], force: bool) -> list[Path]:
    """
    Symlink the Skill into each target's skill directory and return the links made. A link that
    already points at the Skill is left alone. Anything else at the path is the developer's own
    and is refused unless `force`, which replaces it.
    """
    source = skill_dir().resolve()
    links = [Path.home() / TARGETS[target] / source.name for target in targets]
    theirs = [link for link in links if _occupied(link) and not _ours(link, source)]
    if theirs and not force:
        listed = ", ".join(str(link) for link in theirs)
        raise UsageError(
            f"{listed}: already exists and is not our symlink; pass --force to replace"
        )
    for link in links:
        if link in theirs:
            _remove(link)
        if not _ours(link, source):
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(source, target_is_directory=True)
    return links


def _occupied(path: Path) -> bool:
    """Something is at `path`, counting a dangling symlink, which `exists` follows and denies."""
    return path.is_symlink() or path.exists()


def _ours(link: Path, source: Path) -> bool:
    """The link is one install-skill made: a symlink to the Skill."""
    return link.is_symlink() and link.resolve() == source


def _remove(path: Path) -> None:
    """Clear whatever is at `path`: a link or file with unlink, a real directory with rmtree."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)
