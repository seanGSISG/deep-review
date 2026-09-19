"""Throwaway git checkouts to point the Local mode helpers at."""

import subprocess
from pathlib import Path

import pytest


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
