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

### Two places a review happens

deep-review is **one pipeline with two entry points**, already named in `CONTEXT.md`:

- **Local mode** — the Coding agent runs the CLI in its own checkout, after `/tdd` and `/code-review`
  and before it pushes. That loop is the next section, and Deliverables 1 and 2 build it.
- **PR mode** — a Run against a pull request in a fresh clone, posting one Review. Today that is the
  Actions workflow, and Deliverable 3 points it at the CLI.

**The PR-time reviewer is expected to diverge from this pipeline.** It will run a different model, and
possibly a different method entirely: a per-file AST loop with rule application and layered suggestion
filters, in the shape Kodus uses, rather than one agent reading the whole repo. That is a **scale** play,
not a recall play — deep-review already beats Kodus on recall (5 of 5 with zero noise against Kodus deep
mode's 4 of 5 with two low-value items, `docs/bakeoff-2026-09.md`), but it collapses above ~10k diff
lines where a per-file method keeps working (`docs/MVP2.md` item 1, 0 of 7 on Winnow#16).

None of that is this task. It matters here for exactly one reason: **do not design the CLI around being
the permanent PR reviewer.** Keep PR mode as thin as it is today, so replacing it later costs a workflow
edit and nothing else.

Vocabulary note: `docs/bakeoff-2026-09.md` already uses "fast layer" and "deep layer" for PR-Agent and
deep-review, both running at PR time. That is a different axis from Local mode versus PR mode. Use the
`CONTEXT.md` mode names here; leave the layer words to the bake-off.

## The local review loop

Where a Run sits inside a ticket, and who reads what:

```
/implement  →  /tdd  →  /code-review  →  commit
                                           │
                                           ▼
                            ┌─── ROUND 1 ──────────────┐
                            │ deep-review review       │  ← main window sees
                            │   (table only, no --json)│    "6 findings →
                            │            ▼             │     .deep-review/"
                            │ spawn Verifier subagent  │  ← reads findings.json
                            │   in:  findings path,    │    + the Diff + the code
                            │        the Diff          │    IN ITS OWN WINDOW
                            │   out: one line per      │
                            │        Finding — real |  │  ← main window sees
                            │        not-real |        │    ~6 short lines
                            │        uncertain + why   │
                            │            ▼             │
                            │ agent fixes the real ones│  ← it owns the ticket
                            └────────────┬─────────────┘    and knows the spec
                                         ▼
                            ┌─── ROUND 2 ── same three steps ──┐
                            └────────────┬─────────────────────┘
                                         ▼
                                  push, open PR
                                         ▼
                               the PR-time reviewer
```

Three rules hold this together:

- **The CLI does not loop.** One Diff in, one Report out, exit. `CONTEXT.md` defines a Run as "one
  execution of the pipeline against one Diff, producing exactly one Report"; a looping CLI would have to
  hold state, count attempts, judge Findings and edit code, at which point it is a second agent. The
  Skill owns the loop, and the CLI stays the same tool Actions calls.
- **The Verifier adjudicates, the Coding agent fixes.** The Verifier is read-only: it takes the Report
  and the Diff and returns a verdict per Finding. The Coding agent applies the fixes, because it is the
  one that knows the ticket and the spec. That split is what keeps the evidence out of the main context
  window — quoted lines, failure scenarios, `signal/*.txt` and the agent event stream are read in the
  Verifier's window and die there.
- **Two Runs, two fix rounds, then push.** Round 2's fixes are not re-reviewed locally; the PR-time
  reviewer catches them. This is deliberate. Do not "fix" it by adding a third confirming Run.

`/code-review` running just before this is not redundant with it. They check different things with
different models: `/code-review` is Claude sub-agents asking whether the code follows the repo's standards
and matches the ticket, and deep-review is GLM proving defects by execution and quotation. Neither
subsumes the other; do not "simplify" by cutting one.

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
| `CONTEXT.md`, `docs/adr/` | Glossary and recorded decisions. Use the glossary's terms in code. |
| `docs/bakeoff-2026-09.md` | Results and, at the bottom, the pitfalls already hit. Read the pitfalls; they cost hours. |

Background on why this exists, if you want it and can reach it: `/home/adminuser/.claude/plans/so-right-now-we-goofy-cloud.md`. That path is local to Sean's
machine and is not in this repo — skip it if you are working from a fresh clone or in CI. Nothing
in this task depends on it.

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
`skills/pre-pr-review/SKILL.md` in this repo (not `deep-review`: that is the CLI, and Claude Code carries a skill of that name), following the Agent Skills format (YAML frontmatter with
`name` and a `description` that says when to trigger, then instructions; `name` must equal the directory
name and match `^[a-z0-9]+(-[a-z0-9]+)*$`). Verified load paths on this machine: Claude Code and opencode
both read `~/.claude/skills/<name>/SKILL.md` (opencode also reads `~/.config/opencode/skills/` and
`~/.agents/skills/`, per https://opencode.ai/docs/skills/); Pi reads `~/.pi/agent/skills/<name>/SKILL.md`.
`deep-review install-skill` symlinks the skill directory into `~/.claude/skills/` (covers Claude Code and
opencode) and `~/.pi/agent/skills/` (Pi); `--target` picks a subset.

The skill should tell the agent to:
1. Run `deep-review review` (Local mode) after `/code-review` and before pushing, or when asked for a
   review of the current branch. **Not** `--json`: the whole Report landing in the main context window is
   the thing this design exists to avoid. The agent needs the finding count and the path, nothing more.
2. Spawn a Verifier subagent, passing it `.deep-review/findings.json` and the Diff. It returns one line
   per Finding — real, not-real or uncertain, with a one-line reason. It does not edit code.
3. Fix the Findings the Verifier called real, decide on the uncertain ones, and explain any it rejects to
   the user with that Finding's evidence.
4. Repeat steps 1 to 3 at most twice, then push regardless.
5. Never edit `.deep-review/` by hand and never commit it.

Carry the Verifier's prompt **inline in `SKILL.md`** rather than shipping a `.claude/agents/*.md` file:
the skill targets Claude Code, opencode, Codex and Pi, and an inline prompt spawned as a generic subagent
works in all four, where a committed agent definition works in one.

Keep it short; the CLI does the work. Note in the skill that the reviewer is a different model family from
the one writing the code, on purpose.

## Deliverable 3: switch the workflow to the CLI

Change `.github/workflows/deep-review.yml` so the job installs the CLI (`uv tool install
git+https://github.com/seanGSISG/deep-review@main` or `uvx --from git+... deep-review`) plus the pinned
opencode/pi/ast-grep/gitleaks binaries, then runs
`deep-review review --pr "$REPO#$PR" --agent "$AGENT" --post`. Keep the artifact upload
(`include-hidden-files: true`). Delete `scripts/run_local.sh` and `scripts/post_review.py` once the CLI
replaces them; keep `scripts/collect_signal.sh` only if the CLI still shells out to it.

Keep this thin on purpose. The PR-time reviewer is expected to become a different model and possibly a
different method (see "Two places a review happens"), so the workflow should install the CLI and run one
command — nothing more — and replacing it later should cost a workflow edit.

## Decisions (grilled with Sean, 2026-09-18)

These refine the deliverables above and win over them where they differ. Vocabulary is in
`CONTEXT.md` (Reviewer, Coding agent, Run, Report, Finding, Signal, Hypothesis, Score); use it in code
names and comments. The Python-over-TypeScript choice is recorded in `docs/adr/0001-python-cli.md`.

### Package and toolchain
- Python 3.12 floor. Runtime dependency: Pydantic only. CLI is argparse; output is plain text, no Rich.
- Signal collection is ported to Python (one typed function per tool, each with its own 300 s timeout
  and 60,000-byte cap, same gates and filenames as `collect_signal.sh`); the shell script is deleted.
- `prompts/review.md` and `skills/pre-pr-review/SKILL.md` stay at the repo root as the single source and
  are mapped into the wheel with hatchling `force-include`; the CLI reads them via `importlib.resources`.
- ast-grep rules: the CLI clones `coderabbitai/ast-grep-essentials` at a pinned commit (constant in the
  code) into `~/.cache/deep-review/ast-grep-essentials` when missing; `AST_GREP_RULES` overrides.
- `just lint` runs `ruff check` and `ty check`. Add `just acceptance` that runs acceptance item 1.

### `review` command
- Local mode Diff is `git diff <merge-base>` against a temporary index with untracked files added as
  intent-to-add, so new files are reviewed. No resolvable base (no `origin/main`, `main`, `master`)
  exits 2 asking for `--base`. An empty Diff exits 0 with status `skipped`.
- Every Local run appends `.deep-review/` to `.git/info/exclude`; the repo's `.gitignore` is not touched.
- PR mode clones exactly as `run_local.sh` does (`gh repo clone`, fetch head and base SHAs, fall back
  to `pull/N/head`, checkout head). `--out DIR` copies the whole `.deep-review/` after the run; its
  default is `./.deep-review` in PR mode and no copy in Local mode.
- `--pr` accepts `OWNER/REPO#N` or a full PR URL.
- `--model` without a slash gets the selected Reviewer's provider prefix (`zai-coding-plan/` for
  opencode, `zai/` for pi); with a slash it is passed through.
- `--variant LEVEL` is passed through verbatim: opencode `--variant LEVEL`, pi `--thinking LEVEL`.
  Defaults: unset for opencode, `medium` for pi (today's behaviour).
- `--agent` defaults to **opencode**: best recall in the bake-off, and `run_local.sh` already defaults
  to it. `--agent pi` stays selectable (faster, roughly half the tokens, and it found a real race
  opencode never looked for).
- Preflight: if the selected Reviewer binary is not on PATH, exit 2 with an install hint before
  cloning or collecting Signal. No `npx` fallback.
- Size gate counts added plus removed lines from `git diff --numstat`. When it trips with `--post`,
  delete the previous run's inline comments and upsert the summary comment with the one-line notice.
- `--json` prints the whole Report (same bytes as `findings.json` on disk), not just the findings array.

### Report model
- **`Score` comes from the Reviewer, not the CLI.** `prompts/review.md` already defines it (0-5,
  5 = safe to merge) and the Reviewer emits it in `findings.json`. The CLI validates the range and does
  nothing else with it: it is a judgment about code the model actually read, and a severity-count formula
  would only invent precision about whether two P1s beat one P0. It is **advisory** — `--fail-on` gates on
  Severity and never on Score. Do not turn Score into a gate.
- `Report.status` is `ok | skipped | failed`; `Report.notice` is a free-text string carrying the size-gate
  message, Reviewer crash or timeout details, and reasons for dropped findings. Run stats live in
  `Report.stats` (agent, model, variant, seconds, input/output/reasoning/cache-read tokens, tool-call
  counts by name).
- A finding with an invalid severity or a missing required field (`file`, `line`, `severity`, `title`,
  `evidence`, `failure_scenario`) is dropped and noted in `notice`; the Report never fails validation
  because of one finding. `end_line`, `fix`, `source` (default `agent`) are optional.
- Exit codes: 0 normally, 1 only when `--fail-on` matches (P1 means P0 or P1), 2 for usage errors
  (`--post` without `--pr`, no base, Reviewer missing).

### Posting
- Format, markers (`<!-- deep-review:summary -->`, `<!-- deep-review:finding -->`), suggestion blocks,
  and off-diff handling carry over unchanged from `post_review.py`. The review event is always
  `COMMENT`. The footer's run link appears only when `GITHUB_RUN_ID` is set.
- `deep-review post` requires `--pr` (no inference from the checkout) and fetches the PR diff and head
  SHA itself via `gh`; `--findings` defaults to `.deep-review/findings.json`.

### Skill
- `install-skill --target claude|pi|all` (default `all`). `claude` covers Claude Code and opencode via
  `~/.claude/skills/`; `pi` is `~/.pi/agent/skills/`. Symlink from the installed package's skill dir.
  If the target exists and is not our symlink, refuse and require `--force`.
- The skill caps the Coding agent at **two** Runs per task: Run, verify, fix, Run, verify, fix, push.
  There is no third confirming Run — after the second round it pushes regardless and lets the PR-time
  reviewer catch what is left, reporting any remaining Findings with their evidence.
- The Verifier's prompt lives inline in `SKILL.md`, not as a committed agent definition. It is read-only,
  returns a verdict per Finding, and never edits code; the Coding agent applies the fixes.
- **Dismissal takes the same standard as the Finding.** The Verifier is the same model family as the
  Coding agent, judging code that family just wrote, so the cheapest route to "clean" is to call
  everything not-real. To return not-real it must quote the code or run something showing the Finding's
  failure scenario cannot occur; "I looked and it seems fine" is not a dismissal. This mirrors what
  `prompts/review.md` already demands of the Reviewer.
- **Uncertain counts as real for P0 and P1.** Only P2 Findings may be dropped on judgment alone.
- The skill never invokes `--json`. It hands the Verifier the path to `findings.json` so the Report's
  evidence is read in the Verifier's context window rather than the Coding agent's.

### Workflow
- Remove `actions/checkout`; the CLI clones. Export `GH_TOKEN` and `GITHUB_TOKEN` from the job token.
  Install uv, the CLI, and only the selected Reviewer's binary plus ast-grep and gitleaks. Pass
  `--timeout $((timeout_minutes - 5))`. Upload `./.deep-review` with `include-hidden-files: true`.

### Tests
- The integration smoke test runs Local mode on a tmp git fixture with one obvious planted bug and
  asserts only that the Report validates with status `ok`; skipped without `Z_AI_API_KEY`.
- Parser fixtures are excerpts of real event streams (about ten stats-bearing events per Reviewer,
  tool output stripped), captured during the acceptance run. Do not spend extra credits to record them.

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
5. `deep-review install-skill --target claude` puts the skill in `~/.claude/skills/pre-pr-review/`, and a
   Claude Code session in the sandbox checkout invokes it when asked "review this branch".
6. `just test`, `just lint` and `ty check` pass.

## When done

Update `README.md` (usage moves to the CLI), tick the items you completed in `docs/MVP2.md`, commit in
small conventional commits, and push to `main`. Then write a short handoff at the top of this file under a
`## Status` heading: what shipped, what acceptance items you verified and how, and anything left.
