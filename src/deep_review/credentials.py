"""The Z.AI key, kept where the CLI can read it back without the shell having to carry it."""

import os
from pathlib import Path

# The file's name says what it holds, so a directory listing needs no explanation.
KEY_FILE = "zai-api-key"


def key_path() -> Path:
    """
    Where the key lives: $XDG_CONFIG_HOME/deep-review/zai-api-key, falling back to ~/.config.
    The same lookup the Run's own settings directories use, so one variable moves all of it.
    """
    configured = os.environ.get("XDG_CONFIG_HOME")
    root = Path(configured) if configured else Path.home() / ".config"
    return root / "deep-review" / KEY_FILE


def read_key() -> str | None:
    """The stored key, or None when nothing was stored or the file is empty."""
    try:
        value = key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def store_key(value: str) -> Path:
    """
    Write the key readable by this user alone: the directory 700, the file 600, and the mode set
    before the value lands in it, so no other user gets a window. Returns the path written.
    """
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value.strip() + "\n")
    path.chmod(0o600)
    return path
