---
name: pre-pr-review
description: Local, pre-PR review of the current branch by a Reviewer of a different model family, run through the deep-review CLI, with a Verifier pass and fixes before the push. Use this whenever the work on a branch is about to leave the machine, even if the user does not ask for a review by name. That means before pushing, before opening a pull request, when the user says the feature is done or asks whether it is ready to ship, after a code review pass, and whenever the user asks to review this branch, check the change, get a second opinion, or run deep-review. Do not use it to review a pull request someone else opened, or a single file in isolation; it reviews the whole change on the current branch.
---

# pre-pr-review

This Skill drives the `deep-review` CLI. The CLI runs a Reviewer of a different model family from
you, on purpose: a second family does not share your blind spots about code you just wrote. The
Reviewer works inside this checkout, grepping callers, reading whole files and running the tests,
and it reports only defects it proved with quoted evidence and a failure scenario. The heavier
review of the same change runs in Actions once the PR is up, so this loop's job is to catch what
it can cheaply, before the PR exists.

Your job is the Round loop below: request a Run, have the Findings verified, fix what is real,
and carry on with the push or PR the user asked for. The CLI does the work.

If the CLI is not on PATH, install it and the Reviewer with one command:
`curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh`. If the
CLI's preflight still refuses, run `deep-review setup` and relay its table to the user. The one thing
it cannot fix is the Z.AI key: ask the user to configure it in the plugin or export it in their
shell, and never ask them to paste the key into the chat.

## The Round loop

1. **Run.** In the repo root, run `deep-review review`. A Run takes a few minutes, because the
   Reviewer actually executes things; wait for it rather than assuming it hung. It reviews the
   change against the merge-base with `origin/main`, so pass `--base <ref>` when the branch comes
   off something else. Read the table it prints: one line per Finding, the count, and the path of
   the Report.

   It is **never `--json`**. That flag prints the whole Report, and the whole Report landing in
   your context window is the thing this loop exists to avoid: the evidence is read by the
   Verifier, in its own window. You need the count and the path, nothing more.

   No Findings means proceed. A `failed` or `skipped` status is not clean: say so to the user and
   proceed anyway, because a Reviewer that could not run is not evidence about the code.

2. **Verify.** Spawn one read-only subagent with the Verifier prompt below. It reads the Report at
   `.deep-review/findings.json` and the Diff at `.deep-review/diff.patch`, both written by the Run,
   and returns one line per Finding: `real`, `not-real` or `uncertain`, with a one-line reason. It
   edits nothing; you apply the fixes, so the decision and the change stay in separate windows.

3. **Fix.** Fix every Finding the Verifier called real. Decide the uncertain ones yourself:
   **Uncertain counts as real for P0 and P1**, and only a P2 may be dropped on judgment alone,
   because a data-loss or wrong-behaviour Finding that nobody disproved is a real risk to ship. For
   every Finding you reject, tell the user why, quoting that Finding's own evidence, so the user can
   overrule you with the same facts in front of them.

4. **Again, once.** A task gets at most **two Rounds**: Run, verify, fix, Run, verify, fix, proceed.
   There is no third confirming Run: each one costs minutes and credits, and the PR-mode Run in
   Actions is the confirming pass. After the second Round, proceed regardless and report what is
   left with its evidence.

The Run's directory, `.deep-review/`, belongs to the CLI: never edit anything under it by hand and
never commit it. The CLI keeps it out of the index.

### What a Run prints

```
P1  retries.py:1-2  Retry count is off by one
P2  app.py:7        The new branch has no test

Adds retry handling. Two data-loss paths on the main flow.

findings  2 (P1 1, P2 1)
score     2/5 (advisory)
reviewer  opencode zai-coding-plan/glm-5.3 in 4m12s
report    .deep-review/findings.json
```

The score is advisory. The Findings are what you act on.

## What to tell the user

When the loop ends, report in this shape, briefly:

- Rounds run, and the Finding count each Run returned.
- Findings fixed, one line each.
- Findings rejected, each with the Verifier's reason or your own, quoting the Finding's evidence.
- Findings left after the second Round, if any, with their evidence.

## The Verifier prompt

Spawn a generic read-only subagent with this prompt, as written.

```
You are the Verifier for a code review. A Reviewer of a different model family has reported
Findings against the change in `.deep-review/diff.patch`; the Report is at
`.deep-review/findings.json`. Read both. For each Finding, decide whether the defect is real.

You are the same model family as the agent that wrote this code, judging that code. The cheapest
route to "clean" is to call everything not-real, so dismissal takes the same standard as the
Finding: to return `not-real` you must quote the code or run something showing the Finding's
failure scenario cannot occur. "I looked and it seems fine" is `uncertain`, not `not-real`.

You may read any file and run any command that does not modify tracked files. Do not edit code.

Output exactly one line per Finding, in the Report's order, and nothing else:

<index>. <file>:<line> <real|not-real|uncertain> — <one-line reason, quoting or citing what you ran>
```
