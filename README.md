# deep-review

A second-opinion PR reviewer that runs **inside the GitHub Actions runner** with an agent CLI
([opencode](https://opencode.ai)) on **GLM-5.3 via the Z.AI coding plan**. Unlike diff-only
reviewers it can grep callers across the repo, read whole files, run the project's tests and
linters, and it must ground every finding in quoted lines and a failure scenario before posting.

It complements, not replaces, the stock PR-Agent deployment (`/describe`, `/review`, `/improve`).

## How a run works
1. Checkout the PR head; write `.deep-review/diff.patch` and `.deep-review/pr.md`.
2. Signal collection (`deep_review.signal`): run the repo's own `just test|lint` / package scripts /
   ruff+pytest / go vet+test / cargo, plus gitleaks and ast-grep with CodeRabbit's essentials rules.
   Each tool gets its own time cap (`--signal-timeout`) and keeps the tail of its output; a missing
   tool is a skip, and none of them can fail the run. Output lands in `.deep-review/signal/` as
   *hypotheses* for the agent, never as findings on their own.
3. `prompts/review.md`: scope → investigate (grep, read, run) → judge. The agent runs with its
   own instruction files, skills, plugins and MCP servers switched off, so what it was told is the
   review prompt rather than whatever the machine or the branch carries (ADR-0002); it keeps
   `bash` and the rest of its toolset, because a finding it cannot execute is not a finding. It
   writes `.deep-review/findings.json` (P0/P1/P2, evidence, failure scenario, optional fix,
   0-5 score).
4. `scripts/post_review.py`: deletes the previous run's inline comments, posts one review with
   inline comments (GitHub suggestion blocks when a fix is given) and upserts a summary comment.
5. `.deep-review/` is uploaded as a workflow artifact for debugging.

## Install
One command, in a terminal. It installs uv if it is missing, the CLI from its tagged release, then
runs `deep-review setup --yes`, which installs the Reviewer (`opencode`, a standalone binary), asks
for your Z.AI coding-plan key with the input hidden, and links the Skill for any other coding agent
on the machine:
```sh
curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh
```
The key is stored readable by you alone, in `~/.config/deep-review/zai-api-key`; exporting
`Z_AI_API_KEY` in your shell overrides it, and a coding agent is never the one to ask for it. Run
`deep-review setup` any time to see what is missing and fix what it can. A coding-plan key comes
from https://z.ai. Linux and macOS; Windows needs Git Bash for the opencode installer and is untested.

**Claude Code** additionally gets the Skill as a plugin, which also says at session start when the
CLI, the Reviewer or the key is missing:
```
/plugin marketplace add seanGSISG/claude-depot
/plugin install pre-pr-review@claude-depot
```

## Use it locally, before the PR exists
```sh
deep-review review            # table of Findings; --json for the whole Report
```
The `pre-pr-review` skill teaches a coding agent the loop around it: run, verify the Findings in a
read-only subagent, fix what is real, at most twice, then push. Claude Code loads it from the
plugin; opencode and pi read it from their own skill directories, which `setup` links (or
`deep-review install-skill --target claude|pi|all`, `--force` to replace what is there).

## Use it in a repo
```sh
cp templates/caller.yml <repo>/.github/workflows/deep-review.yml
gh secret set ZAI_API_KEY --repo <owner>/<repo>
```
Fork PRs and drafts are skipped. Re-pushes cancel the in-flight run and replace the comments.

## Tuning
- `with.agent`: `opencode` (default) or `pi`. `with.model`: defaults to `zai-coding-plan/glm-5.3` (opencode) or `zai/glm-5.3` (pi); use the `-flash` variants for cheaper runs.
- `with.variant`: reasoning effort (`high`, `max`, `minimal`), provider-specific.
- `.github/review-learnings.md` in the reviewed repo: "do not flag" / "always check" rules the
  prompt obeys.

## Cost
Model calls bill Z.AI coding-plan credits (Pro: 12k per 5 h, 60k per week); an agentic review is
a fraction of a percent of a week. Actions minutes: free on public repos, 2,000-3,000/month on private.
