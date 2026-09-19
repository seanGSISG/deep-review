"""The Report a Run produces, and reading the Reviewer's findings file into one."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self, get_args

from pydantic import BaseModel, Field, ValidationError

Severity = Literal["P0", "P1", "P2"]

# Worst first: the order --fail-on compares against, and the order Findings are reported in.
SEVERITIES: tuple[Severity, ...] = get_args(Severity)

Status = Literal["ok", "skipped", "failed"]

# The Reviewer's own 0-5 mergeability rating. The CLI validates the range and does nothing else
# with it: --fail-on gates on Severity, never on this.
Score = Annotated[int, Field(ge=0, le=5)]

# A required field the Reviewer has to actually fill: an empty string is as good as missing.
NonEmpty = Annotated[str, Field(min_length=1)]


class Finding(BaseModel):
    """
    A defect the Reviewer proved, as it wrote it. The required fields *are* the proof: a Finding
    without a location, quoted evidence and a failure scenario has not earned a place in a Report,
    so it is dropped rather than shown.
    """

    file: str
    line: int
    severity: Severity
    title: NonEmpty
    evidence: NonEmpty
    failure_scenario: NonEmpty
    end_line: int | None = None
    fix: str | None = None
    source: str = "agent"


class RunStats(BaseModel):
    """What the Run cost: which Reviewer ran, for how long, and what it spent getting there."""

    agent: str
    model: str
    variant: str | None = None
    # None until the Reviewer has actually run, which a skipped Run never does.
    seconds: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    tool_calls: dict[str, int] = Field(default_factory=dict)


class Report(BaseModel):
    """
    The complete output of one Run. `status` says whether the Reviewer produced it (`ok`), never
    ran (`skipped`) or died trying (`failed`), and `notice` carries the reason in every case where
    something needs saying — the Size gate, a crash, a timeout, a dropped Finding.
    """

    status: Status
    summary: str = ""
    score: Score | None = None
    verified_by_execution: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    notice: str = ""
    stats: RunStats

    def noting(self, *additions: str) -> Self:
        """
        The same Report with more said in its notice. Nothing is ever replaced: a Reviewer that
        crashed after dropping a Finding has two things the caller needs to hear.
        """
        extra = [line for line in additions if line]
        if not extra:
            return self
        said = [self.notice, *extra] if self.notice else extra
        return self.model_copy(update={"notice": "; ".join(said)})


def serialise(report: Report) -> str:
    """The Report's bytes, as both `--json` and the findings file on disk carry them."""
    return report.model_dump_json(indent=2) + "\n"


def read_findings(path: Path, stats: RunStats) -> Report:
    """
    Validate the Reviewer's findings file into a Report. A malformed Finding is dropped with its
    reason in the notice and never costs the Report the others; the Reviewer wrote those in good
    faith. A file that is missing or is not the JSON object the prompt asked for leaves the Run
    `failed` — which is also what a Reviewer that died before writing one leaves behind.
    """
    if not path.exists():
        return Report(status="failed", notice=f"the Reviewer wrote no {path.name}", stats=stats)
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        return Report(
            status="failed", notice=f"{path.name} is not valid JSON: {error}", stats=stats
        )
    if not isinstance(raw, dict):
        return Report(status="failed", notice=f"{path.name} is not a JSON object", stats=stats)

    notices: list[str] = []
    return Report(
        status="ok",
        summary=str(raw.get("summary") or "").strip(),
        score=_score(raw.get("score"), notices),
        verified_by_execution=[
            str(item) for item in _listed(raw.get("verified_by_execution"), "verified", notices)
        ],
        findings=_findings(raw.get("findings"), notices),
        notice="; ".join(notices),
        stats=stats,
    )


def _score(value: object, notices: list[str]) -> int | None:
    """
    The Score, when the Reviewer gave a whole number inside 0-5. Anything else is dropped and
    noted rather than rounded or clamped: the Score is the Reviewer's own judgement, and the CLI
    validates its range and does nothing else with it.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        if value is not None:
            notices.append(f"ignored a score of {value!r}")
        return None
    if isinstance(value, float) and not value.is_integer():
        notices.append(f"ignored a score of {value!r}, not a whole number")
        return None
    if not 0 <= value <= 5:
        notices.append(f"ignored a score of {value!r}, outside 0-5")
        return None
    return int(value)


def _findings(value: object, notices: list[str]) -> list[Finding]:
    """The Findings that carry their proof, worst first. The rest are dropped with their reason."""
    kept = []
    for index, item in enumerate(_listed(value, "findings", notices), start=1):
        try:
            kept.append(Finding.model_validate(item))
        except ValidationError as error:
            notices.append(f"dropped finding {index} ({_located(item)}): {_reason(error)}")
    return sorted(
        kept, key=lambda found: (SEVERITIES.index(found.severity), found.file, found.line)
    )


def _listed(value: object, field: str, notices: list[str]) -> list[object]:
    """The list the prompt asked for, or nothing at all with a note saying what came instead."""
    if isinstance(value, list):
        return value
    if value is not None:
        notices.append(f"ignored a {field} value that was {type(value).__name__}, not a list")
    return []


def _located(item: object) -> str:
    """Enough of a dropped Finding to go looking for it in the event stream."""
    if not isinstance(item, dict):
        return f"a {type(item).__name__}, not an object"
    return f"{item.get('file', '?')}:{item.get('line', '?')}"


def _reason(error: ValidationError) -> str:
    """Why a Finding was dropped, in the words of the field that failed."""
    return ", ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or 'finding'}: {problem['msg'].lower()}"
        for problem in error.errors()
    )
