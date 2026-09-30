# Common operations. `just` with no target lists them.

default:
    @just --list

# Run the test suite.
test:
    uv run pytest

# Run only the live test: one real Run against a planted bug. It spends the Codex login's usage,
# which is why `just test` leaves it out.
test-live *ARGS:
    uv run pytest -m integration {{ARGS}}

# Lint and type-check.
lint:
    uv run ruff check .
    uv run ty check
    shellcheck install.sh hooks/session-start.sh

# Format.
fmt:
    uv run ruff format .

# Review the current branch (Local mode) with the CLI from this checkout.
review *ARGS:
    uv run deep-review review {{ARGS}}
