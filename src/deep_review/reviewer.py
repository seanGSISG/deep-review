"""Invoking the Reviewer: `codex exec`, reading the Run's inputs and answering with the Report."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from deep_review import UsageError
from deep_review.process import run_capped
from deep_review.report import FINDINGS_SCHEMA

# What the Reviewer leaves in the Run directory.
FINDINGS_NAME = "findings.json"
SCHEMA_NAME = "findings.schema.json"
EVENTS_NAME = "agent-events.jsonl"
STDERR_NAME = "agent.err"

BINARY = "codex"
# The model and effort argus runs on (pr-agent-colo, 2026-09-29 eval), on the ChatGPT login.
DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "medium"
# The release this was tested against. 0.156.1 refused gpt-6.1-sol on a ChatGPT login.
MIN_VERSION = (0, 159, 2)
INSTALL_HINT = "npm install -g @openai/codex, or `codex update`"

# Seconds `codex --version` and `codex login status` get before preflight gives up on them.
PROBE_TIMEOUT_SECONDS = 30.0

# What `codex login status` prints (on stderr) for a ChatGPT login, the only one a Run accepts:
# an API key exits 0 as well, and would bill metered credit (codex-rs/cli/src/login.rs).
CHATGPT_LOGIN = "Logged in using ChatGPT"

# Codex's own environment namespace. Every variable in it is dropped from a Run's environment,
# because CODEX_HOME alone would hand the Reviewer the developer's config, and CODEX_API_KEY
# would move the Run off the ChatGPT login onto metered API billing.
ENV_PREFIX = "CODEX_"


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


def preflight() -> None:
    """
    Fail before the Run does any work. A missing or old binary, or a machine not logged in, is the
    caller's setup rather than a failed Run, so it exits 2 with the fix instead of collecting
    Signal first. The login is checked the way a Run will see it: through the Reviewer's home.
    """
    if shutil.which(BINARY) is None:
        raise UsageError(f"{BINARY} is not on PATH; install it with {INSTALL_HINT}")
    if (problem := version_problem()) is not None:
        raise UsageError(problem)
    if (problem := login_problem()) is not None:
        raise UsageError(problem)


def version_problem() -> str | None:
    """What is wrong with the installed codex's version, or None when it is new enough."""
    found = installed_version()
    if found is None:
        return f"could not read `{BINARY} --version`; reinstall it with {INSTALL_HINT}"
    if found < MIN_VERSION:
        return (
            f"{BINARY} {_dotted(found)} is older than {_dotted(MIN_VERSION)}, the release "
            f"deep-review is tested against; update it with {INSTALL_HINT}"
        )
    return None


