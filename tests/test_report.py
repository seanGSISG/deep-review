"""Validating the Reviewer's findings file into a Report."""

import json
from pathlib import Path

from deep_review.report import Report, RunStats, read_findings, serialise

STATS = RunStats(agent="opencode", model="zai-coding-plan/glm-5.3")

FINDING = {
    "file": "src/shipments.py",
    "line": 42,
    "end_line": 45,
    "severity": "P1",
    "title": "Retry count is off by one",
    "evidence": "for attempt in range(MAX_RETRIES - 1):",
    "failure_scenario": "Given MAX_RETRIES=3, only two attempts are made.",
    "fix": "for attempt in range(MAX_RETRIES):",
    "source": "agent",
}


def write_findings(tmp_path: Path, payload: object) -> Path:
    """Write a findings file the way the Reviewer would, and return its path."""
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_well_formed_file_becomes_an_ok_report(tmp_path: Path) -> None:
    path = write_findings(
        tmp_path,
        {
            "summary": "Adds ETA retries.\n",
            "score": 2,
            "verified_by_execution": ["pytest -q: 1 failed"],
            "findings": [FINDING],
        },
    )

    report = read_findings(path, STATS)

    assert report.status == "ok"
    assert report.summary == "Adds ETA retries."
    assert report.score == 2
    assert report.verified_by_execution == ["pytest -q: 1 failed"]
    assert report.notice == ""
    assert [(found.severity, found.file, found.line) for found in report.findings] == [
        ("P1", "src/shipments.py", 42)
    ]


def test_findings_are_reported_worst_first(tmp_path: Path) -> None:
    severities = ["P2", "P0", "P1"]
    path = write_findings(
        tmp_path, {"findings": [{**FINDING, "severity": level} for level in severities]}
    )

    report = read_findings(path, STATS)

    assert [found.severity for found in report.findings] == ["P0", "P1", "P2"]


def test_a_malformed_finding_is_dropped_and_the_rest_survive(tmp_path: Path) -> None:
    path = write_findings(
        tmp_path,
        {
            "findings": [
                {**FINDING, "severity": "critical"},
                {**FINDING, "evidence": ""},
                {key: value for key, value in FINDING.items() if key != "failure_scenario"},
                "not a finding at all",
                FINDING,
            ]
        },
    )

    report = read_findings(path, STATS)

    assert report.status == "ok"
    assert len(report.findings) == 1
    assert report.notice.count("dropped finding") == 4
    assert "severity" in report.notice
    assert "evidence" in report.notice
    assert "failure_scenario" in report.notice
    assert "src/shipments.py:42" in report.notice


def test_optional_fields_may_be_missing(tmp_path: Path) -> None:
    required = {key: FINDING[key] for key in ("file", "line", "severity", "title", "evidence")}
    path = write_findings(tmp_path, {"findings": [{**required, "failure_scenario": "It breaks."}]})

    report = read_findings(path, STATS)

    found = report.findings[0]
    assert (found.end_line, found.fix) == (None, None)
    assert found.source == "agent"
    assert report.notice == ""


def test_a_score_outside_zero_to_five_is_dropped(tmp_path: Path) -> None:
    report = read_findings(write_findings(tmp_path, {"score": 9}), STATS)

    assert report.status == "ok"
    assert report.score is None
    assert "9" in report.notice


def test_a_missing_findings_file_is_a_failed_run(tmp_path: Path) -> None:
    report = read_findings(tmp_path / "findings.json", STATS)

    assert report.status == "failed"
    assert report.findings == []
    assert "no findings.json" in report.notice


def test_a_truncated_findings_file_is_a_failed_run(tmp_path: Path) -> None:
    path = tmp_path / "findings.json"
    path.write_text('{"summary": "half a', encoding="utf-8")

    report = read_findings(path, STATS)

    assert report.status == "failed"
    assert "not valid JSON" in report.notice


def test_a_findings_file_that_is_not_an_object_is_a_failed_run(tmp_path: Path) -> None:
    report = read_findings(write_findings(tmp_path, [FINDING]), STATS)

    assert report.status == "failed"
    assert "not a JSON object" in report.notice


def test_a_notice_is_added_to_rather_than_replaced() -> None:
    report = Report(status="ok", notice="dropped finding 1", stats=STATS)

    noted = report.noting("", "the Reviewer exited 1")

    assert noted.notice == "dropped finding 1; the Reviewer exited 1"
    assert report.noting("").notice == "dropped finding 1"
    assert Report(status="ok", stats=STATS).noting("only this").notice == "only this"


def test_the_serialised_report_round_trips() -> None:
    report = Report(status="skipped", notice="too big", stats=STATS)

    assert Report.model_validate_json(serialise(report)) == report


def test_a_fractional_score_is_dropped_rather_than_rounded(tmp_path: Path) -> None:
    assert read_findings(write_findings(tmp_path, {"score": 4.0}), STATS).score == 4

    report = read_findings(write_findings(tmp_path, {"score": 4.7}), STATS)

    assert report.score is None
    assert "not a whole number" in report.notice
