"""What a Run spent, read back out of the Reviewer's `codex exec --json` event stream."""

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from deep_review.report import RunStats

# Item types that are the Reviewer acting rather than talking. The rest (agent_message,
# reasoning, todo_list, error) are what it said or thought, not a tool it reached for.
TOOL_ITEMS = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search"})


@dataclass(frozen=True, slots=True)
class Tokens:
    """
    Tokens in the terms the Report reports them: `input` is the fresh half of the input, and
    `output` is what the model wrote *besides* its reasoning.
    """

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0


def codex_stats(path: Path, stats: RunStats) -> RunStats:
    """
    The same stats with what the stream says the Run spent: tokens summed over every completed
    turn, and one count per tool, most-used first.

    Nothing here can fail a Run. A stream that is missing, cut mid-line by the time cap, or full of
    shapes this parser has never seen leaves the counts at zero and says nothing — the Findings are
    what a Run is for, and they are not worth losing over the line that says what they cost.
    """
    totals = Tokens()
    tools: Counter[str] = Counter()
    counted: set[str] = set()
    for event in _events(path):
        kind = event.get("type")
        if kind == "turn.completed":
            totals = _sum(totals, _tokens(event.get("usage")))
        elif kind == "item.completed" and (tool := _tool(event.get("item"), counted)):
            tools[tool] += 1
    return stats.model_copy(
        update={
            "input_tokens": totals.input,
            "output_tokens": totals.output,
            "reasoning_tokens": totals.reasoning,
            "cache_read_tokens": totals.cache_read,
            "tool_calls": dict(tools.most_common()),
        }
    )


def _tool(item: object, counted: set[str]) -> str | None:
    """
    The tool one completed item used, or None for an item that is not a tool call. An MCP call
    is named by its server and tool; the built-ins by their item type. An item id seen before is
    not counted again: a stable id is the stream's own word that it is the same call.
    """
    if not isinstance(item, dict) or item.get("type") not in TOOL_ITEMS:
        return None
    identity = _name(item.get("id"))
    if identity is not None:
        if identity in counted:
            return None
        counted.add(identity)
    if item["type"] == "mcp_tool_call":
        return f"{item.get('server')}.{item.get('tool')}"
    return str(item["type"])


def _tokens(reported: object) -> Tokens:
    """
    One turn's usage. Codex reports cached input inside `input_tokens` (its own `non_cached_input`
    is the difference) and reasoning inside `output_tokens` (it prints it as "output N (reasoning
    M)"), so both are taken out here rather than counted twice.
    """
    if not isinstance(reported, dict):
        return Tokens()
    cached = _count(reported.get("cached_input_tokens"))
    reasoning = _count(reported.get("reasoning_output_tokens"))
    return Tokens(
        input=max(_count(reported.get("input_tokens")) - cached, 0),
        output=max(_count(reported.get("output_tokens")) - reasoning, 0),
        reasoning=reasoning,
        cache_read=cached,
    )


def _sum(running: Tokens, step: Tokens) -> Tokens:
    """The running total with one more turn's tokens in it."""
    return Tokens(
        input=running.input + step.input,
        output=running.output + step.output,
        reasoning=running.reasoning + step.reasoning,
        cache_read=running.cache_read + step.cache_read,
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
