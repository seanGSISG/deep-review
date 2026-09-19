"""Running a child process under a time cap, leaving nothing behind when the cap expires."""

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import IO


def run_capped(
    argv: Sequence[str],
    *,
    cwd: Path,
    stdout: IO[bytes] | int,
    stderr: IO[bytes] | int,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> int | None:
    """
    Run a command in `cwd`, sending its output to already-open files, and wait no longer than the
    cap. Returns its exit code, or None when the cap killed it.

    Three details here are load-bearing, and every one of them costs hours when it is missing:
    stdin is /dev/null, or an agent CLI blocks forever before its first model call; output goes to
    files rather than pipes, so a tool that outruns a pipe buffer cannot deadlock the wait; and
    the child gets its own process group, so the cap reaches everything it spawned.
    """
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        return None


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """
    Kill the child and everything it spawned. start_new_session made it a process group leader, so
    one signal reaches the whole tree. Nothing is worth flushing in a process being abandoned, so
    it goes straight to SIGKILL.
    """
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
