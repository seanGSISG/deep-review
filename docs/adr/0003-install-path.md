---
status: accepted
date: 2026-09-22
---

# The install path is the CLI's own `setup`, not an npm wizard

A member of the public arrives with none of uv, opencode or a Z.AI key. The question was whether
to wrap everything in an npm package with an interactive installer. We chose a `setup` command on
the Python CLI, a bootstrap shell script that reaches it, and the Claude Code plugin's own key
prompt.

## Considered options

- **npm package with a wizard.** Rejected. Nothing in the stack needs Node: opencode ships
  standalone binaries with an official installer that pins a version and edits the PATH, and uv
  does the same, so npm would add a runtime and a second package manager to host a wizard. The
  CLI is Python (ADR-0001), so the package could only shell out to uv, leaving two versions to
  move together. And npm runs lifecycle scripts without a TTY, so the wizard could not prompt from
  `postinstall`; it would be `npx something setup`, which is `deep-review setup` in another language.
- **`deep-review setup` (chosen).** Idempotent, prints a table, installs opencode from its official
  script after showing the command (`--yes` for scripts), leaves pi to the human because it needs
  Node, links the Skill for the agents present, and reports the key without ever asking for it.
  `install.sh` at the repo root is the curl-able bootstrap: uv, the CLI from a tagged release, then
  `setup --yes`.

## The key

Claude Code's plugin manifest can declare a `sensitive` user-config value: asked for at enable
time with masked input, kept in the OS keychain, and handed only to hook processes. The plugin's
SessionStart hook exports it under the CLI's fallback name, `Z_AI_API_KEY`, through the session env
file, unless the shell already carries a key. The value never enters the model's context. Everyone
outside Claude Code exports the variable in their shell, which is what opencode and pi users
already do for every provider; a config file of ours would be a second place for a secret to live.

## Consequences

- The hook runs at every session start for plugin users. It is a dozen lines of bash and prints
  nothing when the machine is ready; when the CLI or Reviewer is missing it prints one line.
- The bootstrap pins a release tag, so a release means a tag and a bump of the pinned version in
  `install.sh`.
- Windows is unbuilt: the opencode installer and the hook are bash.
