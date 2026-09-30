"""The deep-review command line."""

import argparse
import sys
from pathlib import Path

from deep_review import UsageError, __version__
from deep_review.git import build_diff, repo_root, resolve_base
from deep_review.local import local_description
from deep_review.render import LABEL_WIDTH, render
from deep_review.report import SEVERITIES, Report, Severity, serialise
from deep_review.reviewer import DEFAULT_EFFORT, DEFAULT_MODEL, preflight
from deep_review.run import RunOptions, execute
from deep_review.setup import ready, setup
from deep_review.signal import STEP_TIMEOUT_SECONDS
from deep_review.skill import TARGETS, install

# Above this many changed lines the Reviewer collapses, so the Size gate skips it (MVP2 item 1).
MAX_DIFF_LINES = 10_000

# Minutes a Run gets before the Reviewer is killed.
TIMEOUT_MINUTES = 20.0

# Minutes each Signal tool gets before it is killed, taken from the collector's own default so
# the two cannot drift. Raise it for a repo whose test suite is slower than the Reviewer is.
SIGNAL_TIMEOUT_MINUTES = STEP_TIMEOUT_SECONDS / 60


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the requested command, and return the process exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "install-skill":
            return _install_skill(args)
        if args.command == "setup":
            return _setup()
        return _review(args)
    except UsageError as error:
        print(f"deep-review: {error}", file=sys.stderr)
        return 2


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deep-review", description="A second-opinion review of a change, by a Reviewer."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("review", help="review the current branch (Local mode)")
    review.add_argument(
        "--base",
        metavar="REF",
        help="measure the Diff from the merge-base with this ref, "
        "instead of origin/main, main or master",
    )
    review.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="ID",
        help="the Codex model the Reviewer runs (default: %(default)s)",
    )
    review.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        metavar="LEVEL",
        help="its reasoning effort, passed to Codex as model_reasoning_effort "
        "(default: %(default)s)",
    )
    review.add_argument(
        "--json", action="store_true", help="print the whole Report instead of the table"
    )
    review.add_argument(
        "--fail-on",
        choices=SEVERITIES,
        metavar="P0|P1|P2",
        help="exit 1 when a Finding is this severe or worse",
    )
    review.add_argument(
        "--max-diff-lines",
        type=int,
        default=MAX_DIFF_LINES,
        metavar="N",
        help="skip the Reviewer above this many added plus removed lines (default: %(default)s)",
    )
    review.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT_MINUTES,
        metavar="MINUTES",
        help="kill the Reviewer after this long (default: %(default)s)",
    )
    review.add_argument(
        "--signal-timeout",
        type=float,
        default=SIGNAL_TIMEOUT_MINUTES,
        metavar="MINUTES",
        help="kill each Signal tool after this long; raise it for a repo whose own test suite "
        "takes longer than this (default: %(default)s)",
    )
    install_skill = commands.add_parser(
        "install-skill", help="symlink the Skill into the Coding agents' skill directories"
    )
    install_skill.add_argument(
        "--target",
        choices=[*sorted(TARGETS), "all"],
        default="all",
        help="claude covers Claude Code and opencode via ~/.claude/skills; "
        "pi is ~/.pi/agent/skills (default: %(default)s)",
    )
    install_skill.add_argument(
        "--force",
        action="store_true",
        help="replace whatever is at the target path, if it is not already our symlink",
    )
    commands.add_parser(
        "setup", help="check this machine is ready for a Run: Codex, its login and the Skill"
    )
    return parser


def _setup() -> int:
    """Print the setup table; exit 2 while anything in it is still missing."""
    rows = setup()
    for row in rows:
        print(f"{row.name:<{LABEL_WIDTH}}{row.status:<{LABEL_WIDTH}}{row.detail}")
    return 0 if ready(rows) else 2


def _install_skill(args: argparse.Namespace) -> int:
    """Link the Skill into the chosen agents' skill directories and say where it went."""
    targets = sorted(TARGETS) if args.target == "all" else [args.target]
    for link in install(targets, args.force):
        print(f"{link} -> {link.resolve()}")
    return 0


def _review(args: argparse.Namespace) -> int:
    """
    Local mode: one Run in the current checkout, reported to the terminal. The Reviewer's own
    troubles never reach the exit code — only --fail-on and usage errors do.
    """
    repo = repo_root(Path.cwd())
    preflight()
    base = resolve_base(repo, args.base)
    options = RunOptions(
        model=args.model,
        effort=args.effort,
        timeout_seconds=args.timeout * 60,
        signal_timeout_seconds=args.signal_timeout * 60,
        max_diff_lines=args.max_diff_lines,
    )
    report, written = execute(
        repo, base, build_diff(repo, base.sha), local_description(repo, base), options
    )
    if args.json:
        print(serialise(report), end="")
    else:
        print(render(report, None if written is None else written.relative_to(repo)))
    return _exit_code(report, args.fail_on)


def _exit_code(report: Report, fail_on: Severity | None) -> int:
    """
    0 unless --fail-on matches a Finding's Severity, where P1 means P0 or P1. A Reviewer that
    crashed or a linter that failed never fails the CLI: the Report said so, and that is its job.
    """
    if fail_on is None:
        return 0
    threshold = SEVERITIES.index(fail_on)
    return (
        1 if any(SEVERITIES.index(found.severity) <= threshold for found in report.findings) else 0
    )
