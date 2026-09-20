"""
Signal collection: the deterministic tool output a Run gathers before the Reviewer starts, so the
Reviewer opens on Hypotheses rather than a cold repo. One file per tool under the Run's `signal/`
directory, each holding the command, its combined output and how it ended.

Nothing here can fail a Run. A tool that is not installed is a skip, a failing test suite is the
Signal most worth having, and one that hangs is killed on its own cap and said so in its file.

The module is named for the term in CONTEXT.md; absolute imports mean it does not shadow the
standard library's `signal` anywhere in the package.
"""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from deep_review import cache_dir
from deep_review.git import RUN_DIR_NAME
from deep_review.process import run_streamed

# Where a Run's Signal files land, inside the Run directory.
SIGNAL_DIR_NAME = "signal"

# What each tool gets by default, when a caller does not say. Collection is serial and runs
# before the Reviewer, so a per-tool cap is really a guard on how long a Run spends getting
# ready: fourteen tools at five minutes is over an hour on top of the Reviewer's own cap.
STEP_TIMEOUT_SECONDS = 300.0

# How much of a tool's output survives. The tail is what is kept, because that is where a test
# runner puts its failures and a linter its count.
OUTPUT_CAP_BYTES = 60_000

# Probes only ask a tool what it can do, so they get far less time than running it does.
PROBE_TIMEOUT_SECONDS = 15.0

# CodeRabbit's rule pack, pinned so the same Diff yields the same Hypotheses next month. The
# environment variable points at a different pack and turns the clone off entirely.
AST_GREP_RULES_ENV = "AST_GREP_RULES"
AST_GREP_RULES_REPO = "https://github.com/coderabbitai/ast-grep-essentials"
AST_GREP_RULES_COMMIT = "73120109bf45c284d0cd8a37bdd7082e80e92e87"
AST_GREP_CONFIG_NAME = "sgconfig.yml"
CLONE_TIMEOUT_SECONDS = 120.0

# How long a failed clone is left alone. An unreachable GitHub then costs one Run its wait
# instead of taxing every Run after it, and a connection that comes back is picked up with no
# cleanup from anyone.
CLONE_RETRY_AFTER_SECONDS = 3600.0

# What makes a checkout worth pointing ruff and pytest at.
PYTHON_MARKERS = ("pyproject.toml", "src", "tests")


@dataclass(frozen=True, slots=True)
class Step:
    """One tool's Signal: the file it fills under `signal/`, and the command that fills it."""

    name: str
    argv: tuple[str, ...]


def collect(repo: Path, run_dir: Path, timeout_seconds: float = STEP_TIMEOUT_SECONDS) -> list[Path]:
    """
    Gather every tool's Signal into `run_dir/signal/` and say which files were written, giving
    each tool `timeout_seconds` of its own. A tool that is not installed writes nothing at all,
    and neither a broken tool nor an unwritable directory raises: the Reviewer reads the checkout
    either way, and a Run that died because a linter could not run has failed at the only thing
    it was for.
    """
    signal_dir = run_dir / SIGNAL_DIR_NAME
    written: list[Path] = []
    try:
        signal_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written
    for collector in COLLECTORS:
        try:
            steps = list(collector(repo))
        except OSError:
            continue
        for step in steps:
            path = _run_step(repo, signal_dir, step, timeout_seconds)
            if path is not None:
                written.append(path)
    return written


def just_steps(repo: Path) -> Iterator[Step]:
    """
    The repo's own `just test` and `just lint`. Its recipes are what its maintainers actually run,
    so they come first and are asked for by name rather than assumed to exist.
    """
    if not (repo / "justfile").is_file():
        return
    recipes = _just_recipes(repo)
    for recipe in ("test", "lint"):
        if recipe in recipes:
            yield Step(f"just-{recipe}", ("just", recipe))


def node_steps(repo: Path) -> Iterator[Step]:
    """
    The scripts a package.json declares, run through the package manager its lockfile names.
    Dependencies are installed first when they are missing, because a lint script cannot run
    without them; scripts are skipped rather than guessed at when the manifest does not list them.
    """
    manifest = repo / "package.json"
    if not manifest.is_file():
        return
    manager = _package_manager(repo)
    if manager is None:
        return
    if not (repo / "node_modules").is_dir():
        yield Step("install", (manager, "install", "--ignore-scripts"))
    scripts = _package_scripts(manifest)
    for script in ("lint", "typecheck", "test"):
        if script in scripts:
            yield Step(f"pkg-{script}", (manager, "run", script))


