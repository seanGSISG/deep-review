# Bake-off, September 2026

Goal: decide whether an agentic deep review adds real bugs over stock PR-Agent at cents per PR, and
which agent CLI to run it with. Model everywhere: GLM-5.3 on the Z.AI coding plan (Pro tier, $0 marginal).

## Test set

| PR | What it is | Ground truth |
|---|---|---|
| pr-review-sandbox #1 (head `5a7a0fd`) | Round-1 planted bugs, then a fix commit + tests | 0 remaining bugs (all fixed in `a0a7905`) |
| pr-review-sandbox #2 (head `412134d`) | Round-1 commit cherry-picked onto main, no tests | 5 planted: failed lookup aborts loop, missing tracking-number guard removed, retry count off by one, non-atomic `save_state`, `page()` off-by-one and `limit=0` mishandled |

## Results

| Reviewer | PR | Real bugs found | Noise | Time | Tokens (fresh in / cached / out) | Notes |
|---|---|---|---|---|---|---|
| PR-Agent `/improve` (fast layer, App on COLO) | #2 | 3 of 5: early return, missing guard, paging off-by-one | 0 | ~1 min | n/a | inline suggestions, importance 8-9 |
| deep-review, **Pi** 0.80 | #2 | 4 of 5: paging, missing guard, non-atomic write, loose `load_state` | 0 | 118 s | 11.6k / 43k / 7.4k, 8 steps, 12 tools | wrote `/tmp/verify_pr2.py` and proved each finding by running it; missed the retry off-by-one |
| deep-review, **opencode** 1.4 (local) | #2 | **5 of 5**, plus 2 more real ones (`limit=0` coerced to 50, `load_state` returns non-dict) | 0 | 277 s | 56k / 760k / 4.5k, 11 steps, 16 tools | extracted the sibling branch's pinned tests and ran them (4 failed), plus a throwaway script; score 1/5 |
| deep-review, opencode 1.18 (**GitHub Actions**, run 35364673214) | #2 | **5 of 5**, plus `limit=0`, non-dict `load_state`, and 'no tests' | 0 | 159 s agent, ~3.5 min job | n/a | 8 inline comments + summary with score 1/5 and execution evidence; the production path |
| deep-review, opencode 1.4 | #1 | 0 (correct) | 0 | 148 s | 33k / 538k / 2k, 8 steps, 15 tools | proved the fix commit via `git log -p` and an edge-case script; score 5/5 |
| deep-review, Pi 0.80 | #1 | 1 real P2 (concurrent `save_state` race on a fixed tmp name, 59/60 trials crashed) | 0 | 228 s | ~30k ctx/call | a defect nobody planted; opencode did not look for it |

## Verdict so far
The deep layer earns its place: on PR #2 it found every planted bug plus real extras that the fast layer missed, with zero noise, in under 5 minutes and for a negligible slice of the Pro plan. opencode is the default harness (best recall here); Pi stays selectable (`agent: pi`) because it was faster and found a real race opencode did not look for on PR #1. Next: run both on the two large real PRs from the 2026-09-12 eval (hardware-dashboard#25, Winnow#16) with `scripts/run_local.sh`, and compare against Kodus once its GitHub App exists.

## Observations
- Both agents ground findings in execution when the prompt demands it; that is the behaviour Greptile's
  TREX sells, and it costs seconds here.
- opencode carries a ~63k-token system prompt per call (nearly all cache hits, so cheap in credits but not
  free). Pi's per-call context is about half that.
- The fast layer and the deep layer overlap on the obvious bugs; the deep layer's added value is the
  execution-verified ones (atomicity, races) and the zero-noise output.

## Pitfalls hit
- Agent CLIs block forever when stdin is an open socket/pipe; run them with `< /dev/null`.
- `npm install --ignore-scripts` breaks opencode (postinstall fetches the binary); fine for Pi.
- `actions/upload-artifact@v4` skips dot-prefixed paths unless `include-hidden-files: true`.
- A called workflow can only request permissions the caller job grants (`pull-requests: write`).
