"""What a Run spent, read back out of the Reviewer's event stream."""

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from deep_review.report import RunStats

# The part types carrying the numbers. Match on the *part's* type, not the event's: opencode's
# `run --format json` renames each part as it prints it (`step-finish` becomes `step_finish`), so
# the part is the stream's own name for the thing and the event's is one formatter's spelling of it.
STEP_FINISH = "step-finish"
TOOL = "tool"


def read_stats(path: Path, stats: RunStats) -> RunStats:
    """
    The same stats with what the event stream says the Run spent: fresh, cached, output and
    reasoning tokens summed over every step, and one count per tool name, most-used first.

    Nothing here can fail a Run. A stream that is missing, cut mid-line by the time cap, or full of
    shapes this parser has never seen leaves the counts at zero and says nothing — the Findings are
    what a Run is for, and they are not worth losing over the line that says what they cost.
    """
    tokens: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    counted: set[str] = set()
    for part in _parts(path):
        # One part, counted once. opencode prints an event per part update, and the same part can
        # arrive twice — its event bus replays on a reconnect, which is why it coalesces by part id
        # upstream. Tokens added twice are worse than tokens never added.
        identity = part.get("callID") or part.get("id")
        if isinstance(identity, str):
            if identity in counted:
                continue
            counted.add(identity)
        kind = part.get("type")
        if kind == STEP_FINISH:
            _spent(tokens, part.get("tokens"))
        elif kind == TOOL and isinstance(name := part.get("tool"), str):
            tools[name] += 1
    return stats.model_copy(
        update={
            "input_tokens": tokens["input"],
            "output_tokens": tokens["output"],
            "reasoning_tokens": tokens["reasoning"],
            "cache_read_tokens": tokens["cache_read"],
            "tool_calls": dict(tools.most_common()),
        }
    )


def _parts(path: Path) -> Iterator[dict[str, object]]:
    """Every event's part, skipping whatever is not a JSON object carrying one."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A Reviewer killed on the time cap leaves half a line behind it.
                continue
            if isinstance(event, dict) and isinstance(part := event.get("part"), dict):
                yield part


def _spent(tokens: Counter[str], reported: object) -> None:
    """One step's token counts, ignoring any the stream did not report as a number."""
    if not isinstance(reported, dict):
        return
    for name in ("input", "output", "reasoning"):
        tokens[name] += _count(reported.get(name))
    cache = reported.get("cache")
    tokens["cache_read"] += _count(cache.get("read")) if isinstance(cache, dict) else 0


def _count(value: object) -> int:
    """A count the stream gave as a number. A bool is not one, whatever Python thinks."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
