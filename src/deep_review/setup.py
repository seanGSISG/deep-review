"""`deep-review setup`: whether this machine is ready for a Run, and what to run where it is not."""

import shutil
from dataclasses import dataclass
from pathlib import Path

from deep_review import UsageError
from deep_review.reviewer import (
    BINARY,
    INSTALL_HINT,
    installed_version,
    login_problem,
    version_problem,
)
from deep_review.skill import install


def install_dirs() -> tuple[Path, ...]:
    """
    Where opencode's installer puts its binary. opencode is only a Coding agent now, one the Skill
    is linked for, and its installer edits the shell profile rather than PATH, so setup looks here
    as well as on PATH.
    """
    return (Path.home() / ".opencode" / "bin",)


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the setup table: what was checked, how it stands, and what to do if anything."""

    name: str
    status: str  # ok | missing
    detail: str


def setup() -> list[Row]:
    """
    Check Codex, its login and the Skill links, linking the Skill where it can. Codex itself is
    not installed from here: it ships over npm or its own self-update, neither of which is ours
    to drive, and its login opens a browser, which only the human at the terminal can finish.
    """
    return [_reviewer_row(), _login_row(), _skill_row()]


def ready(rows: list[Row]) -> bool:
    """True when nothing in the table is still missing."""
    return all(row.status != "missing" for row in rows)


def which(binary: str) -> Path | None:
    """The binary on PATH, or in a directory an installer we know of puts things."""
    if (found := shutil.which(binary)) is not None:
        return Path(found)
    return next((d / binary for d in install_dirs() if (d / binary).is_file()), None)


def _reviewer_row() -> Row:
    if (found := shutil.which(BINARY)) is None:
        return Row("reviewer", "missing", f"{BINARY}: install it with {INSTALL_HINT}")
    if (problem := version_problem()) is not None:
        return Row("reviewer", "missing", problem)
    version = ".".join(str(part) for part in installed_version() or ())
    return Row("reviewer", "ok", f"{BINARY} {version} at {found}")


def _login_row() -> Row:
    if shutil.which(BINARY) is None:
        return Row("login", "missing", f"install {BINARY} first")
    if (problem := login_problem()) is not None:
        return Row("login", "missing", problem)
    return Row("login", "ok", "Codex is logged in; a Run uses that login")


def _skill_row() -> Row:
    """
    Link the Skill for the coding agents present on this machine. Claude Code gets it from the
    plugin, so opencode is what earns the ~/.claude/skills link, and pi its own tree.
    """
    targets = [target for target, binary in (("claude", "opencode"), ("pi", "pi")) if which(binary)]
    if not targets:
        return Row(
            "skill", "ok", "Claude Code loads it from the plugin; no opencode or pi to link for"
        )
    try:
        links = install(targets, force=False)
    except UsageError as error:
        return Row("skill", "missing", f"{error}: run `deep-review install-skill --force`")
    return Row("skill", "ok", "linked " + ", ".join(str(link) for link in links))
