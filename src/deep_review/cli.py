"""The deep-review command line."""

import argparse
import sys
from pathlib import Path

from deep_review import UsageError, __version__
from deep_review.git import build_diff, repo_root, resolve_base
from deep_review.local import local_description, write_run_inputs


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the requested command, and return the process exit code."""
    args = _parser().parse_args(argv)
    try:
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
    return parser


def _review(args: argparse.Namespace) -> int:
    """
    Local mode: prepare the Run's inputs in the current checkout and say where they landed.
    An empty Diff is nothing to review, so it is skipped rather than treated as an error.
    """
    repo = repo_root(Path.cwd())
    base = resolve_base(repo, args.base)
    diff = build_diff(repo, base.sha)
    if not diff.strip():
        print(f"skipped: nothing changed against {base.ref} ({base.sha[:8]})")
        return 0
    inputs = write_run_inputs(repo, base, diff, local_description(repo, base))
    print(f"base    {base.sha[:8]} (merge-base with {base.ref})")
    print(f"diff    {inputs.diff_path.relative_to(repo)} ({inputs.diff_lines} lines)")
    print(f"pr      {inputs.description_path.relative_to(repo)}")
    print(f"prompt  {inputs.prompt_path.relative_to(repo)}")
    return 0
