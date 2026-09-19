"""Throwaway git checkouts, and a stand-in for the Reviewer, to point the CLI at."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from deep_review.reviewer import OPENCODE, PI, Reviewer


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
    test on a machine with ast-grep would clone the pack from GitHub and leave it in the
    developer's own cache. The tests that are about the rules point these somewhere real.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("AST_GREP_RULES", str(tmp_path / "no-rules"))


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
# A stand-in for the Reviewer: it records how it was invoked, then does what the test asked for.
R="$FAKE_REVIEWER_RECORD"
: > "$R/argv"
for arg in "$@"; do printf '%s\\0' "$arg" >> "$R/argv"; done
readlink /proc/self/fd/0 > "$R/stdin" 2>/dev/null || echo unknown > "$R/stdin"
printf '%s' "$PWD" > "$R/cwd"
# Two events in the shape the CLI it is standing in for prints, so a Run has stats to report
# without a model call. Both spellings report the same spend: 1200 fresh, 9000 cached, 80 written
# and 40 reasoned, which is pi's 120 of output with its reasoning still inside it.
case "$(basename "$0")" in
pi)
cat <<'EVENTS'
{"type":"message_end","message":{"role":"assistant","usage":{"input":1200,"output":120,"reasoning":40,"cacheRead":9000,"cacheWrite":0}}}
{"type":"tool_execution_start","toolCallId":"toolu_1","toolName":"bash","args":{}}
EVENTS
;;
*)
cat <<'EVENTS'
{"type":"step_finish","part":{"id":"prt_step","type":"step-finish","tokens":{"input":1200,"output":80,"reasoning":40,"cache":{"write":0,"read":9000}}}}
{"type":"tool_use","part":{"id":"prt_call","callID":"call_1","type":"tool","tool":"bash"}}
EVENTS
;;
esac
if [ -f "$R/findings" ]; then
  mkdir -p .deep-review
  cat "$R/findings" > .deep-review/findings.json
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
    The Reviewer, stubbed out: an executable on PATH that records its argv, its stdin and its
    working directory, and writes whatever the test asked it to write. It stands in for the real
    thing so the invocation details can be asserted without a model call.
    """

    record: Path

    def will_write(self, payload: object) -> None:
        """Have the Reviewer write this findings file."""
        (self.record / "findings").write_text(json.dumps(payload), encoding="utf-8")

    def will_exit(self, code: int) -> None:
        """Have the Reviewer exit with this code."""
        (self.record / "exit").write_text(str(code), encoding="utf-8")

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
    def child(self) -> int:
        """The pid of the process it left running, for the time cap to kill along with it."""
        return int((self.record / "child").read_text(encoding="utf-8"))

    @property
    def ran(self) -> bool:
        """True once it has been invoked at all."""
        return (self.record / "argv").exists()


def install(reviewer: Reviewer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeReviewer:
    """Put the stand-in on PATH under this Reviewer's name, with the key its preflight looks for."""
    binary = tmp_path / "bin" / reviewer.binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(FAKE_REVIEWER, encoding="utf-8")
    binary.chmod(0o755)
    record = tmp_path / "record"
    record.mkdir()
    monkeypatch.setenv("PATH", f"{binary.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REVIEWER_RECORD", str(record))
    monkeypatch.setenv(reviewer.key_env, "test-key")
    return FakeReviewer(record=record)


@pytest.fixture
def reviewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeReviewer:
    """An `opencode` on PATH that is not opencode, printing opencode's event shape."""
    return install(OPENCODE, tmp_path, monkeypatch)


@pytest.fixture
def pi_reviewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeReviewer:
    """The same stand-in installed as `pi`, printing pi's event shape instead."""
    return install(PI, tmp_path, monkeypatch)