def python_steps(repo: Path) -> Iterator[Step]:
    """
    ruff and pytest through uv, which fetches both on demand, so a checkout with no environment of
    its own still produces Signal.
    """
    if not _looks_like_python(repo):
        return
    yield Step("ruff", ("uvx", "ruff", "check", "."))
    yield Step("pytest", ("uv", "run", "--with", "pytest", "pytest", "-q"))


def go_steps(repo: Path) -> Iterator[Step]:
    """`go vet` and `go test` across a module."""
    if (repo / "go.mod").is_file():
        yield Step("go-vet", ("go", "vet", "./..."))
        yield Step("go-test", ("go", "test", "./..."))


def rust_steps(repo: Path) -> Iterator[Step]:
    """clippy and the test suite for a cargo crate."""
    if (repo / "Cargo.toml").is_file():
        yield Step("cargo-clippy", ("cargo", "clippy", "--all-targets", "-q"))
        yield Step("cargo-test", ("cargo", "test", "-q"))


def gitleaks_steps(repo: Path) -> Iterator[Step]:
    """
    Secret scanning over the whole checkout, in any language. Findings are redacted, so a Signal
    file the Reviewer reads and a Run artifact someone downloads never carry the secret itself.
    """
    yield Step("gitleaks", ("gitleaks", "dir", ".", "--no-banner", "--redact", "-v"))


def ast_grep_steps(repo: Path) -> Iterator[Step]:
    """
    CodeRabbit's essentials rule pack, run over the checkout. The binary is checked for before the
    rules are, because fetching a rule pack for a tool that cannot run it is wasted time.
    """
    if shutil.which("ast-grep") is None:
        return
    rules = ast_grep_rules()
    if rules is None:
        return
    config = rules / AST_GREP_CONFIG_NAME
    yield Step("ast-grep", ("ast-grep", "scan", "-c", str(config), "--report-style", "short", "."))


# Every collector a Run asks, in the order their Signal is gathered.
COLLECTORS: tuple[Callable[[Path], Iterator[Step]], ...] = (
    just_steps,
    node_steps,
    python_steps,
    go_steps,
    rust_steps,
    gitleaks_steps,
    ast_grep_steps,
)


def ast_grep_rules() -> Path | None:
    """
    Where the ast-grep rules are: whatever AST_GREP_RULES points at, or the pinned pack in the
    user's cache, cloned the first time and reused after. None when there is no usable pack, which
    skips the tool. An override that turns out to hold no rules is not quietly replaced by the
    pinned pack: the caller said which rules to use.
    """
    override = os.environ.get(AST_GREP_RULES_ENV)
    if override:
        pack = Path(override)
        return pack if (pack / AST_GREP_CONFIG_NAME).is_file() else None
    cache = rules_cache_dir()
    if (cache / AST_GREP_CONFIG_NAME).is_file():
        return cache
    if _backing_off(cache):
        return None
    return cache if _clone_rules(cache) else None


def rules_cache_dir() -> Path:
    """The cached rule pack's home."""
    return cache_dir("ast-grep-essentials")


def _clone_rules(cache: Path) -> bool:
    """
    Fetch the rule pack at its pinned commit. It is built in a sibling directory and moved into
    place whole, so a clone that is interrupted cannot leave a half-written cache for later Runs
    to trust. A shallow fetch of a bare SHA is what keeps this to one commit instead of a history.
    """
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="ast-grep-rules-", dir=cache.parent))
    except OSError:
        return False
    here = ("git", "-C", str(staging))
    clone = (
        ("git", "init", "--quiet", str(staging)),
        (*here, "remote", "add", "origin", AST_GREP_RULES_REPO),
        (*here, "fetch", "--depth", "1", "--quiet", "origin", AST_GREP_RULES_COMMIT),
        (*here, "checkout", "--quiet", "FETCH_HEAD"),
    )
    if not all(_ask(argv, CLONE_TIMEOUT_SECONDS) is not None for argv in clone):
        shutil.rmtree(staging, ignore_errors=True)
        _remember_failure(cache)
        return False
    try:
        staging.rename(cache)
    except OSError:
        # Another Run got there first, which is a reuse rather than a failure.
        shutil.rmtree(staging, ignore_errors=True)
    if not (cache / AST_GREP_CONFIG_NAME).is_file():
        _remember_failure(cache)
        return False
    with suppress(OSError):
        _failure_marker(cache).unlink(missing_ok=True)
    return True


