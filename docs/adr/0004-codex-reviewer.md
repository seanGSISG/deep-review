---
status: accepted
date: 2026-09-30
supersedes: 0002
---

# The Reviewer is Codex on gpt-6.1-sol, hermetic by its own home and flags

GLM is out everywhere. argus moved to gpt-6.1-sol at medium effort after the 2026-09-29
pr-review-eval run, and opencode 2.x broke the default Reviewer outright (#17: `--pure`,
`--dangerously-skip-permissions` and `--variant` are all gone). The Reviewer is now `codex exec`
on the developer's own Codex (ChatGPT) login. opencode and pi are no longer Reviewers; they stay
supported as Coding agents that load the Skill.

## Considered options

- **Port opencode to 2.x and point it at the ai-pool proxy.** Rejected: every hermeticity lever in
  ADR-0002 would need re-deriving for a CLI that now runs through a background service, the pool
  needs a per-machine client key and the tailnet, and effort can only reach gpt-6.1-sol through a
  `(medium)` suffix on the model name, which the proxy parses.
- **Keep pi on the pool.** Rejected for the same key and tailnet cost, and a second Reviewer keeps
  the table, the per-Reviewer key names (#21) and the fallback question (#25) alive.
- **Codex (chosen).** OpenAI's own harness for the model. The login already exists on the machine,
  so there is no key, no setup prompt and no proxy. `--output-schema` makes the Report the final
  message, validated in strict mode by the API rather than trusted from a prompt. The sandbox
  enforces what the prompt could only ask for: the tree's `.git` is read-only and the network off.

## Hermeticity, lever by lever (Codex 0.159.2; source paths in openai/codex)

| What would leak | Closed by | Evidence |
|---|---|---|
| `~/.codex/config.toml`: MCP servers, plugins, profiles, hook trust | a fresh temporary Codex home per Run, deleted when it ends, plus `--ignore-user-config` | cli reference |
| `$CODEX_HOME/AGENTS.md` / `AGENTS.override.md` | the same home: it is read straight from the home with no flag or key to stop it | `codex-home/src/instructions/mod.rs` |
| the checkout's `AGENTS.md` chain | `-c project_doc_max_bytes=0`: `read_agents_md` returns before walking | `core/src/agents_md.rs` |
| `~/.agents/skills`, the repo's `.agents/skills` | `-c skills.include_instructions=false`, which drops the catalog block | `config/src/skills_config.rs` |
| `.codex/hooks.json` in the checkout, user hooks | `-c features.hooks=false`, and the empty home | hooks docs |
| execpolicy `.rules` | `--ignore-rules` | cli reference |
| `CODEX_API_KEY` and the rest of `CODEX_*` in the developer's shell | the whole namespace dropped from the Run's environment | — |
| session history | `--ephemeral` | cli reference |

Verified on 2026-09-30 with a canary checkout (AGENTS.md, CLAUDE.md, `.agents/skills`, a project
`.codex/config.toml`): with these levers none of them reached the model's input, where the flags
alone had let the repo AGENTS.md through and the model obeyed it.

**The login.** The home holds one entry: `auth.json`, a symlink to the developer's own. It is
fresh per Run because Codex writes its own state (config, memories, skills) into its home as it
goes; a shared home carried those between Runs, and two Runs racing to create the link crashed
one of them (both found by the first self-review). Preflight also requires `codex login status`
to say "Logged in using ChatGPT": it exits 0 for an API key too, which would bill metered credit. Codex's file
store saves by opening the path in place (`login/src/auth/storage.rs`, truncate + write, no
rename), so a token refresh inside a Run writes through the link instead of forking a copy whose
refresh token the next refresh would invalidate. File storage is Codex's default; a keyring login
is keyed by a hash of the home path, so it cannot follow a Run, and preflight says so.

**The sandbox.** `--sandbox workspace-write` with the user's cache added as a writable root: uv
failed on its lock under `~/.cache/uv` without it. `.git`, `.agents` and `.codex` stay read-only
inside the checkout, and the network is off, so a test step that fetches fails and the prompt tells
the Reviewer to record that rather than work around it.

## Consequences

- No key anywhere: `credentials.py`, the setup key prompt and the three Z.AI variable names are
  gone (#19, #21). `setup` checks and reports; it installs nothing, because Codex ships over npm
  or its own `codex update` and its login needs a browser.
- `--agent` and `--variant` are gone; `--model` and `--effort` go straight to Codex. A Run's
  effort is what Codex was sent, closing the inert-variant half of #13.
- The Skill runs the CLI through `uvx --from git+…@v<release>`, never a `deep-review` on PATH, so
  the plugin's release decides the CLI's (#26, proposal 5). A release bumps that pin with
  `__version__`, `install.sh` and `plugin.json`; `test_skill.py` fails if any of them disagree.
- The GitHub Actions workflow, its caller template and `scripts/run_local.sh` are retired: they
  ran opencode and pi on a Z.AI secret, and a hosted runner has no Codex login. argus covers the
  PR; `scripts/post_review.py` stays for #7.
- The bake-off's numbers are for GLM on opencode and pi. They do not transfer, and #15 now means
  re-running it on Codex.
- Codex's own multi-agent role text still reaches the model; it instructs against spawning
  sub-agents unless asked, and no config key in 0.159.2 removes it.
