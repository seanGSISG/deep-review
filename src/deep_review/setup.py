"""`deep-review setup`: the machine made ready for a Run, installing what the CLI can."""

import getpass
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from deep_review import UsageError
from deep_review.credentials import key_path, read_key, store_key
from deep_review.reviewer import KEY_FALLBACK, Reviewer
from deep_review.skill import install

# Seconds an installer script gets before it is given up on.
INSTALL_TIMEOUT_SECONDS = 300.0


def install_dirs() -> tuple[Path, ...]:
    """
    Where opencode's installer puts the binary. It edits the shell profile to add this to PATH,
    but the shell running setup is older than that edit, so setup looks here as well as on PATH.
    """
    return (Path.home() / ".opencode" / "bin",)


# What the terminal shows before the hidden key entry.
KEY_PROMPT = "Z.AI coding-plan key (hidden; stored for this user only, empty to skip): "


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the setup table: what was checked, how it stands, and what to do if anything."""

    name: str
    status: str  # ok | installed | missing
    detail: str


def setup(reviewer: Reviewer, *, assume_yes: bool) -> list[Row]:
    """
    Check the Reviewer binary, the key and the Skill links, fixing what can be fixed from here.
    A Reviewer with an official installer is installed after a confirmation that shows the exact
    command (or straight away with `assume_yes`); one that only ships over npm is left to the
    human, because Node is not ours to install. A missing key is asked for on the terminal with
    the input hidden and stored readable by this user alone; it is the one thing the coding
    agent must never ask for, because a secret typed into a chat lands in the transcript.
    """
    return [_reviewer_row(reviewer, assume_yes), _key_row(reviewer), _skill_row()]


def ready(rows: list[Row]) -> bool:
    """True when nothing in the table is still missing."""
    return all(row.status != "missing" for row in rows)


def which(binary: str) -> Path | None:
    """The binary on PATH, or in a directory an installer we ran puts things."""
    if (found := shutil.which(binary)) is not None:
        return Path(found)
    return next((d / binary for d in install_dirs() if (d / binary).is_file()), None)


def _reviewer_row(reviewer: Reviewer, assume_yes: bool) -> Row:
    if (found := which(reviewer.binary)) is not None:
        return Row("reviewer", "ok", f"{reviewer.binary} at {found}")
    if reviewer.install_script is None:
        return Row("reviewer", "missing", f"{reviewer.binary}: run `{reviewer.install_hint}`")
    command = (
        f"curl -fsSL {reviewer.install_script} | bash -s -- --version {reviewer.pinned_version}"
    )
    if not assume_yes and not ask(f"{reviewer.binary} is not installed. Run `{command}`?"):
        return Row("reviewer", "missing", f"{reviewer.binary}: run `{command}`")
    install_from_script(reviewer)
    if which(reviewer.binary) is None:
        raise UsageError(f"the {reviewer.binary} installer finished but left no binary to find")
    if shutil.which(reviewer.binary) is None:
        return Row(
            "reviewer",
            "installed",
            f"{reviewer.binary} {reviewer.pinned_version}; open a new shell so it is on PATH",
        )
    return Row("reviewer", "installed", f"{reviewer.binary} {reviewer.pinned_version}")


def _key_row(reviewer: Reviewer) -> Row:
    if os.environ.get(reviewer.key_env) or os.environ.get(KEY_FALLBACK):
        return Row("key", "ok", f"{reviewer.key_env} or {KEY_FALLBACK} is set")
    if read_key() is not None:
        return Row("key", "ok", f"stored in {key_path()}")
    how = f"run `deep-review setup` in a terminal, or export {KEY_FALLBACK} in your shell profile"
    if not terminal_present():
        return Row("key", "missing", how)
    value = ask_hidden(KEY_PROMPT).strip()
    if not value:
        return Row("key", "missing", how)
    return Row("key", "stored", f"in {store_key(value)}, readable by this user only")


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


def ask(prompt: str) -> bool:
    """A y/N question on the terminal. Anything but a leading y is no."""
    return input(f"{prompt} [y/N] ").strip().lower().startswith("y")


def ask_hidden(prompt: str) -> str:
    """
    A secret typed on the terminal, not echoed. getpass opens /dev/tty itself, so this works
    when stdin is a pipe, which it is under `curl ... | sh`. A closed terminal, Ctrl-D, is a
    skip, the same as an empty answer.
    """
    try:
        return getpass.getpass(prompt)
    except EOFError:
        return ""


def terminal_present() -> bool:
    """Whether there is a terminal to ask on. A CI job or a hook has none and gets no prompt."""
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def install_from_script(reviewer: Reviewer) -> None:
    """
    Fetch the Reviewer's official installer and run it pinned to the tested version, the way its
    own docs say to, printing the command first so nothing runs that was not shown.
    """
    assert reviewer.install_script is not None
    command = ["bash", "-s", "--", "--version", reviewer.pinned_version]
    print(f"$ curl -fsSL {reviewer.install_script} | {' '.join(command)}", file=sys.stderr)
    with urllib.request.urlopen(reviewer.install_script, timeout=30) as response:  # noqa: S310
        script = response.read()
    result = subprocess.run(
        command,
        input=script,
        cwd=Path.home(),
        timeout=INSTALL_TIMEOUT_SECONDS,
        stdout=sys.stderr,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise UsageError(f"the {reviewer.binary} installer exited {result.returncode}")
