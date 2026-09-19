# MVP2: future improvements for deep-review

Backlog for the next iteration of the review stack, recorded 2026-09-18 after the first bake-off
(`docs/bakeoff-2026-09.md`). Ordered by leverage. Nothing here is started.

## 1. Scale: the one real weakness
Above roughly 10k diff lines a single agent spends its budget proving the system works rather than reading
every hunk, and misses diff-readable bugs a frontier model catches. On Winnow#16 (17k lines, 78 files) both
agents matched 0 of Copilot's 7 findings after 13 to 15 minutes of building, race-testing and probing the
live service.
- **Now:** gate the workflow at about 10k diff lines. Below it, deep review owns findings. Above it, skip the
  agent (post a one-line notice) and let PR-Agent's chunked `/review` cover the PR. Implement as a size check in
  the "Prepare review inputs" step plus a job output the later steps condition on.
- **Next — PARKED (2026-09-19).** Per-directory matrix split for large PRs: partition changed files by
  top-level directory (or ~3k diff lines), run one agent job per slice in parallel, merge the slices'
  `findings.json` (dedupe by file+line). Parked because the PR-time reviewer is now expected to become a
  per-file AST method (Kodus-shaped, see `PROMPT.md` "Two places a review happens"), and a per-file loop
  **is** this fix, done better: inherently partitioned, with no matrix, no merge step and no dedup logic.
  Revisit only if the PR tier stays a whole-repo agent.
- **Maybe — PARKED for the same reason.** A cheap diff-only first pass with `glm-5.3-flash` listing
  hypotheses per file. Per-file hypothesis generation is what a rule pass does in the Kodus shape.
- **Note:** the size gate matters much less in Local mode. A Local Run reviews one ticket's Diff, which
  will rarely approach 10k lines. Build the gate for PR mode; it is not a blocker for the local loop.

## 2. Credits
A large run costs 2 to 3 percent of the Pro plan's week (8 to 10 million mostly-cached tokens, most of it
opencode's ~63k-token system prompt repeated per step). Fine for a few big PRs a week, which argues against
running the deep review on everything **at PR time**.

This does **not** argue against the local loop, which runs two Runs per ticket. Local Runs review one
ticket's Diff: roughly 50k fresh + 500k cached each, so two Runs across ten tickets a week is about 10M
cached tokens — one large PR run, 2 to 3 percent of the week. The expensive case is a big PR, not a busy
week of small ones. Run volume is now driven by ticket count rather than PR count, which makes the credit
estimate in the footer (below) more useful, not less.
- Gate by size (above) and skip on `paths-ignore`-only changes (already in the caller template).
- Pi uses roughly half the tokens of opencode per run; consider it for large or low-risk PRs.
- Add the credit estimate (fresh + cached tokens from the event stream) to the summary footer so cost is visible
  per PR.

## 3. Minutes and per-repo setup
- Private repos burn Actions minutes (~4 min per run). Register a self-hosted runner on COLO for private repos
  and expose `runs-on` as a workflow input; keep public repos on GitHub-hosted runners.
- End state: one webhook service on COLO behind the existing GitHub App that runs this same pipeline
  (signal collection, prompt, poster) in a throwaway container. Removes both the per-repo workflow file and
  the minutes. Kodus is the buy-instead-of-build candidate for that slot; decide after its trial.

## 4. Quality
- Agent choice per PR: opencode had the best recall on known bugs; Pi found real extras nobody else reported.
  Consider running Pi on PRs opencode scores 5/5, as a cheap second opinion.
- Repo learnings: `.github/review-learnings.md` ("do not flag" / "always check") read by the prompt and by
  PR-Agent's `extra_instructions`. Manual to start; an embedding-based 👎 filter only if noise ever appears.
- Persist findings across pushes: carry the previous run's findings into the prompt so the summary can say
  "still open" versus "fixed" instead of re-deriving everything.
- Emit the PR description from the same run (title, summary, labels) so PR-Agent's `/describe` can retire too.

## 5. Housekeeping
- Bump PR-Agent to a release containing upstream #3072 and delete the `persistent_finding_state` override.
- Gate on fork PRs is done; also skip when the PR author is a bot (dependabot, renovate).
- Pin `actions/*` by SHA once the workflow stabilises.
