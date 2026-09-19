"""Running a child process under a time cap, leaving nothing behind when the cap expires."""

import os
import select
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import IO

# How often a streamed read looks up from the pipe to ask whether the child is still alive.
POLL_SECONDS = 0.2

# How much is taken off the pipe at a time.
READ_BYTES = 65_536


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


def run_streamed(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    cap: int,
) -> tuple[bytes, int, int | None]:
    """
    Run a command and keep only the last `cap` bytes it printed, dropping the rest as it arrives.
    Returns those bytes, how many earlier ones they replaced, and the exit code — or None for the
    code when the cap killed it. stdout and stderr come back interleaved in the order they were
    written, and a tool that prints for its whole time cap costs no more than one that prints a
    line: nothing beyond `cap` is ever held, on disk or in memory.

    Waiting ends when the *tool* ends, not when the last writer to its pipe does. A tool that
    leaves a daemon holding the pipe must not cost the caller its whole cap staring at a pipe
    nobody is going to close.
    """
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    kept = bytearray()
    dropped = 0
    code: int | None = None
    deadline = time.monotonic() + timeout_seconds
    stream = process.stdout
    if stream is None:  # unreachable with stdout=PIPE above; the type cannot say so
        _kill_group(process)
        return b"", 0, None
    with stream:
        # Read the descriptor directly: the pipe is drained by select, and going through the
        # buffered object as well would only put the same bytes in two places.
        pipe = stream.fileno()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_group(process)
                break
            if select.select([pipe], [], [], min(remaining, POLL_SECONDS))[0]:
                chunk = os.read(pipe, READ_BYTES)
                if not chunk:
                    # Every writer is gone, so there is nothing left to come.
                    code = process.wait()
                    break
                kept += chunk
                if len(kept) > cap:
                    dropped += len(kept) - cap
                    del kept[: len(kept) - cap]
            elif (code := process.poll()) is not None:
                break
    return bytes(kept), dropped, code


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """
    Kill the child and everything it spawned. start_new_session made it a process group leader, so
    one signal reaches the whole tree. Nothing is worth flushing in a process being abandoned, so
    it goes straight to SIGKILL.
    """
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
