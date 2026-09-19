"""Invoking the Reviewer: the agent CLI that reads the Run's inputs and writes the findings file."""

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from deep_review import UsageError
from deep_review.process import run_capped

# What the Reviewer leaves in the Run directory.
FINDINGS_NAME = "findings.json"
EVENTS_NAME = "agent-events.jsonl"
STDERR_NAME = "agent.err"

# The key on Sean's machine, used when the Reviewer's own variable is unset.
KEY_FALLBACK = "Z_AI_API_KEY"


@dataclass(frozen=True, slots=True)
class Reviewer:
    """
    One agent CLI and the flags that turn it into a Reviewer: JSON events on stdout, no session
    state, and none of the user's own skills, extensions or prompt templates loaded. That last part
    is not tidiness — a Reviewer that loaded the user's skills could invoke deep-review recursively,
    and it must see only the review prompt.
    """

    name: str
    binary: str
    # Prefix added to a --model without a slash in it.
    provider: str
    default_model: str
    default_variant: str | None
    # The environment variable this CLI reads the Z.AI key from.
    key_env: str
    install_hint: str
    flags: tuple[str, ...]
    model_flag: str
    variant_flag: str


OPENCODE = Reviewer(
    name="opencode",
    binary="opencode",
    provider="zai-coding-plan",
    default_model="zai-coding-plan/glm-5.3",
    default_variant=None,
    key_env="ZHIPU_API_KEY",
    install_hint="npm install -g opencode-ai@1.18.31",
    flags=("run", "--format", "json", "--pure", "--dangerously-skip-permissions"),
    model_flag="-m",
    variant_flag="--variant",
)

# opencode is the default: best recall in the bake-off (docs/bakeoff-2026-09.md).
REVIEWERS: dict[str, Reviewer] = {OPENCODE.name: OPENCODE}
DEFAULT_REVIEWER = OPENCODE.name


@dataclass(frozen=True, slots=True)
class Outcome:
    """
    How the Reviewer process ended. The findings file, not this, decides the Run's status: the
    Reviewer is allowed to exit non-zero after writing a complete Report, and one that died before
    writing anything has failed whatever it exited with.
    """

    exit_code: int | None
    seconds: float

    @property
    def timed_out(self) -> bool:
        """True when the Run's time cap killed it, which is why there is no exit code."""
        return self.exit_code is None


def preflight(reviewer: Reviewer) -> None:
    """
    Fail before the Run does any work. A missing binary or key is the caller's setup rather than a
    failed Run, so it exits 2 with the fix instead of collecting Signal and cloning first.
    """
    if shutil.which(reviewer.binary) is None:
        raise UsageError(
            f"{reviewer.binary} is not on PATH; install it with `{reviewer.install_hint}`"
        )
    if api_key(reviewer) is None:
        raise UsageError(
            f"no Z.AI key in the environment; set {reviewer.key_env} or {KEY_FALLBACK}"
        )


def api_key(reviewer: Reviewer) -> str | None:
    """The Z.AI key, from the variable this Reviewer wants or the one Sean's machine exports."""
    return os.environ.get(reviewer.key_env) or os.environ.get(KEY_FALLBACK)


def resolve_model(reviewer: Reviewer, model: str | None) -> str:
    """A bare model id gains the Reviewer's provider prefix; one with a slash is passed through."""
    if model is None:
        return reviewer.default_model
    return model if "/" in model else f"{reviewer.provider}/{model}"


def invoke(
    reviewer: Reviewer,
    repo: Path,
    run_dir: Path,
    prompt: str,
    model: str,
    variant: str | None,
    timeout_seconds: float,
) -> Outcome:
    """
    Run the Reviewer in the checkout and wait for it, capturing its event stream and its stderr
    beside the Run's inputs. The prompt goes as a single argv element and is never piped: an agent
    CLI reads a piped prompt as something else entirely. The rest of the care this needs — stdin,
    the output files, the process group the time cap kills — lives in `run_capped`.
    """
    argv = [
        reviewer.binary,
        *reviewer.flags,
        reviewer.model_flag,
        model,
        *((reviewer.variant_flag, variant) if variant else ()),
        prompt,
    ]
    environment = {**os.environ}
    key = api_key(reviewer)
    if key is not None:
        environment[reviewer.key_env] = key
    started = time.monotonic()
    with (
        (run_dir / EVENTS_NAME).open("wb") as events,
        (run_dir / STDERR_NAME).open("wb") as errors,
    ):
        code = run_capped(
            argv,
            cwd=repo,
            env=environment,
            stdout=events,
            stderr=errors,
            timeout_seconds=timeout_seconds,
        )
    return Outcome(exit_code=code, seconds=time.monotonic() - started)
