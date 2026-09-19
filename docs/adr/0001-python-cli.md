---
status: accepted
date: 2026-09-18
---

# Python for the deep-review CLI

The CLI could have been TypeScript: the Reviewers (opencode, pi) are npm packages so Node is
already on every CI runner, the planned COLO webhook service may be TypeScript, and Sean's general
preference for CLIs and services is TypeScript. We chose Python anyway because the diff-hunk line
mapping, the marker-based idempotent poster, the stdlib GitHub client, and both Reviewer
event-stream parsers already exist in Python and were debugged against real runs; `uvx --from git+…`
runs the source with no build step or committed dist; and `subprocess.run` gives stdin-to-devnull,
timeout, and process-group kill in one call, which is the whole hard part of the runner.

## Considered options

- **TypeScript on Bun/Node.** Rejected: rewrites every debugged piece, needs a build step or a
  committed dist to install from git, and process-tree killing is fiddlier.
- **Python (chosen).** Package `src/deep_review/`, uv, Ruff, ty, pytest, Pydantic at boundaries.

## Consequences

- CI installs uv in addition to Node (about ten seconds).
- The COLO service calls the CLI as a subprocess rather than importing a shared library; the
  language behind that boundary is invisible to it, so this decision does not constrain the service.
