"""Invoking the Reviewer: the agent CLI that reads the Run's inputs and writes the findings file."""

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deep_review import UsageError, cache_dir
from deep_review.credentials import read_key
from deep_review.events import opencode_stats, pi_stats
from deep_review.process import run_capped
from deep_review.report import RunStats

# What the Reviewer leaves in the Run directory.
FINDINGS_NAME = "findings.json"
EVENTS_NAME = "agent-events.jsonl"
STDERR_NAME = "agent.err"

# The key on Sean's machine, used when the Reviewer's own variable is unset.
KEY_FALLBACK = "Z_AI_API_KEY"


@dataclass(frozen=True, slots=True)
class Reviewer:
    """
    One agent CLI, and the flags and environment that turn it into a Reviewer: JSON events on
    stdout, no session state, and none of the user's own instruction files, skills, extensions,
    plugins, MCP servers or prompt templates loaded. That last part is not tidiness — a Reviewer
    that loaded the user's skills could invoke deep-review recursively, one that loaded the
    checkout's AGENTS.md would take instructions from the change it is reviewing, and it must see
    only the review prompt.
    """

    name: str
    binary: str
    # Prefix added to a --model without a slash in it.
    provider: str
    default_model: str
    default_variant: str | None
    # The environment variable this CLI reads the Z.AI key from.
    key_env: str
    # The release the CLI was tested against, and what `setup` installs.
    pinned_version: str
    # The official installer script, when this CLI ships a standalone binary that way; `setup`
    # pipes it to bash with the pinned version. None for a CLI that only ships over npm.
    install_script: str | None
    # What preflight tells a human to run when the binary is missing.
    install_hint: str
    flags: tuple[str, ...]
    model_flag: str
    variant_flag: str
    # This CLI's own environment namespace. Every variable in it is dropped from a Run's
    # environment, because these CLIs take whole configurations that way: OPENCODE_CONFIG_CONTENT
    # alone would hand the Reviewer one from the developer's shell.
    env_prefix: str
    # The variable pointing this CLI at the directory it reads its own settings from. A Run gives
    # it an empty one of ours, which is what strips the rest: global instruction files, installed
    # packages, plugins, MCP servers and, for pi, project trust.
    home_env: str
    # What a Run sets to turn off the context this CLI still loads on its own. Assignments, not
    # names: everything else here called `env` holds the name of a variable.
    disables: tuple[tuple[str, str], ...]
    # How this CLI's own event stream is read back into the Run's stats.
    read_stats: Callable[[Path, RunStats], RunStats]


OPENCODE = Reviewer(
    name="opencode",
    binary="opencode",
    provider="zai-coding-plan",
    default_model="zai-coding-plan/glm-5.3",
    default_variant=None,
    key_env="ZHIPU_API_KEY",
    pinned_version="1.18.31",
    install_script="https://opencode.ai/install",
    install_hint="deep-review setup",
    flags=("run", "--format", "json", "--pure", "--dangerously-skip-permissions"),
    model_flag="-m",
    variant_flag="--variant",
    env_prefix="OPENCODE_",
    # Global.Path.config, which is $XDG_CONFIG_HOME/opencode: its AGENTS.md is pushed onto the
    # system prompt with no flag of its own, and its opencode.json carries plugins and MCP
    # servers. OPENCODE_CONFIG_DIR is not the lever it looks like — it appends a second AGENTS.md
    # path rather than replacing the default, so setting it adds a source instead of removing one.
    home_env="XDG_CONFIG_HOME",
    disables=(
        # The walk from cwd to the worktree root: AGENTS.md, CLAUDE.md, CONTEXT.md and the
        # project opencode.json, all of them from the checkout under review. It also gates the
        # project .opencode directories, and a .opencode in a checkout starts an npm install
        # that writes into the tree the Run is meant to leave alone.
        ("OPENCODE_DISABLE_PROJECT_CONFIG", "1"),
        # Both halves of the Claude Code bridge in one flag: CLAUDE.md, from the checkout and
        # from ~/.claude, and the skills under ~/.claude/skills and .claude/skills — one of
        # which is the Skill that asks for a Run in the first place.
        ("OPENCODE_DISABLE_CLAUDE_CODE", "1"),
    ),
    read_stats=opencode_stats,
)

