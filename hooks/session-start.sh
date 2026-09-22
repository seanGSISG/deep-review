#!/usr/bin/env bash
# SessionStart hook for the pre-pr-review plugin.
#
# Two jobs, both silent when there is nothing to do. First, the Z.AI key the plugin asked for at
# enable time arrives here as CLAUDE_PLUGIN_OPTION_ZAI_API_KEY (Claude Code keeps it in the
# keychain; it never reaches the model). Export it for the agent's shell through the session env
# file, under the CLI's own fallback name, unless the shell already carries a key. Second, when
# the CLI or the default Reviewer is missing, say so in one line so the agent can tell the user
# what to run. Nothing is installed from here: a network install at session start is exactly the
# behaviour the Reviewer is sandboxed against.
set -u

if [ -n "${CLAUDE_PLUGIN_OPTION_ZAI_API_KEY:-}" ] && [ -n "${CLAUDE_ENV_FILE:-}" ] \
  && [ -z "${Z_AI_API_KEY:-}" ] && [ -z "${ZAI_API_KEY:-}" ] && [ -z "${ZHIPU_API_KEY:-}" ]; then
  printf 'export Z_AI_API_KEY=%q\n' "$CLAUDE_PLUGIN_OPTION_ZAI_API_KEY" >> "$CLAUDE_ENV_FILE"
fi

# shellcheck disable=SC2016  # the backticks are for the reader, not the shell
if ! command -v deep-review >/dev/null 2>&1; then
  echo 'pre-pr-review: the deep-review CLI is not installed on this machine. Run `curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh` to install it and the Reviewer.'
elif ! command -v opencode >/dev/null 2>&1; then
  echo 'pre-pr-review: the Reviewer (opencode) is not installed on this machine. Run `deep-review setup`.'
fi
