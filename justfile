# Common operations. `just` with no target lists them.

default:
    @just --list

# Run the test suite.
test:
    uv run pytest

# Lint and type-check.
lint:
    uv run ruff check .
    uv run ty check

# Format.
fmt:
    uv run ruff format .

# Review the current branch (Local mode) with the CLI from this checkout.
review *ARGS:
    uv run deep-review review {{ARGS}}
