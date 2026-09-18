#!/usr/bin/env bash
# Gather deterministic review signal into .deep-review/signal/*.txt.
# Runs the repo's own lint/test entry points when they exist, then gitleaks and
# ast-grep (CodeRabbit's essentials rule pack). Every step is best-effort and
# time-capped: a tool that is missing or fails writes a note, never fails the job.
set -u
OUT=.deep-review/signal
mkdir -p "$OUT"
STEP_TIMEOUT=${STEP_TIMEOUT:-300}

run() { # run <name> <cmd...> : capture combined output, capped
  local name=$1; shift
  { echo "\$ $*"; timeout "$STEP_TIMEOUT" "$@" 2>&1; echo "[exit $?]"; } | tail -c 60000 > "$OUT/$name.txt"
}

# Project-native entry points
if [ -f justfile ] && command -v just >/dev/null; then
  just --summary 2>/dev/null | tr ' ' '\n' | grep -qx test && run just-test just test
  just --summary 2>/dev/null | tr ' ' '\n' | grep -qx lint && run just-lint just lint
fi
if [ -f package.json ]; then
  PM=npm; [ -f pnpm-lock.yaml ] && PM=pnpm; [ -f bun.lockb ] || [ -f bun.lock ] && PM=bun
  command -v "$PM" >/dev/null || PM=npm
  [ -d node_modules ] || run install "$PM" install --ignore-scripts
  grep -q '"lint"' package.json && run pkg-lint "$PM" run lint
  grep -q '"typecheck"' package.json && run pkg-typecheck "$PM" run typecheck
  grep -q '"test"' package.json && run pkg-test "$PM" run test
fi
if [ -f pyproject.toml ] || ls ./*.py >/dev/null 2>&1 || [ -d src ] || [ -d tests ]; then
  command -v uv >/dev/null && { run ruff uvx ruff check .; run pytest uv run --with pytest pytest -q; }
fi
if [ -f go.mod ]; then run go-vet go vet ./...; run go-test go test ./...; fi
if [ -f Cargo.toml ]; then run cargo-clippy cargo clippy --all-targets -q; run cargo-test cargo test -q; fi

# Cross-language scanners
if command -v gitleaks >/dev/null; then run gitleaks gitleaks dir . --no-banner --redact -v; fi
if command -v ast-grep >/dev/null && [ -d "${AST_GREP_RULES:-/opt/ast-grep-essentials}" ]; then
  run ast-grep ast-grep scan -c "${AST_GREP_RULES:-/opt/ast-grep-essentials}/sgconfig.yml" --report-style short .
fi
ls -la "$OUT"
