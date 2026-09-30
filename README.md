# deep-review

A second-opinion reviewer for the change on your branch, run before it leaves the machine. The
Reviewer is [Codex](https://developers.openai.com/codex) (`codex exec`) on **gpt-6.1-sol at medium
effort**, the model argus runs on, using your own Codex login. Unlike diff-only reviewers it can
grep callers across the repo, read whole files and run the project's tests and linters, and it must
ground every finding in quoted lines and a failure scenario.

It complements, not replaces, argus (PR-Agent: `/describe`, `/review`, `/improve`) on the PR.

## How a run works
1. Write `.deep-review/diff.patch` (merge-base with `origin/main` to the working tree) and
   `.deep-review/pr.md`.
2. Signal collection (`deep_review.signal`): run the repo's own `just test|lint` / package scripts /
   ruff+pytest / go vet+test / cargo, plus gitleaks and ast-grep with CodeRabbit's essentials rules.
   Each tool gets its own time cap (`--signal-timeout`) and keeps the tail of its output; a missing
   tool is a skip, and none of them can fail the run. Output lands in `.deep-review/signal/` as
   *hypotheses* for the Reviewer, never as findings on their own.
3. `prompts/review.md`: scope → investigate (grep, read, run) → judge. Codex runs against a Codex
   home of ours holding only a link to your login, with AGENTS.md, skills, hooks and rules switched
   off, so what it was told is the review prompt rather than whatever the machine or the branch
   carries (ADR-0004). Its commands run in Codex's `workspace-write` sandbox: the checkout, `/tmp`
   and your cache are writable, `.git` is read-only and there is no network.
4. The Reviewer's final message is the Report, validated by `--output-schema` (P0/P1/P2, evidence,
   failure scenario, optional fix, 0-5 score), and lands in `.deep-review/findings.json`.

## Install
One command, in a terminal. It installs uv if it is missing and the CLI from its tagged release,
then runs `deep-review setup`, which checks Codex and its login and links the Skill for any other
coding agent on the machine:
```sh
curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh
```
The Reviewer needs Codex 0.159.2 or later (`npm install -g @openai/codex`, or `codex update`) and a
ChatGPT login in Codex's default file credential store (`codex login`). There is no key to manage: a
Run uses that login, and its token refreshes land in your own `~/.codex/auth.json`. Run
`deep-review setup` any time to see what is missing.

**Claude Code** gets the Skill as a plugin, which also says at session start when uv, Codex or its
login is missing:
```
/plugin marketplace add seanGSISG/claude-depot
/plugin install pre-pr-review@claude-depot
```
The Skill runs the CLI as `uvx --from git+https://github.com/seanGSISG/deep-review@v<release>
deep-review`, pinned to the release it shipped with, so updating the plugin updates the CLI and a
`deep-review` on PATH is never the one the Skill runs. A plugin user needs only uv and Codex; the
install script above is for running the CLI by hand and for linking the Skill into opencode or pi.

## Use it
```sh
deep-review review            # table of Findings; --json for the whole Report
deep-review review --effort high --model gpt-6-sol
```
The `pre-pr-review` skill teaches a coding agent the loop around it: run, verify the Findings in a
read-only subagent, fix what is real, at most twice, then push. Claude Code loads it from the
plugin; opencode and pi read it from their own skill directories, which `setup` links (or
`deep-review install-skill --target claude|pi|all`, `--force` to replace what is there).

`.github/review-learnings.md` in the reviewed repo holds "do not flag" / "always check" rules the
prompt obeys.

## Cost
A Run spends your ChatGPT plan's Codex usage, not metered API credit: the live test's planted-bug
Run took 35 s and ~64k tokens, 46k of them cached.