def installed_version() -> tuple[int, ...] | None:
    """The installed codex's version, from `codex --version` (`codex-cli 0.159.2`)."""
    try:
        result = subprocess.run(
            [BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
    return tuple(int(part) for part in match.groups()) if match else None


def login_problem() -> str | None:
    """
    Why a Run could not authenticate on the ChatGPT plan, or None when it can. `codex login
    status` runs against a Reviewer home, so a login kept anywhere but the developer's auth.json
    (a keyring, say) shows up here instead of as a failed Run, and it must name ChatGPT: it exits
    0 for an API key too.
    """
    if not user_auth_file().is_file():
        return (
            f"no Codex login in {user_auth_file()}; run `codex login` in a terminal "
            "(deep-review needs the default file credential store)"
        )
    try:
        with reviewer_home() as home:
            result = subprocess.run(
                [BINARY, "login", "status"],
                env=hermetic_env(home),
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"`{BINARY} login status` did not finish: {error}"
    if result.returncode != 0:
        return (
            f"`{BINARY} login status` exited {result.returncode}; run `codex login` in a terminal"
        )
    said = (result.stderr + result.stdout).strip()
    if CHATGPT_LOGIN not in said:
        return (
            f"Codex is not on a ChatGPT login ({said or 'no status'}); a Run would bill metered "
            "API credit. Run `codex login` and sign in with ChatGPT"
        )
    return None


def user_codex_home() -> Path:
    """
    The developer's own Codex home, where their login lives. Absolute, because the Reviewer's home
    links to it from somewhere else entirely: a relative CODEX_HOME would link to nothing.
    """
    configured = os.environ.get("CODEX_HOME")
    return (Path(configured).expanduser() if configured else Path.home() / ".codex").absolute()


def user_auth_file() -> Path:
    """The developer's Codex credentials: file storage, Codex's default."""
    return user_codex_home() / "auth.json"


@contextmanager
def reviewer_home() -> Iterator[Path]:
    """
    The Codex home a Run hands the Reviewer in place of the developer's: a fresh temporary
    directory holding nothing but a link to their auth.json, deleted when the Run ends. Codex
    reads the global AGENTS.md straight from its home with no flag to stop it, and its config,
    hooks, rules, skills and memories live there too, so an empty home is the one lever that
    reaches all of them (ADR-0004). Fresh per Run, because Codex writes its own state into the
    home as it goes: a shared one would carry one Run's leftovers into the next, and two Runs
    racing to set up the link would crash one of them.

    auth.json is a symlink rather than a copy because Codex refreshes the token by opening the
    file in place: the refresh lands in the developer's own file, and a copy would drift from it.
    """
    with tempfile.TemporaryDirectory(prefix="deep-review-codex-") as directory:
        home = Path(directory)
        (home / "auth.json").symlink_to(user_auth_file())
        yield home


def hermetic_env(home: Path) -> dict[str, str]:
    """
    The environment a Run gives the Reviewer, built from the caller's rather than replacing it:
    the Reviewer needs a PATH and a shell for the commands that prove its Findings. Codex's whole
    namespace is dropped, then CODEX_HOME is pointed at the Reviewer's own `home`.
    """
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith(ENV_PREFIX)
    }
    environment["CODEX_HOME"] = str(home)
    return environment


def argv(run_dir: Path, prompt: str, model: str, effort: str) -> list[str]:
    """
    The whole `codex exec` command line. Every flag here is load-bearing:

    - `--json` streams the events the Run's stats are read from; `-o` with `--output-schema`
      makes the final message the Report, validated against the schema by the API itself.
    - `--ephemeral` keeps a Run out of the developer's session history.
    - `--ignore-user-config` and `--ignore-rules` skip config and execpolicy rules, belt and
      braces on top of the empty home.
    - `project_doc_max_bytes=0` stops the AGENTS.md walk through the checkout under review, and
      `skills.include_instructions=false` drops the catalog of ~/.agents/skills and the repo's
      .agents/skills. Repo guidelines still reach the Reviewer, but only because the prompt tells
      it to read them as files (issue #12), not as instructions it was handed.
    - `features.hooks=false`: a repo's .codex/hooks.json must never run on a Run.
    - `workspace-write` lets the Reviewer run tests in the checkout and write throwaway scripts
      in /tmp, keeps .git read-only and the network off. The user's cache is writable too,
      because uv, go and friends keep theirs there and every test run needs it.
    """
    return [
        BINARY,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--config",
        f"sandbox_workspace_write.writable_roots={json.dumps([str(_user_cache())])}",
        "--config",
        "project_doc_max_bytes=0",
        "--config",
        "skills.include_instructions=false",
        "--config",
        "features.hooks=false",
        "--model",
        model,
        "--config",
        f"model_reasoning_effort={json.dumps(effort)}",
        "--output-schema",
        str(run_dir / SCHEMA_NAME),
        "--output-last-message",
        str(run_dir / FINDINGS_NAME),
        prompt,
    ]


def invoke(
    repo: Path,
    run_dir: Path,
    prompt: str,
    model: str,
    effort: str,
    timeout_seconds: float,
) -> Outcome:
    """
    Run the Reviewer in the checkout and wait for it, capturing its event stream and its stderr
    beside the Run's inputs. The prompt goes as a single argv element and stdin is /dev/null:
    `codex exec` reads a piped stdin as extra context. The rest of the care this needs — the
    output files, the process group the time cap kills — lives in `run_capped`.
    """
    (run_dir / SCHEMA_NAME).write_text(json.dumps(FINDINGS_SCHEMA, indent=2), encoding="utf-8")
    started = time.monotonic()
    with (
        reviewer_home() as home,
        (run_dir / EVENTS_NAME).open("wb") as events,
        (run_dir / STDERR_NAME).open("wb") as errors,
    ):
        code = run_capped(
            argv(run_dir, prompt, model, effort),
            cwd=repo,
            env=hermetic_env(home),
            stdout=events,
            stderr=errors,
            timeout_seconds=timeout_seconds,
        )
    return Outcome(exit_code=code, seconds=time.monotonic() - started)


def _user_cache() -> Path:
    """The user's cache root, honouring XDG_CACHE_HOME as the tools that write there do."""
    configured = os.environ.get("XDG_CACHE_HOME")
    return Path(configured) if configured else Path.home() / ".cache"


def _dotted(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)
