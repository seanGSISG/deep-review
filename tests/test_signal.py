"""Signal collection: what it runs, what it writes, and everything it refuses to fail on."""

import json
import os
import time
from pathlib import Path

import pytest

from conftest import git, write
from deep_review import signal
from deep_review.signal import ast_grep_rules, collect


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway rules cache, so no test reads or writes the developer's real one."""
    root = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(root))
    monkeypatch.delenv(signal.AST_GREP_RULES_ENV, raising=False)
    return root / "deep-review" / "ast-grep-essentials"


@pytest.fixture
def tools(tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A PATH holding only the stand-ins a test puts in it, so collection sees exactly the tools the
    test installed and never the machine's own — which would run this repo's real test suite.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PATH", str(binaries))
    return binaries


# The machine's own PATH, captured before `tools` narrows it. A stand-in gets it back, so it can
# still call `sleep` and friends while collection itself sees only the stand-ins.
SYSTEM_PATH = os.environ["PATH"]


def stub(tools: Path, name: str, body: str) -> None:
    """Put a stand-in for a tool on PATH, running `body` as a shell script."""
    path = tools / name
    path.write_text(f'#!/bin/sh\nexport PATH="{SYSTEM_PATH}"\n{body}\n', encoding="utf-8")
    path.chmod(0o755)


def signal_dir(repo: Path) -> Path:
    """Where a Run's Signal files land."""
    return repo / ".deep-review" / "signal"


def test_a_signal_file_is_written_for_every_tool_present(repo: Path, tools: Path) -> None:
    write(repo, "go.mod", "module example.com/app\n")
    stub(tools, "go", 'echo "ran $*"')

    written = collect(repo, repo / ".deep-review")

    assert {path.name for path in written} == {"go-vet.txt", "go-test.txt"}
    vet = (signal_dir(repo) / "go-vet.txt").read_text(encoding="utf-8")
    assert vet == "$ go vet ./...\nran vet ./...\n[exit 0]\n"


def test_a_missing_binary_is_a_skip_rather_than_a_failure(repo: Path, tools: Path) -> None:
    write(repo, "go.mod", "module example.com/app\n")
    write(repo, "Cargo.toml", '[package]\nname = "app"\n')

    assert collect(repo, repo / ".deep-review") == []
    assert list(signal_dir(repo).iterdir()) == []


def test_a_failing_tool_is_signal_rather_than_a_failed_run(repo: Path, tools: Path) -> None:
    write(repo, "go.mod", "module example.com/app\n")
    stub(tools, "go", 'echo "app.go:12: unreachable code" >&2\nexit 2')

    written = collect(repo, repo / ".deep-review")

    vet = (signal_dir(repo) / "go-vet.txt").read_text(encoding="utf-8")
    assert len(written) == 2
    assert "app.go:12: unreachable code" in vet, "a tool's stderr is Signal too"
    assert vet.endswith("[exit 2]\n")


def test_a_tool_that_outlives_its_cap_is_killed_and_said_so(
    repo: Path, tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signal, "STEP_TIMEOUT_SECONDS", 0.5)
    write(repo, "go.mod", "module example.com/app\n")
    stub(tools, "go", "echo starting\nsleep 30")

    started = time.monotonic()
    collect(repo, repo / ".deep-review")

    assert time.monotonic() - started < 20, "the cap did not stop the tool"
    vet = (signal_dir(repo) / "go-vet.txt").read_text(encoding="utf-8")
    assert "starting" in vet, "what it managed to say before the cap is kept"
    assert "killed" in vet


def test_output_beyond_the_cap_is_truncated_rather_than_dropped(
    repo: Path, tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signal, "OUTPUT_CAP_BYTES", 300)
    write(repo, "go.mod", "module example.com/app\n")
    stub(tools, "go", 'i=0\nwhile [ $i -lt 200 ]; do echo "line $i of padding"; i=$((i + 1)); done')

    collect(repo, repo / ".deep-review")

    vet = (signal_dir(repo) / "go-vet.txt").read_text(encoding="utf-8")
    assert vet.startswith("$ go vet ./...\n"), "the command survives the cap"
    assert vet.endswith("[exit 0]\n"), "so does how it ended"
    assert "line 199 of padding" in vet, "the tail is where a tool puts its conclusion"
    assert "line 0 of padding" not in vet
    assert "dropped" in vet


def test_the_repos_own_recipes_are_run_when_it_has_them(repo: Path, tools: Path) -> None:
    write(repo, "justfile", "test:\n    pytest\n")
    stub(tools, "just", '[ "$1" = "--summary" ] && echo "test build" && exit 0\necho "just $*"')

    written = collect(repo, repo / ".deep-review")

    assert {path.name for path in written} == {"just-test.txt"}, "there is no lint recipe to run"


def test_node_steps_come_from_the_scripts_package_json_declares(repo: Path, tools: Path) -> None:
    write(repo, "package.json", json.dumps({"scripts": {"lint": "eslint .", "test": "vitest"}}))
    stub(tools, "npm", 'echo "npm $*"')

    written = collect(repo, repo / ".deep-review")

    assert {path.name for path in written} == {"install.txt", "pkg-lint.txt", "pkg-test.txt"}


def test_rules_are_cloned_once_at_the_pinned_commit_and_reused(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pinned = _rules_source(tmp_path)
    monkeypatch.setattr(signal, "AST_GREP_RULES_REPO", str(source))
    monkeypatch.setattr(signal, "AST_GREP_RULES_COMMIT", pinned)

    rules = ast_grep_rules()

    assert rules == cache
    assert (cache / "sgconfig.yml").read_text(encoding="utf-8") == "pinned\n", (
        "the clone followed the branch instead of the pin"
    )
    marker = cache / "cloned-once"
    marker.touch()

    assert ast_grep_rules() == cache
    assert marker.exists(), "the rules were cloned again instead of reused from cache"


def test_the_rules_override_is_honoured_and_skips_the_clone(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = tmp_path / "own-rules"
    own.mkdir()
    (own / "sgconfig.yml").write_text("ruleDirs: []\n", encoding="utf-8")
    monkeypatch.setenv(signal.AST_GREP_RULES_ENV, str(own))

    assert ast_grep_rules() == own
    assert not cache.exists(), "the pinned pack was cloned although the override said where to look"


def test_ast_grep_scans_with_the_rules_it_was_pointed_at(
    repo: Path, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = tmp_path / "own-rules"
    own.mkdir()
    (own / "sgconfig.yml").write_text("ruleDirs: []\n", encoding="utf-8")
    monkeypatch.setenv(signal.AST_GREP_RULES_ENV, str(own))
    stub(tools, "ast-grep", 'echo "$*"')

    collect(repo, repo / ".deep-review")

    scan = (signal_dir(repo) / "ast-grep.txt").read_text(encoding="utf-8")
    assert f"-c {own / 'sgconfig.yml'}" in scan


def _rules_source(tmp_path: Path) -> tuple[Path, str]:
    """
    A local stand-in for the rule pack, and the commit to pin to. It has a second commit on top,
    so a clone that followed the branch instead of the pin brings back the wrong sgconfig.yml.
    Fetching a bare SHA needs the server's permission, which GitHub grants and a fresh local
    repository does not.
    """
    source = tmp_path / "rules-source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    git(source, "config", "uploadpack.allowAnySHA1InWant", "true")
    (source / "sgconfig.yml").write_text("pinned\n", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "rules at the pin")
    pinned = git(source, "rev-parse", "HEAD")
    (source / "sgconfig.yml").write_text("later\n", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "rules after the pin")
    return source, pinned


def test_a_failed_clone_is_not_retried_by_every_run_that_follows(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pinned = _rules_source(tmp_path)
    monkeypatch.setattr(signal, "AST_GREP_RULES_REPO", str(tmp_path / "nowhere"))
    monkeypatch.setattr(signal, "AST_GREP_RULES_COMMIT", pinned)

    assert ast_grep_rules() is None, "there is nothing at that URL to clone"

    tried = 0
    reach_out = signal._ask

    def counted(
        argv: tuple[str, ...], timeout_seconds: float, cwd: Path | None = None
    ) -> str | None:
        nonlocal tried
        tried += 1
        return reach_out(argv, timeout_seconds, cwd)

    monkeypatch.setattr(signal, "_ask", counted)

    assert ast_grep_rules() is None
    assert tried == 0, "an unreachable remote is paid for again on every Run that follows"

    # The mark ages out on its own, so a remote that comes back needs no cleanup from anyone.
    monkeypatch.setattr(signal, "CLONE_RETRY_AFTER_SECONDS", 0.0)
    monkeypatch.setattr(signal, "AST_GREP_RULES_REPO", str(source))

    assert ast_grep_rules() == cache
    assert tried > 0


def test_a_tool_that_leaves_a_child_holding_the_pipe_does_not_hold_up_the_run(
    repo: Path, tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signal, "STEP_TIMEOUT_SECONDS", 30.0)
    write(repo, "go.mod", "module example.com/app\n")
    stub(tools, "go", "echo done\nsleep 20 &\nexit 0")

    started = time.monotonic()
    collect(repo, repo / ".deep-review")

    assert time.monotonic() - started < 10, "the Run waited on a pipe nobody was going to close"
    vet = (signal_dir(repo) / "go-vet.txt").read_text(encoding="utf-8")
    assert "done" in vet
    assert vet.endswith("[exit 0]\n"), "the tool's own exit code is what gets reported"
