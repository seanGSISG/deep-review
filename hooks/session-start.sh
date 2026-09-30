#!/usr/bin/env bash
# SessionStart hook for the pre-pr-review plugin. Silent when the machine is ready. When the CLI,
# Codex or its login is missing, it prints one line so the agent can tell the user what to run.
# Nothing is installed or logged in from here: `codex login` opens a browser, which only the human
# at the terminal can finish, and a network install at session start is not this hook's to make.
set -u

# shellcheck disable=SC2016  # the backticks are for the reader, not the shell
if ! command -v deep-review >/dev/null 2>&1; then
  echo 'pre-pr-review: the deep-review CLI is not installed on this machine. Run `curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh` in a terminal to install it.'
elif ! command -v codex >/dev/null 2>&1; then
  echo 'pre-pr-review: the Reviewer (Codex) is not installed on this machine. Run `npm install -g @openai/codex` in a terminal, then `deep-review setup`.'
elif [ ! -s "${CODEX_HOME:-$HOME/.codex}/auth.json" ]; then
  echo 'pre-pr-review: Codex is not logged in on this machine. Run `codex login` in a terminal, then `deep-review setup`.'
fi
