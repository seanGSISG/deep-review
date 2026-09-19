"""One Run: the Run's inputs in, the Reviewer, exactly one Report out."""

from dataclasses import dataclass
from pathlib import Path

from deep_review.git import RUN_DIR_NAME, Base, Diff
from deep_review.local import write_run_inputs
from deep_review.report import Report, RunStats, read_findings, serialise
from deep_review.reviewer import EVENTS_NAME, FINDINGS_NAME, STDERR_NAME, Outcome, Reviewer, invoke
from deep_review.signal import collect


@dataclass(frozen=True, slots=True)
class RunOptions:
    """The knobs `deep-review review` exposes, resolved from the command line."""

    reviewer: Reviewer
    model: str
    variant: str | None
    timeout_seconds: float
    signal_timeout_seconds: float
    max_diff_lines: int


def execute(
    repo: Path, base: Base, diff: Diff, description: str, options: RunOptions
) -> tuple[Report, Path | None]:
    """
    Run the pipeline once against one Diff, and say where the Report landed — nowhere, when there
    was nothing to review and the Run left the checkout alone. Nothing here raises for a Run that
    went wrong: a Reviewer that crashed, timed out or was never started is a Report with a status
    and a notice, because the Coding agent needs to hear what happened and a Report is how a Run
    says it.
    """
    stats = RunStats(agent=options.reviewer.name, model=options.model, variant=options.variant)
    if not diff.text.strip():
        # An earlier Diff's Report must not outlive it. The Skill hands the Verifier whatever
        # findings file is in the checkout, so one describing a change that is gone is worse
        # than none at all. Nothing is created here: there is nothing to review.
        (repo / RUN_DIR_NAME / FINDINGS_NAME).unlink(missing_ok=True)
        return Report(
            status="skipped",
            notice=f"nothing changed against {base.ref} ({base.sha[:8]})",
            stats=stats,
        ), None
    inputs = write_run_inputs(repo, base, diff, description)
    findings_path = inputs.run_dir / FINDINGS_NAME
    if diff.changed_lines > options.max_diff_lines:
        return _persist(
            Report(
                status="skipped",
                notice=(
                    f"the Diff changes {diff.changed_lines} lines, over the --max-diff-lines "
                    f"limit of {options.max_diff_lines}; the Reviewer did not run"
                ),
                stats=stats,
            ),
            findings_path,
        )
    # The previous Run's findings file has to go: a Reviewer that dies before writing one would
    # otherwise leave the last Run's Report looking like this one's.
    findings_path.unlink(missing_ok=True)
    # Hypotheses for the Reviewer to open on. Nothing collected here can fail the Run, so there is
    # nothing to check: a tool that is missing or broken just leaves the Reviewer a colder repo.
    collect(repo, inputs.run_dir, timeout_seconds=options.signal_timeout_seconds)
    outcome = invoke(
        options.reviewer,
        repo=repo,
        run_dir=inputs.run_dir,
        prompt=inputs.prompt_path.read_text(encoding="utf-8"),
        model=options.model,
        variant=options.variant,
        timeout_seconds=options.timeout_seconds,
    )
    stats = stats.model_copy(update={"seconds": round(outcome.seconds, 1)})
    stats = options.reviewer.read_stats(inputs.run_dir / EVENTS_NAME, stats)
    report = read_findings(findings_path, stats)
    # A Reviewer that crashed or ran out of time leaves a failed Run even when a findings file
    # survived it, because nothing proves that file is the whole Report. Whatever Findings it did
    # hold are kept and reported: a Run that died is worth less than a clean one, not nothing.
    if ending := _ending(outcome, options.timeout_seconds):
        report = report.model_copy(update={"status": "failed"}).noting(ending)
    return _persist(report, findings_path)


def _ending(outcome: Outcome, timeout_seconds: float) -> str:
    """
    What to say about how the Reviewer ended. A clean exit needs nothing said; anything else is
    said even when the findings file survived it, so a broken Run never looks like a healthy one.
    """
    if outcome.timed_out:
        return f"the Reviewer was killed on the {timeout_seconds / 60:g} minute timeout"
    if outcome.exit_code != 0:
        return f"the Reviewer exited {outcome.exit_code}; see {STDERR_NAME}"
    return ""


def _persist(report: Report, findings_path: Path) -> tuple[Report, Path]:
    """Every Run leaves its whole Report on disk, so --json and the file are the same bytes."""
    findings_path.write_text(serialise(report), encoding="utf-8")
    return report, findings_path