def _failure_marker(cache: Path) -> Path:
    """Where a failed clone is remembered: beside the cache it could not fill."""
    return cache.parent / f"{cache.name}.failed"


def _remember_failure(cache: Path) -> None:
    """
    Note that the clone did not work, so the Runs that follow skip it until the mark ages out.
    Failing to leave the mark only costs the next Run the same wait, so it is not worth raising.
    """
    with suppress(OSError):
        _failure_marker(cache).touch()


def _backing_off(cache: Path) -> bool:
    """
    True while the last clone's failure is recent enough that trying again would just spend
    another Run's time on the same unreachable remote. It ages out on its own, so a GitHub that
    comes back is picked up without anyone deleting anything.
    """
    try:
        age = time.time() - _failure_marker(cache).stat().st_mtime
    except OSError:
        return False
    return age < CLONE_RETRY_AFTER_SECONDS


def _run_step(repo: Path, signal_dir: Path, step: Step, cap: float) -> Path | None:
    """
    Run one tool and write its Signal file: the command, its combined output capped at the last
    OUTPUT_CAP_BYTES bytes, and how it ended. The cap is applied as the output arrives rather than
    afterwards, so a tool that never stops printing costs a Run no more than a quiet one does.
    """
    if shutil.which(step.argv[0]) is None:
        return None
    path = signal_dir / f"{step.name}.txt"
    try:
        output, dropped, code = run_streamed(
            step.argv, cwd=repo, timeout_seconds=cap, cap=OUTPUT_CAP_BYTES
        )
        body = output.decode("utf-8", errors="replace")
        path.write_text(_compose(step, body, dropped, code, cap), encoding="utf-8")
    except OSError:
        return None
    return path


def _compose(step: Step, body: str, dropped: int, code: int | None, cap: float) -> str:
    """
    One Signal file. The command and how the tool ended are always there, whatever the cap did to
    what came between them: a tool the Reviewer cannot tell passed from failed is not Signal. The
    cap is passed rather than read back, so the file names the timeout the tool actually got.
    """
    lines = [f"$ {shlex.join(step.argv)}"]
    if dropped:
        lines.append(f"[... {dropped} earlier bytes dropped ...]")
    if trimmed := body.rstrip("\n"):
        lines.append(trimmed)
    lines.append(f"[killed on the {cap:g}s timeout]" if code is None else f"[exit {code}]")
    return "\n".join(lines) + "\n"


def _just_recipes(repo: Path) -> set[str]:
    """The recipe names `just` reports for this checkout, or none when it cannot say."""
    summary = _ask(("just", "--summary"), PROBE_TIMEOUT_SECONDS, cwd=repo)
    return set(summary.split()) if summary else set()


def _package_manager(repo: Path) -> str | None:
    """
    The package manager this checkout is locked to, falling back to npm. None when even npm is
    missing, which makes every package.json script a skip.
    """
    for lockfile, manager in (
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("pnpm-lock.yaml", "pnpm"),
    ):
        if (repo / lockfile).is_file() and shutil.which(manager) is not None:
            return manager
    return "npm" if shutil.which("npm") is not None else None


def _package_scripts(manifest: Path) -> set[str]:
    """The script names a package.json declares, or none when it cannot be read as one."""
    try:
        content = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    scripts = content.get("scripts") if isinstance(content, dict) else None
    return set(scripts) if isinstance(scripts, dict) else set()


def _looks_like_python(repo: Path) -> bool:
    """True when the checkout has enough Python in it to be worth linting and testing."""
    if any((repo / marker).exists() for marker in PYTHON_MARKERS):
        return True
    return any(repo.glob("*.py"))


def _ask(argv: tuple[str, ...], timeout_seconds: float, cwd: Path | None = None) -> str | None:
    """
    Run one short command a Run only needs an answer from — which recipes exist, whether a fetch
    worked — and return its stdout, or None when it failed, was missing or took too long. Tools
    whose *output* is the Signal go through `_run_step` instead; nothing here is capped or kept.
    """
    try:
        answer = subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return answer.stdout if answer.returncode == 0 else None


def main() -> None:
    """
    Collect Signal into `.deep-review/signal/` in the current checkout, for the shell callers
    still standing between this port and Deliverable 3 — which replaces both of them with one
    `deep-review review` call, and this entry point with them.
    """
    collect(Path.cwd(), Path.cwd() / RUN_DIR_NAME)


if __name__ == "__main__":
    main()