PI = Reviewer(
    name="pi",
    binary="pi",
    provider="zai",
    default_model="zai/glm-5.3",
    # pi thinks at its model's own default unless told otherwise; medium is what the bake-off ran.
    default_variant="medium",
    key_env="ZAI_API_KEY",
    pinned_version="0.85.1",
    install_script=None,
    install_hint="npm install -g @earendil-works/pi-coding-agent@0.85.1",
    flags=(
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        # AGENTS.md and CLAUDE.md discovery, which pi does whatever the project is trusted for.
        "--no-context-files",
        # Project-local files, which with Sean's defaultProjectTrust of "always" are otherwise
        # trusted outright in the non-interactive mode a Run uses.
        "--no-approve",
    ),
    model_flag="--model",
    variant_flag="--thinking",
    env_prefix="PI_",
    # settings.json and everything it names — packages, extensions, skills, prompts — plus the
    # tools directory and trust.json. auth.json lives here too, so a Run's key has to come from
    # the environment instead; it does, which is what makes an empty directory workable.
    home_env="PI_CODING_AGENT_DIR",
    disables=(),
    read_stats=pi_stats,
)

# opencode is the default: best recall in the bake-off (docs/bakeoff-2026-09.md). pi is selectable
# for roughly half the tokens, and it found a race on PR #1 that opencode never looked for.
REVIEWERS: dict[str, Reviewer] = {reviewer.name: reviewer for reviewer in (OPENCODE, PI)}
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
    """
    The Z.AI key: the variable this Reviewer's own CLI reads, then the CLI's fallback name, then
    the file `deep-review setup` stored. The environment wins so a shell can override the file.
    """
    return os.environ.get(reviewer.key_env) or os.environ.get(KEY_FALLBACK) or read_key()


def resolve_model(reviewer: Reviewer, model: str | None) -> str:
    """A bare model id gains the Reviewer's provider prefix; one with a slash is passed through."""
    if model is None:
        return reviewer.default_model
    return model if "/" in model else f"{reviewer.provider}/{model}"


def reviewer_home(reviewer: Reviewer) -> Path:
    """
    The settings directory a Run hands this Reviewer in place of the user's own: empty, ours, and
    the same one on every Run. Nothing writes context into it, so what the Reviewer loads from it
    is nothing, here and on anyone else's machine. It lives in the cache because that is what it
    is — opencode fills it with the plugin package it installs for itself, pi with an empty
    auth.json, and a Run that finds it missing costs one npm install to build it again.

    One directory per Reviewer, because both CLIs would otherwise write their own state into it.
    Neither needs it to exist first: both create it, which is why nothing here does.
    """
    return cache_dir("reviewers", reviewer.name)


def hermetic_env(reviewer: Reviewer) -> dict[str, str]:
    """
    The environment a Run gives the Reviewer, built from the caller's rather than replacing it:
    the Reviewer needs a PATH and a shell for the bash tool that proves its Findings. Three things
    happen on the way in. Every variable in the CLI's own namespace is dropped, so nothing the
    developer exported can change a Run. The CLI is pointed at an empty settings directory of
    ours. And the flags that turn off the context it still finds by itself go in, after the drop,
    so a variable in the shell cannot switch one of them back off.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(reviewer.env_prefix)
    }
    environment[reviewer.home_env] = str(reviewer_home(reviewer))
    environment.update(reviewer.disables)
    if (key := api_key(reviewer)) is not None:
        environment[reviewer.key_env] = key
    return environment


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
    CLI reads a piped prompt as something else entirely. What it runs in is not the caller's
    environment but `hermetic_env`, so the prompt is all it was told. The rest of the care this
    needs — stdin, the output files, the process group the time cap kills — lives in `run_capped`.
    """
    argv = [
        reviewer.binary,
        *reviewer.flags,
        reviewer.model_flag,
        model,
        *((reviewer.variant_flag, variant) if variant else ()),
        prompt,
    ]
    started = time.monotonic()
    with (
        (run_dir / EVENTS_NAME).open("wb") as events,
        (run_dir / STDERR_NAME).open("wb") as errors,
    ):
        code = run_capped(
            argv,
            cwd=repo,
            env=hermetic_env(reviewer),
            stdout=events,
            stderr=errors,
            timeout_seconds=timeout_seconds,
        )
    return Outcome(exit_code=code, seconds=time.monotonic() - started)
