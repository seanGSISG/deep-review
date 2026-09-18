# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Post the deep-review findings to a pull request.

Reads `.deep-review/findings.json` and `.deep-review/diff.patch`, then:
1. deletes this tool's inline comments from earlier runs (found by marker),
2. posts one review (event COMMENT) with an inline comment per finding whose line is
   inside the diff, using a GitHub suggestion block when a fix is given,
3. upserts one summary comment (found by marker) with the score, the findings table
   (including findings that fell outside the diff) and the run footer.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, HEAD_SHA, RUN_URL, MODEL, ELAPSED_SECONDS.
Exit 0 always after posting; a failure to post any single inline comment is logged, not fatal.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"
MARK_SUMMARY = "<!-- deep-review:summary -->"
MARK_INLINE = "<!-- deep-review:finding -->"
SEVERITY_ICON = {"P0": "🔴 P0", "P1": "🟠 P1", "P2": "🟡 P2"}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    end_line: int
    severity: str
    title: str
    evidence: str
    failure_scenario: str
    fix: str
    source: str

    @classmethod
    def parse(cls, raw: dict) -> "Finding":
        line = int(raw.get("line") or 0)
        end = int(raw.get("end_line") or line)
        sev = str(raw.get("severity", "P2")).upper()
        return cls(
            file=str(raw.get("file", "")).lstrip("./"),
            line=line,
            end_line=max(end, line),
            severity=sev if sev in SEVERITY_ICON else "P2",
            title=str(raw.get("title", "")).strip(),
            evidence=str(raw.get("evidence", "")).strip(),
            failure_scenario=str(raw.get("failure_scenario", "")).strip(),
            fix=str(raw.get("fix", "") or "").rstrip(),
            source=str(raw.get("source", "agent")).strip() or "agent",
        )


def gh(method: str, path: str, body: dict | None = None) -> object:
    """Minimal GitHub REST call; raises urllib.error.HTTPError on 4xx/5xx."""
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
        return json.loads(data) if data else None


def gh_paged(path: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        chunk = gh("GET", f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        if not chunk:
            return out
        out.extend(chunk)
        page += 1


def commentable_lines(patch: str) -> dict[str, set[int]]:
    """Map file -> new-side line numbers that appear in the diff (added or context)."""
    lines: dict[str, set[int]] = {}
    current: str | None = None
    new_ln = 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].strip()
            current = current[2:] if current.startswith("b/") else current
            lines.setdefault(current, set())
        elif raw.startswith("@@") and current is not None:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw)
            new_ln = int(m.group(1)) if m else 0
        elif current is not None and raw and not raw.startswith("---"):
            if raw[0] == "+" or raw[0] == " ":
                lines[current].add(new_ln)
                new_ln += 1
            elif raw[0] == "\\":
                pass  # "\ No newline at end of file"
    return lines


def inline_body(f: Finding) -> str:
    parts = [MARK_INLINE, f"**{SEVERITY_ICON[f.severity]} · {f.title}**", "", f.failure_scenario]
    if f.evidence:
        parts += ["", f"<details><summary>Evidence</summary>\n\n{f.evidence}\n\n</details>"]
    if f.fix:
        parts += ["", "```suggestion", f.fix, "```"]
    if f.source != "agent":
        parts += ["", f"_Confirmed from `{f.source}` output._"]
    return "\n".join(parts)


def summary_body(data: dict, findings: list[Finding], outside: list[Finding], footer: str) -> str:
    score = int(data.get("score", 0))
    stars = "★" * score + "☆" * (5 - score)
    rows = ["| Sev | File | Finding |", "|---|---|---|"]
    for f in sorted(findings, key=lambda x: (x.severity, x.file, x.line)):
        loc = f"`{f.file}:{f.line}`"
        rows.append(f"| {SEVERITY_ICON[f.severity]} | {loc} | {f.title} |")
    table = "\n".join(rows) if findings else "_No grounded defects found._"
    parts = [MARK_SUMMARY, "## Deep review", "", data.get("summary", "").strip(), "",
             f"**Mergeability: {score}/5** {stars}", "", table]
    if outside:
        parts += ["", "<details><summary>Findings outside the diff (not inline-commentable)</summary>", ""]
        for f in outside:
            parts += [f"- {SEVERITY_ICON[f.severity]} `{f.file}:{f.line}` **{f.title}**: {f.failure_scenario}"]
        parts += ["", "</details>"]
    runs = data.get("verified_by_execution") or []
    if runs:
        parts += ["", "<details><summary>Verified by execution</summary>", ""]
        parts += [f"- {r}" for r in runs]
        parts += ["", "</details>"]
    parts += ["", footer]
    return "\n".join(parts)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr = int(os.environ["PR_NUMBER"])
    head = os.environ["HEAD_SHA"]
    root = Path(".deep-review")
    findings_path = root / "findings.json"
    if not findings_path.exists():
        print("no findings.json; posting failure notice", file=sys.stderr)
        data = {"summary": "Deep review did not produce findings (agent run failed or timed out).", "score": 0,
                "findings": [], "verified_by_execution": []}
    else:
        data = json.loads(findings_path.read_text())
    findings = [Finding.parse(f) for f in data.get("findings", []) if f.get("file")]
    patch = (root / "diff.patch").read_text() if (root / "diff.patch").exists() else ""
    ok_lines = commentable_lines(patch)
    inline = [f for f in findings if f.line in ok_lines.get(f.file, set())]
    outside = [f for f in findings if f not in inline]

    # 1. remove this tool's inline comments from earlier runs
    for c in gh_paged(f"/repos/{repo}/pulls/{pr}/comments"):
        if MARK_INLINE in (c.get("body") or ""):
            try:
                gh("DELETE", f"/repos/{repo}/pulls/comments/{c['id']}")
            except urllib.error.HTTPError as e:
                print(f"could not delete comment {c['id']}: {e}", file=sys.stderr)

    # 2. one review with inline comments
    comments = []
    for f in inline:
        c: dict = {"path": f.file, "line": f.line, "side": "RIGHT", "body": inline_body(f)}
        if f.end_line > f.line and f.end_line in ok_lines.get(f.file, set()):
            c.update({"start_line": f.line, "start_side": "RIGHT", "line": f.end_line})
        comments.append(c)
    if comments:
        try:
            gh("POST", f"/repos/{repo}/pulls/{pr}/reviews",
               {"commit_id": head, "event": "COMMENT", "body": f"{MARK_INLINE}Deep review: {len(comments)} inline finding(s).",
                "comments": comments})
        except urllib.error.HTTPError as e:
            print(f"review post failed ({e}); falling back to summary-only", file=sys.stderr)
            outside = findings
            inline = []

    # 3. upsert the summary comment
    footer = (f"<sub>{os.environ.get('MODEL', '?')} · {os.environ.get('ELAPSED_SECONDS', '?')}s · "
              f"head `{head[:8]}` · [run]({os.environ.get('RUN_URL', '')})</sub>")
    body = summary_body(data, inline, outside, footer)
    existing = next((c for c in gh_paged(f"/repos/{repo}/issues/{pr}/comments") if MARK_SUMMARY in (c.get("body") or "")), None)
    if existing:
        gh("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}", {"body": body})
    else:
        gh("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
    print(f"posted {len(inline)} inline, {len(outside)} summary-only, score {data.get('score')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
