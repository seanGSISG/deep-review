"""The Report as a terminal reads it."""

from collections import Counter
from pathlib import Path

from deep_review.report import Finding, Report, RunStats

# Width of the footer's label column.
LABEL_WIDTH = 10


def render(report: Report, findings_path: Path | None) -> str:
    """
    One line per Finding, the Reviewer's summary, then what the Run cost. This stays small on
    purpose: the Skill hands the Verifier the findings file so a Finding's evidence is read in the
    Verifier's context window, and what comes back to the Coding agent is the count and the path.
    """
    blocks = []
    if report.status != "ok":
        blocks.append(f"{report.status}: {report.notice or 'the Reviewer produced no Report'}")
    if report.findings:
        blocks.append(_table(report.findings))
    if report.summary:
        blocks.append(report.summary.strip())
    blocks.append(_footer(report, findings_path))
    return "\n\n".join(blocks)


def _table(findings: list[Finding]) -> str:
    """Severity, file:line and title, one Finding per line, worst first as the Report holds them."""
    rows = [(found.severity, _location(found), found.title.strip()) for found in findings]
    location = max(len(row[1]) for row in rows)
    return "\n".join(f"{row[0]}  {row[1]:<{location}}  {row[2]}" for row in rows)


def _location(found: Finding) -> str:
    """Where the Finding is, as a line the editor and the terminal both know how to open."""
    if found.end_line is not None and found.end_line != found.line:
        return f"{found.file}:{found.line}-{found.end_line}"
    return f"{found.file}:{found.line}"


def _footer(report: Report, findings_path: Path | None) -> str:
    """The counts, the advisory Score, what the Run cost, and where the whole Report landed."""
    lines = [_label("findings", _counts(report.findings))]
    if report.score is not None:
        lines.append(_label("score", f"{report.score}/5 (advisory)"))
    lines.append(_label("reviewer", _reviewer(report.stats)))
    if report.status == "ok" and report.notice:
        lines.append(_label("notice", report.notice))
    if findings_path is not None:
        lines.append(_label("report", str(findings_path)))
    return "\n".join(lines)


def _counts(findings: list[Finding]) -> str:
    """How many Findings, and how they break down by Severity."""
    if not findings:
        return "none"
    seen = Counter(found.severity for found in findings)
    return f"{len(findings)} ({', '.join(f'{level} {count}' for level, count in seen.items())})"


def _reviewer(stats: RunStats) -> str:
    """Which Reviewer ran, on what model, and how long it took."""
    line = f"{stats.agent} {stats.model}"
    if stats.variant:
        line += f" ({stats.variant})"
    return line if stats.seconds is None else f"{line} in {stats.seconds:g}s"


def _label(name: str, value: str) -> str:
    return f"{name:<{LABEL_WIDTH}}{value}"
