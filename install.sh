#!/bin/sh
# Bootstrap deep-review on a machine that has nothing: uv if it is missing, then the CLI from a
# tagged release, then `deep-review setup`, which installs the Reviewer, asks for the key and links the Skill.
# Each command is printed before it runs. Idempotent: run it again to reinstall or upgrade.
#
#   curl -fsSL https://raw.githubusercontent.com/seanGSISG/deep-review/main/install.sh | sh
#
# DEEP_REVIEW_VERSION picks another tag or branch; the default is the release this script shipped in.
set -eu

VERSION="${DEEP_REVIEW_VERSION:-v0.3.1}"
say() { printf '\n$ %s\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
  say "curl -LsSf https://astral.sh/uv/install.sh | sh"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# uv's installer puts uv here and edits the shell profile; this shell is older than that edit.
export PATH="$HOME/.local/bin:$PATH"

say "uv tool install --force git+https://github.com/seanGSISG/deep-review@$VERSION"
uv tool install --force "git+https://github.com/seanGSISG/deep-review@$VERSION"

say "deep-review setup --yes"
exec deep-review setup --yes
