"""What a Run spent, read back out of the Reviewer's event stream."""

import json
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from deep_review.report import RunStats


@dataclass(frozen=True, slots=True)
class Tokens:
    """
    One step's tokens in the terms the Report reports them: `input` is the fresh half of the input,
    and `output` is what the model wrote *besides* its reasoning. Each Reviewer's reader normalises
    to this, so a Run's numbers mean the same thing whichever Reviewer produced them.
    """

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0


@dataclass(frozen=True, slots=True)
class Spend:
    """
    What one event reported: a step's tokens, one tool call, or neither. `identity` is the stream's
    own name for the thing being reported, where the stream has one — an event that arrives twice
    under the same identity is counted once.
    """

    identity: str | None = None
    tokens: Tokens | None = None
    tool: str | None = None


# How one Reviewer reads one of its events. Every difference between the streams lives behind this.
Reader = Callable[[dict[str, object]], Spend | None]


def opencode_stats(path: Path, stats: RunStats) -> RunStats:
    """The same stats with what opencode's event stream says the Run spent."""
    return _tally(path, _opencode, stats)


def pi_stats(path: Path, stats: RunStats) -> RunStats:
    """The same stats with what pi's event stream says the Run spent."""
    return _tally(path, _pi, stats)


def _tally(path: Path, read: Reader, stats: RunStats) -> RunStats:
    """
    The same stats with what the stream says the Run spent: tokens summed over every step it
    reported, and one count per tool name, most-used first.

    Nothing here can fail a Run. A stream that is missing, cut mid-line by the time cap, or full of
    shapes this parser has never seen leaves the counts at zero and says nothing — the Findings are
    what a Run is for, and they are not worth losing over the line that says what they cost.
    """
    totals = Tokens()
    tools: Counter[str] = Counter()
    counted: set[str] = set()
    for event in _events(path):
        spend = read(event)
        if spend is None:
            continue
        if spend.identity is not None:
            if spend.identity in counted:
                continue
            counted.add(spend.identity)
        if spend.tokens is not None:
            totals = _sum(totals, spend.tokens)
        if spend.tool is not None:
            tools[spend.tool] += 1
    return stats.model_copy(
        update={
            "input_tokens": totals.input,
            "output_tokens": totals.output,
            "reasoning_tokens": totals.reasoning,
            "cache_read_tokens": totals.cache_read,
            "tool_calls": dict(tools.most_common()),
        }
    )


def _sum(running: Tokens, step: Tokens) -> Tokens:
    """The running total with one more step's tokens in it."""
    return Tokens(
        input=running.input + step.input,
        output=running.output + step.output,
        reasoning=running.reasoning + step.reasoning,
        cache_read=running.cache_read + step.cache_read,
    )


def _opencode(event: dict[str, object]) -> Spend | None:
    """
    opencode reports on the event's *part*, not the event. `run --format json` renames each part as
    it prints it (`step-finish` becomes `step_finish`), so the part carries the stream's own name
    for the thing and the event carries one formatter's spelling of it.
    """
    part = event.get("part")
    if not isinstance(part, dict):
        return None
    # opencode prints an event per part update, and the same part can arrive twice — its event bus
    # replays on a reconnect, which is why it coalesces by part id upstream. Tokens added twice are
    # worse than tokens never added.
    identity = _name(part.get("callID")) or _name(part.get("id"))
    kind = part.get("type")
    if kind == "step-finish":
        return Spend(identity=identity, tokens=_opencode_tokens(part.get("tokens")))
    if kind == "tool":
        return Spend(identity=identity, tool=_name(part.get("tool")))
    return None


def _pi(event: dict[str, object]) -> Spend | None:
    """
    pi reports on the event itself: a message's usage when the message ends, and a tool call when
    its execution starts. Neither carries an identity, because pi prints its stream straight to
    stdout as it goes rather than replaying an event bus — nothing arrives twice to be counted
    twice, and the only id on an assistant message is the provider's, not the stream's own.
    """
    kind = event.get("type")
    if kind == "message_end":
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return None
        return Spend(tokens=_pi_tokens(message.get("usage")))
    if kind == "tool_execution_start":
        return Spend(tool=_name(event.get("toolName")))
    return None


def _opencode_tokens(reported: object) -> Tokens:
    """One opencode step's tokens, which already keep reasoning and output apart."""
    if not isinstance(reported, dict):
        return Tokens()
    cache = reported.get("cache")
    return Tokens(
        input=_count(reported.get("input")),
        output=_count(reported.get("output")),
        reasoning=_count(reported.get("reasoning")),
        cache_read=_count(cache.get("read")) if isinstance(cache, dict) else 0,
    )


def _pi_tokens(reported: object) -> Tokens:
    """
    One pi message's usage. Its `reasoning` is a subset of its `output` where opencode reports the
    two side by side, so the reasoning half comes out of `output` here rather than being counted in
    both columns and making the Run look dearer than it was.
    """
    if not isinstance(reported, dict):
        return Tokens()
    reasoning = _count(reported.get("reasoning"))
    return Tokens(
        input=_count(reported.get("input")),
        output=max(_count(reported.get("output")) - reasoning, 0),
        reasoning=reasoning,
        cache_read=_count(reported.get("cacheRead")),
    )


def _events(path: Path) -> Iterator[dict[str, object]]:
    """Every event in the stream, skipping whatever is not a JSON object."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A Reviewer killed on the time cap leaves half a line behind it.
                continue
            if isinstance(event, dict):
                yield event


def _name(value: object) -> str | None:
    """A name the stream gave as a string, or nothing to count it under."""
    return value if isinstance(value, str) else None


def _count(value: object) -> int:
    """A count the stream gave as a number. A bool is not one, whatever Python thinks."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
