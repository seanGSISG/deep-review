# deep-review

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `seanGSISG/deep-review`, driven via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary — the five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Checkpoints

Some tickets are decision points, not just work. At a checkpoint you **stop and ask Sean**. You do
not decide alone, and you do not push through a session that has stopped reasoning well.

- **Issue #2, the first complete Run**, is the tracer bullet this whole plan rests on. If it grows
  beyond one comfortable context window, that is the signal to split the Reviewer invocation off
  from the Report handling — **not** the signal to keep going. Splitting a ticket changes scope,
  and scope is Sean's call. Say what you have built, what is left, and where you would cut it.
- The same applies to any ticket: if you are compacting to finish one, you have already passed the
  point where you should have said something.

Surfacing a checkpoint is never a failure. Silently pushing past one is.
