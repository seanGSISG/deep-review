---
status: accepted
date: 2026-09-19
---

# The Reviewer runs against an empty settings directory of ours

Both agent CLIs load instruction files, skills, plugins and MCP servers on their own, from the
developer's home directory and from the checkout under review. Measured on Sean's box before this
change, every opencode Run reached the Reviewer's system prompt carrying `~/.config/opencode/AGENTS.md`
and `~/.claude/CLAUDE.md`, four MCP servers and a plugin from the global `opencode.json`, and the
skills under `~/.claude/skills` — one of which is the Skill that asks for a Run in the first place.
pi loaded the packages named in `~/.pi/agent/settings.json`, and with `defaultProjectTrust: "always"`
trusted project-local files outright. A Run's behaviour therefore depended on the machine it ran on
and, in PR mode, on the branch it was reviewing.

We point each CLI at an empty directory we own — `XDG_CONFIG_HOME` for opencode, `PI_CODING_AGENT_DIR`
for pi — drop every variable in the CLI's own environment namespace, and set the flags that close
what is left. The directory lives under the cache (`$XDG_CACHE_HOME/deep-review/reviewers/<name>`)
because it is disposable: the CLIs fill it with their own state, and a Run that finds it missing
costs one npm install to build it again.

Tools are deliberately **not** restricted. Execution is the premise of the whole design: a Reviewer
that cannot run `bash` cannot prove a defect, which is the one thing a Finding requires.

## Considered options

- **An empty settings directory of ours, plus the CLI's own disabling flags (chosen).**
  `XDG_CONFIG_HOME` for opencode, `PI_CODING_AGENT_DIR` for pi, the CLI's whole environment
  namespace dropped, and `OPENCODE_DISABLE_PROJECT_CONFIG` / `OPENCODE_DISABLE_CLAUDE_CODE` /
  `--no-context-files` / `--no-approve` for what a directory cannot reach.
- **`OPENCODE_CONFIG_DIR`.** Rejected: it looks like the lever and is not. `globalFiles()` pushes
  `$OPENCODE_CONFIG_DIR/AGENTS.md` *in addition to* `Global.Path.config/AGENTS.md`, so setting it
  adds a source instead of removing one. `Global.Path.config` resolves from `XDG_CONFIG_HOME`, which
  is the only lever that reaches the unconditional push.
- **`--pure` alone (what we shipped before).** Rejected: its whole effect is setting `OPENCODE_PURE=1`,
  which two plugin loaders read. It gates none of the instruction files, skills or MCP servers, and
  it does not stop the npm install a `.opencode/` directory in the checkout triggers.
- **`OPENCODE_DISABLE_CLAUDE_CODE_PROMPT`.** Superseded by `OPENCODE_DISABLE_CLAUDE_CODE`, which
  implies it *and* `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`. One variable, two holes, no list to keep
  in sync.
- **Seeding the hermetic directory with the user's credentials.** Considered because pi's `auth.json`
  lives in the agent directory, so an empty one appeared to cost the Reviewer its provider.
  Unnecessary: pi falls back to the provider's environment variable, which a Run already sets, and
  `zai/glm-5.3` resolves from an empty directory with `ZAI_API_KEY` in the environment. opencode
  keeps its `auth.json` under `XDG_DATA_HOME`, which we leave alone.
- **An allowlist of variables to drop.** Rejected in favour of dropping the CLI's whole namespace.
  `OPENCODE_CONFIG`, `OPENCODE_CONFIG_CONTENT` and `OPENCODE_CONFIG_DIR` each hand over a whole
  configuration; an allowlist would need revisiting every release, and a namespace does not.

## Consequences

- A `.opencode/` directory in the checkout no longer starts an npm install that writes into the
  tree under review. That was never remote code execution — opencode installs with `ignoreScripts`
  — but a Run must not mutate what it is reviewing, and in PR mode the package names come from the
  repository being reviewed.
- Sean's `~/.config/opencode/opencode.json` no longer reaches a Run, so the Reviewer loses the
  LiteLLM provider and the homelab MCP servers it had been getting by accident. `--model` still
  takes any provider the CLI resolves without configuration.
- One vector stays open, and no flag reaches it: opencode's read tool resolves instruction files on
  *every file read* and appends a nearby `AGENTS.md` to the tool output inside `<system-reminder>`
  tags, ungated by `OPENCODE_DISABLE_PROJECT_CONFIG`. A sub-directory `AGENTS.md` in the branch
  under review therefore still reaches the model. That one is defended at our layer rather than
  with a flag — issue #12.
- LSP servers are left alone. opencode downloads them lazily into `$XDG_CACHE_HOME/opencode/bin`,
  which is capability rather than context — and disabling the download would make two machines
  *differ*, since one with a warm cache would keep its diagnostics and one without would not.
