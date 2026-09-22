"""deep-review: a second-opinion pull-request reviewer."""

import os
from pathlib import Path

__version__ = "0.3.1"


class UsageError(Exception):
    """The caller asked for something the CLI cannot do. Reported on stderr, exit code 2."""


def cache_dir(*parts: str) -> Path:
    """
    A directory of ours under the user's cache, honouring XDG_CACHE_HOME and falling back to
    ~/.cache. Everything kept here is disposable: deleting it costs the next Run the work of
    fetching or rebuilding what was in it, and nothing else.
    """
    configured = os.environ.get("XDG_CACHE_HOME")
    root = Path(configured) if configured else Path.home() / ".cache"
    return root.joinpath("deep-review", *parts)
