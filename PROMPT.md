# Task: turn deep-review into a CLI and an agent skill

You are starting with zero context. Read this whole file, then the files it names, before writing code.

## What deep-review is

deep-review is Sean's second-opinion pull-request reviewer. It runs an agent CLI (opencode, or Pi) with
GLM-5.3 on Sean's Z.AI coding-plan subscription against a checked-out PR. Unlike diff-only reviewers, the
agent can grep callers across the repo, read whole files, and run the project's tests and linters; the prompt
forces it to ground every finding in quoted lines plus a failure scenario, and to drop anything it cannot
prove. It posts one GitHub review with inline P0/P1/P2 findings and a 0-5 mergeability score.

Today it exists only as a GitHub Actions reusable workflow in this repo, plus a shell runner for local
bake-offs. It works and it is good: on a sandbox PR with five planted bugs it found all five plus three real
extras with zero noise in ~3.5 minutes, beating PR-Agent (3 of 5) and a self-hosted Kodus trial (4 of 5).
Details: `docs/bakeoff-2026-09.md`.

Its two costs come from living in Actions: it burns Actions minutes on private repos and needs a workflow
file plus a secret in every repo. The end state is one webhook service on COLO (Sean's VPS) that runs the
same pipeline for every repo. That service is **not** this task. This task builds the shared core it will
call: a **CLI**, and an **agent skill** so coding agents (Claude Code, opencode, Codex, Pi) can run a deep
review locally before pushing.

## Read these files first (all in this repo)

| File | Why |
|---|---|
| `prompts/review.md` | The review prompt. The `{{BASE_SHA}}` placeholder is substituted before the run. Keep the prompt's content; only the plumbing changes. |
| `scripts/collect_signal.sh` | Deterministic signal collection: the repo's own `just test|lint`, package.json scripts, ruff + pytest, go vet/test, cargo, plus gitleaks and ast-grep with CodeRabbit's `ast-grep-essentials` rules. Output goes to `.deep-review/signal/*.txt` as hypotheses for the agent. Every step is best-effort and time-capped. |
| `scripts/post_review.py` | Posts findings to GitHub: deletes the tool's previous inline comments (found by an HTML marker), posts one review with inline comments only for lines inside the diff (it parses the unified diff to know which new-side lines are commentable), and upserts a summary comment. Stdlib only, PEP 723 script. |
| `scripts/run_local.sh` | The local bake-off runner. It is 80% of the CLI already: resolve PR head/base via `gh`, clone, diff, prepare inputs, collect signal, run the agent (opencode or pi, selected by `AGENT=`), copy results, print token/tool stats parsed from the agent's JSON event stream. |
| `.github/workflows/deep-review.yml` | The reusable workflow: install pinned tool versions, prepare inputs, collect signal, run agent, post, upload artifact. After this task it should shrink to installing the CLI and running one command. |
| `templates/caller.yml` | What each reviewed repo adds to call the workflow. |
| `README.md`, `docs/MVP2.md` | Current usage and the backlog. MVP2 item 1 (size gate) and item 4 (agent choice) are in scope here; the matrix split and the webhook are not. |
| `docs/bakeoff-2026-09.md` | Results and, at the bottom, the pitfalls already hit. Read the pitfalls; they cost hours. |

Background on why this exists, if you want it: `/home/adminuser/.claude/plans/so-right-now-we-goofy-cloud.md`.

## Deliverable 1: the `deep-review` CLI

A Python package in this repo (`src/deep_review/`), installable with `uv tool install .` and runnable with
`uvx --from . deep-review`, exposing one console script `deep-review`.

### Commands

```
deep-review review [--pr OWNER/REPO#N | --base REF] [--agent opencode|pi] [--model ID]
                   [--variant LEVEL] [--post] [--json] [--fail-on P0|P1|P2]
                   [--max-diff-lines N] [--timeout MINUTES] [--out DIR]
deep-review post   [--pr OWNER/REPO#N] [--findings PATH]     # post an existing findings.json
deep-review install-skill [--target claude|opencode|all]     # see Deliverable 2
```

Behaviour of `review`:
- **Local mode** (`--base`, default): run inside any git checkout. Base defaults to the merge-base of HEAD
  with `origin/main` (fall back to `main`, then `master`). Diff = `git diff <base>...HEAD` plus, if the
  working tree is dirty, the uncommitted changes too (review what the developer is about to push).
- **PR mode** (`--pr`): resolve head and base SHAs with `gh` (already authenticated on this machine), clone
  into a temp dir, check out the head SHA, run, clean up. `--post` requires PR mode.
- Write inputs to `.deep-review/` in the checkout: `diff.patch`, `pr.md` (title + body, or the branch name
  and commit messages in local mode), `signal/`, `prompt.md` (prompt with `{{BASE_SHA}}` filled). Then run
  the agent, then read `.deep-review/findings.json`.
- **Size gate:** if the diff exceeds `--max-diff-lines` (default 10000) skip the agent, say so, and in PR
  mode with `--post` post a one-line notice instead of a review. This is MVP2 item 1.
- **Output:** always write `findings.json`; print a compact table (severity, file:line, title) and the
  summary, score, and run stats (agent, model, seconds, fresh/cached/output tokens, tool-call counts). With
  `--json` print the findings JSON to stdout instead of the table, so agents and scripts can consume it.
- **Exit code:** 0 by default. With `--fail-on P1`, exit 1 if any finding is P1 or worse. Never fail because
  a linter or the agent crashed; a failed agent run produces an empty findings list with a clear notice.

### Agent runners

Port the two invocations from `scripts/run_local.sh` exactly, including these hard-won details:
- opencode: `opencode run --format json --pure --dangerously-skip-permissions -m <model> [--variant X]
  "<prompt>"`, model default `zai-coding-plan/glm-5.3`, key from `ZHIPU_API_KEY`.
- pi: `pi -p --mode json --no-session --no-extensions --no-skills --no-prompt-templates --model <model>
  --thinking <level> "<prompt>"`, model default `zai/glm-5.3`, key from `ZAI_API_KEY`.
- Keep `--pure` (opencode) and `--no-skills --no-extensions --no-prompt-templates` (pi) on these reviewer
  invocations even after the skill exists: the reviewer must see only the review prompt, and loading the
  user's skills would let it invoke deep-review recursively. The skill is for the *coding* agent, which
  loads it through normal discovery or explicitly with `pi -p --skill <dir> "..."`.
- **stdin must be `/dev/null`** for both, or they block forever before the first model call when launched
  from anything that leaves stdin open (background shells, some CI). Use `subprocess.run(..., stdin=DEVNULL)`.
- Both take the prompt as a single argv element; never pipe it.
- Capture stdout to `.deep-review/agent-events.jsonl` and stderr to `agent.err`; enforce the timeout
  (default 20 minutes) and kill the process group on expiry.
- Token and tool stats: opencode emits `step_finish` events with `part.tokens.{input,output,reasoning,
  cache.read}` and `tool` events with `part.tool`; pi emits `message_end` events whose `message.usage` has
  `{input,output,cacheRead}` when `message.role == "assistant"`, and `tool_execution_start` events with
  `toolName`. `run_local.sh` has working parsers for both; reuse them.
- The Z.AI key on this machine is in the env var `Z_AI_API_KEY`; the CLI should accept that as a fallback
  and export the name each agent wants.

### Signal collection

Port `scripts/collect_signal.sh` to Python (typed, one function per tool, each with its own timeout and
"tool missing" handling) or keep shelling out to it; either is acceptable, but the behaviour must stay:
never fail the run, cap each tool's output, and write one file per tool under `.deep-review/signal/`.

### Posting

Move `scripts/post_review.py` into the package as the `post` command and the `--post` path. Keep its diff
parsing (only lines present in the diff are inline-commentable; the rest go into the summary), its
HTML-comment markers for idempotency, and its stdlib-only GitHub client. Token: `GITHUB_TOKEN`, falling
back to `gh auth token`.

### Data model

Findings and the run report are Pydantic models (Sean prefers Pydantic at boundaries): `Finding` with
`file, line, end_line, severity (P0|P1|P2), title, evidence, failure_scenario, fix, source`, and
`Report` with `summary, score (0-5), verified_by_execution, findings, run stats`. Validate the agent's
`findings.json` with them and tolerate missing optional fields.

## Deliverable 2: the agent skill

Coding agents already have a shell, so the skill is a `SKILL.md`, not an MCP server. Put it at
`skills/deep-review/SKILL.md` in this repo, following the Agent Skills format (YAML frontmatter with
`name` and a `description` that says when to trigger, then instructions; `name` must equal the directory
name and match `^[a-z0-9]+(-[a-z0-9]+)*$`). Verified load paths on this machine: Claude Code and opencode
both read `~/.claude/skills/<name>/SKILL.md` (opencode also reads `~/.config/opencode/skills/` and
`~/.agents/skills/`, per https://opencode.ai/docs/skills/); Pi reads `~/.pi/agent/skills/<name>/SKILL.md`.
`deep-review install-skill` symlinks the skill directory into `~/.claude/skills/` (covers Claude Code and
opencode) and `~/.pi/agent/skills/` (Pi); `--target` picks a subset.

The skill should tell the agent to:
1. Run `deep-review review --json` (local mode) before opening or updating a PR, or when asked for a
   review of the current branch.
2. Read the findings, fix P0/P1 items, decide on P2 items, and re-run until clean or until only findings
   it disagrees with remain, which it must then explain to the user with the finding's evidence.
3. Never edit `.deep-review/` by hand and never commit it (`.gitignore` entry).

Keep it short; the CLI does the work. Note in the skill that the reviewer is a different model family from
the one writing the code, on purpose.

## Deliverable 3: switch the workflow to the CLI

Change `.github/workflows/deep-review.yml` so the job installs the CLI (`uv tool install
git+https://github.com/seanGSISG/deep-review@main` or `uvx --from git+... deep-review`) plus the pinned
opencode/pi/ast-grep/gitleaks binaries, then runs
`deep-review review --pr "$REPO#$PR" --agent "$AGENT" --post`. Keep the artifact upload
(`include-hidden-files: true`). Delete `scripts/run_local.sh` and `scripts/post_review.py` once the CLI
replaces them; keep `scripts/collect_signal.sh` only if the CLI still shells out to it.

## Conventions (Sean's, non-negotiable)

- Python via **uv** only (never pip). `pyproject.toml` is the single source of truth. **Ruff** for lint and
  format, **ty** for type checking, **pytest** for tests. Add a `justfile` with `just test`, `just lint`,
  `just fmt`, `just review` (runs the CLI on the current branch).
- Type hints everywhere, modern syntax (`str | None`, `list[int]`), `pathlib.Path`, f-strings, no bare
  `except`, no mutable defaults, dataclasses or Pydantic for structured data.
- Keep it simple. No plugin systems, no config files beyond flags and env, no abstractions for one caller.
  A fix that shrinks the code beats one that grows it.
- Comments above functions and classes that say how they are used; keep comments in sync with the code.
- Tests are for logic, not ceremony: the diff-hunk line mapping, findings validation, the agent event
  parsers (use small fixture excerpts from real event streams; `run_local.sh` shows their shape), the
  size gate, and the summary markdown. One integration smoke test that runs the real agent, skipped unless
  `Z_AI_API_KEY` is set.
- Never commit secrets. `.deep-review/` is git-ignored.
- No Anthropic models anywhere in this tool (Sean's rule; the reviewer must be a different family from the
  coding agent, and the budget is the Z.AI subscription).

## Environment facts

- This machine (Spark, Ubuntu arm64) has `uv`, `gh` (logged in as seanGSISG), `opencode` 1.4.3 at
  `~/.bun/bin/opencode`, `pi` 0.80.10, `just`, and `Z_AI_API_KEY` in the environment. Z.AI's coding
  endpoint is `https://api.z.ai/api/coding/paas/v4`; models `glm-5.3`, `glm-5.3-flash`, `glm-5.3-flashx`.
- Pinned versions used in CI: opencode-ai 1.18.31 (npm; **do not** install with `--ignore-scripts`, its
  postinstall fetches the binary), @earendil-works/pi-coding-agent 0.85.1 (npm, `--ignore-scripts` is
  fine), @ast-grep/cli 0.45.3, gitleaks 8.30.1.
- Test repo: `seanGSISG/pr-review-sandbox` (private, throwaway, Python stdlib). **PR #2** has five planted
  bugs in `src/shipments.py`: a failed lookup early-returns and truncates the loop, the missing
  tracking-number guard was removed, the retry count is off by one, `save_state` lost its atomic write, and
  `page()` returns limit+1 items and coerces `limit=0` to 50. PR-Agent (a separate bot) and the Actions
  workflow already comment there; that is fine.
- Z.AI credits: a small-PR run is ~50k fresh + ~500k cached tokens; a large one ~10M cached. Sean is on the
  Pro plan, so a handful of test runs is nothing, but do not loop the integration test.

## Acceptance

1. `uvx --from . deep-review review --pr seanGSISG/pr-review-sandbox#2 --json` returns at least five
   findings including all five planted bugs above, in under six minutes, with zero style-only findings.
2. In a checkout of pr-review-sandbox on branch `feat/eta-retries-v2`, `deep-review review` (local mode,
   no flags) reviews the same diff against `main` and prints the table; `--fail-on P1` exits 1.
3. `deep-review review --pr seanGSISG/pr-review-sandbox#2 --post` replaces the previous run's inline
   comments and updates the summary comment in place (no duplicates after two runs).
4. The workflow on `pr-review-sandbox` (caller already installed there) goes green on a new push to PR #2
   using the CLI, and the review it posts matches acceptance 1.
5. `deep-review install-skill --target claude` puts the skill in `~/.claude/skills/deep-review/`, and a
   Claude Code session in the sandbox checkout invokes it when asked "review this branch".
6. `just test`, `just lint` and `ty check` pass.

## When done

Update `README.md` (usage moves to the CLI), tick the items you completed in `docs/MVP2.md`, commit in
small conventional commits, and push to `main`. Then write a short handoff at the top of this file under a
`## Status` heading: what shipped, what acceptance items you verified and how, and anything left.
