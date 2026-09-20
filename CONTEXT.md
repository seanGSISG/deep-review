# deep-review

A second-opinion pull-request reviewer: a Reviewer of a different model family from the coding agent
reads a checked-out change, proves defects by execution and quotation, and reports them with a
mergeability score.

## Language

### Actors

**Reviewer**:
The agent CLI (opencode or pi) plus the model it drives, executing the review prompt against a checkout.
It runs with the CLI's own context loading switched off, so what it was told is the review prompt and
not the instruction files the machine or the branch had lying around — bar one vector opencode gives
no flag for, which #12 closes at our layer (ADR-0002).
_Avoid_: agent, the model, the bot

**Coding agent**:
The agent that writes code (Claude Code, opencode, Codex, Pi) and consumes the skill to request a Run.
_Avoid_: agent, the user's agent

**Verifier**:
The read-only subagent that adjudicates a Report's Findings before the Coding agent changes anything,
returning real, not-real or uncertain for each. It never edits code, and it exists so a Report's evidence
is read in its context window instead of the Coding agent's.
_Avoid_: reviewer (that is what produced the Findings), validator, checker, judge

### Execution

**Run**:
One execution of the pipeline against one Diff, producing exactly one Report.
_Avoid_: review (verb), job, session

**Round**:
One Run, plus the Verifier pass and the fixes that follow it. The Skill allows two Rounds per task,
then pushes.
_Avoid_: iteration, attempt, retry, pass

**Local mode**:
A Run inside the developer's own checkout, reviewing what is about to be pushed.

**PR mode**:
A Run against a GitHub pull request in a fresh temporary clone.

**Base**:
The commit the Diff is measured from: the PR's base SHA, or the merge-base with the default branch.

**Diff**:
The change under review: Base to head, plus uncommitted and untracked work in Local mode.
_Avoid_: patch, changes

**Size gate**:
The rule that skips the Reviewer when the Diff exceeds a line threshold.

### Evidence

**Signal**:
Deterministic tool output (tests, linters, secret and pattern scanners) collected before the Reviewer starts.
_Avoid_: pre-checks, lint results

**Hypothesis**:
A single item of Signal. It is not a Finding until the Reviewer proves it.

**Finding**:
A defect the Reviewer has proven, carrying quoted evidence and a failure scenario.
_Avoid_: issue, comment, hypothesis, warning

**Severity**:
A Finding's rank: P0, P1 or P2. Style-only observations are not Findings at any severity.

**Report**:
The complete output of a Run: summary, Score, what was verified by execution, Findings, and run stats.
_Avoid_: results, findings.json (that is its file, not the concept)

**Score**:
The Report's 0 to 5 mergeability rating, 5 meaning safe to merge.
_Avoid_: grade, rating

### Publishing

**Review**:
The single GitHub pull-request review object a Run posts, holding inline comments for Findings inside the Diff.
_Avoid_: using "review" for the Run or the CLI command

**Summary comment**:
The one pull-request comment a Run upserts, holding the Report's summary, Score, and Findings outside the Diff.

**Skill**:
The SKILL.md instructions a Coding agent loads to know when and how to request a Run.
