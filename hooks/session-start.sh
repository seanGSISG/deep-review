#!/usr/bin/env bash
# SessionStart hook for the pre-pr-review plugin. Silent when the machine is ready. When the CLI,
# the default Reviewer or the key is missing, it prints one line so the agent can tell the user
# what to run. Nothing is installed or asked for from here: a network install at session start is
# exactly the behaviour the Reviewer is sandboxed against, and the key is entered on a terminal,
# by `deep-review setup`, never through the agent.
set -u

# shellcheck disable=SC2016  # the backticks are for the reader, not the shell
if ! command -v deep-review >/dev/null 2>&1; then
  echo 'pre-pr-review: the deep-review CLI is not installed on this machine. Run `curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh` in a terminal to install it, the Reviewer and the key.'
elif ! command -v opencode >/dev/null 2>&1; then
  echo 'pre-pr-review: the Reviewer (opencode) is not installed on this machine. Run `deep-review setup` in a terminal.'
elif [ -z "${Z_AI_API_KEY:-}${ZAI_API_KEY:-}${ZHIPU_API_KEY:-}" ] \
  && [ ! -s "${XDG_CONFIG_HOME:-$HOME/.config}/deep-review/zai-api-key" ]; then
  echo 'pre-pr-review: no Z.AI key is configured. Run `deep-review setup` in a terminal; it asks for the key with the input hidden. Do not ask the user to paste the key into the chat.'
fi
