"""deep-review: a second-opinion pull-request reviewer."""

__version__ = "0.1.0"


class UsageError(Exception):
    """The caller asked for something the CLI cannot do. Reported on stderr, exit code 2."""
