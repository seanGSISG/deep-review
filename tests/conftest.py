"""Throwaway git checkouts, and a stand-in for the Reviewer, to point the CLI at."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from deep_review.reviewer import BINARY


def git(repo: Path, *args: str) -> str:
    """Run a git command in `repo` and return its stdout, failing the test on a non-zero exit."""
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def write(repo: Path, relative: str, content: str) -> Path:
    """Write a file in the checkout, creating parent directories."""
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def commit(repo: Path, message: str) -> str:
    """Stage everything and commit; returns the new commit's SHA."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def isolated_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Keep the ast-grep rule pack out of the suite. Every Run collects Signal, so without this a
    test on a machine with ast-grep would clone the pack from GitHub. The tests that are about
    the rules point this somewhere real.
    """
    monkeypatch.setenv("AST_GREP_RULES", str(tmp_path / "no-rules"))


@pytest.fixture(autouse=True)
def isolated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """
    Keep what a Run caches out of the developer's own cache, which is where the Reviewer's Codex
    home lives. The live Run keeps the machine's cache: it is the real thing.
    """
    if "integration" in request.keywords:
        return
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout on `main` with one commit and a .gitignore."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "main")
    git(checkout, "config", "user.email", "test@example.com")
    git(checkout, "config", "user.name", "Test")
    write(checkout, ".gitignore", "secrets/\n")
    write(checkout, "app.py", "def ship(order):\n    return order\n")
    commit(checkout, "init: app")
    return checkout


FAKE_REVIEWER = """#!/bin/sh
# A stand-in for codex. `--version` and `login status` answer preflight; `exec` records how it was
# invoked, then does what the test asked for.
R="$FAKE_REVIEWER_RECORD"
case "$1" in
--version) cat "$R/version" 2>/dev/null || echo "codex-cli 0.159.2"; exit 0 ;;
login)
  cat "$R/login_says" 2>/dev/null >&2 || echo "Logged in using ChatGPT" >&2
  exit "$(cat "$R/login_exit" 2>/dev/null || echo 0)" ;;
esac
: > "$R/argv"
for arg in "$@"; do printf '%s\\0' "$arg" >> "$R/argv"; done
readlink /proc/self/fd/0 > "$R/stdin" 2>/dev/null || echo unknown > "$R/stdin"
printf '%s' "$PWD" > "$R/cwd"
env -0 > "$R/env"
ls -A "$CODEX_HOME" > "$R/home_entries"
readlink "$CODEX_HOME/auth.json" > "$R/auth_link"
# Events in the shape `codex exec --json` prints, so a Run has stats to report without a model
# call: 10,200 input of which 9,000 cached, 120 output of which 40 reasoning, one shell command.
cat <<'EVENTS'
{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"pytest","aggregated_output":"","exit_code":0,"status":"completed"}}
{"type":"turn.completed","usage":{"input_tokens":10200,"cached_input_tokens":9000,"cache_write_input_tokens":0,"output_tokens":120,"reasoning_output_tokens":40}}
EVENTS
# The Report goes where --output-last-message points, as codex writes it.
out=""
previous=""
for arg in "$@"; do
  [ "$previous" = "--output-last-message" ] && out="$arg"
  previous="$arg"
done
if [ -f "$R/findings" ] && [ -n "$out" ]; then
  cat "$R/findings" > "$out"
fi
if [ -f "$R/hang" ]; then
  sh -c 'sleep 60' &
  echo $! > "$R/child"
  sleep 60
fi
if [ -f "$R/exit" ]; then exit "$(cat "$R/exit")"; fi
"""


@dataclass(frozen=True, slots=True)
class FakeReviewer:
    """
    The Reviewer, stubbed out: an executable on PATH that records its argv, its stdin, its
    working directory and its environment, and writes whatever the test asked it to write. It
    stands in for the real thing so the invocation details can be asserted without a model call.
    """

    record: Path

    def will_write(self, payload: object) -> None:
        """Have the Reviewer write this findings file."""
        (self.record / "findings").write_text(json.dumps(payload), encoding="utf-8")

    def will_exit(self, code: int) -> None:
        """Have the Reviewer exit with this code."""
        (self.record / "exit").write_text(str(code), encoding="utf-8")

    def will_report_version(self, version: str) -> None:
        """Have `codex --version` print this instead of the tested release."""
        (self.record / "version").write_text(version, encoding="utf-8")

    def will_log_in_with(self, status: str) -> None:
        """Have `codex login status` exit 0 but say this, as it does for a non-ChatGPT login."""
        (self.record / "login_says").write_text(status, encoding="utf-8")

    def will_fail_login(self) -> None:
        """Have `codex login status` exit 1, as it does with no credentials."""
        (self.record / "login_exit").write_text("1", encoding="utf-8")

    def will_hang(self) -> None:
        """
        Have the Reviewer sleep past any test's timeout, with a child of its own running. It hangs
        after writing, so `will_write` as well stands in for one killed once its Report was out.
        """
        (self.record / "hang").touch()

    @property
    def argv(self) -> list[str]:
        """The arguments it was given, so a prompt split across two of them would show up."""
        return (self.record / "argv").read_bytes().decode().split("\0")[:-1]

    @property
    def stdin(self) -> str:
        """Where its standard input came from: /dev/null, or it blocks before the first call."""
        return (self.record / "stdin").read_text(encoding="utf-8").strip()

    @property
    def cwd(self) -> Path:
        """The directory it ran in, which is the checkout under review."""
        return Path((self.record / "cwd").read_text(encoding="utf-8"))

    @property
    def env(self) -> dict[str, str]:
        """
        The environment it was handed, so anything the developer's shell exported for this CLI
        would show up here. NUL-separated, because a variable is allowed to hold newlines.
        """
        entries = (self.record / "env").read_bytes().decode().split("\0")[:-1]
        environment: dict[str, str] = {}
        for entry in entries:
            name, _, value = entry.partition("=")
            environment[name] = value
        return environment

    @property
    def home_entries(self) -> list[str]:
        """What its CODEX_HOME held while it ran; the directory is gone once the Run ends."""
        return (self.record / "home_entries").read_text(encoding="utf-8").split()

    @property
    def auth_link(self) -> Path:
        """Where the home's auth.json pointed while it ran."""
        return Path((self.record / "auth_link").read_text(encoding="utf-8").strip())

    @property
    def child(self) -> int:
        """The pid of the process it left running, for the time cap to kill along with it."""
        return int((self.record / "child").read_text(encoding="utf-8"))

    @property
    def ran(self) -> bool:
        """True once it has been invoked at all."""
        return (self.record / "argv").exists()


@pytest.fixture
def reviewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeReviewer:
    """
    A `codex` on PATH that is not codex, and a Codex home logged in with file credentials, so
    preflight passes and every invocation detail can be asserted without a model call.
    """
    binary = tmp_path / "bin" / BINARY
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(FAKE_REVIEWER, encoding="utf-8")
    binary.chmod(0o755)
    record = tmp_path / "record"
    record.mkdir()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"auth_mode": "chatgpt"}', encoding="utf-8")
    monkeypatch.setenv("PATH", f"{binary.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REVIEWER_RECORD", str(record))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return FakeReviewer(record=record)
