# Common operations. `just` with no target lists them.

default:
    @just --list

# Run the test suite.
test:
    uv run pytest

# Run only the live test: one real Run against a planted bug. It spends Z.AI credits, which is
# why `just test` leaves it out.
test-live:
    uv run pytest -m integration

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
