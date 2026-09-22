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

`deep-review setup` asks for it on the terminal with the input hidden (getpass opens /dev/tty, so
this works under `curl ... | sh` too) and stores it in `$XDG_CONFIG_HOME/deep-review/zai-api-key`,
directory 700, file 600. The environment still wins, so a shell export or a CI secret overrides
the file. The coding agent never asks for the key: a secret typed into a chat lands in the
transcript, and the plugin's SessionStart hook says so when it finds no key.

We first tried Claude Code's plugin `userConfig` with `sensitive: true`, which stores in the
keychain and hands the value to hooks. It was abandoned: the enable-time dialog did not appear
for Sean, a CLI install only prints "run `/plugin configure`", and the value reaches the agent's
shell only through a hook writing the session env file. Three moving parts, one of them not under
our control, for a prompt the CLI can do itself in ten lines, and it left opencode and pi users
with nothing.
## Consequences

- The hook runs at every session start for plugin users. It is a dozen lines of bash and prints
  nothing when the machine is ready; when the CLI, the Reviewer or the key is missing it prints
  one line.
- The bootstrap pins a release tag, so a release means a tag and a bump of the pinned version in
  `install.sh`.
- Windows is unbuilt: the opencode installer and the hook are bash.
