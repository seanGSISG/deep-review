"""Run stats parsed out of the Reviewer's `codex exec --json` stream, against a real capture."""

import json
from pathlib import Path

from deep_review.events import codex_stats
from deep_review.report import RunStats

# A real stream: the live test's Run against the planted divide-by-zero, paths and owners scrubbed.
CAPTURE = Path(__file__).parent / "fixtures" / "codex-events.jsonl"


def stats(path: Path) -> RunStats:
    """The stats a Run would report, off the stream at `path`."""
    return codex_stats(path, RunStats(agent="codex", model="gpt-6.1-sol", effort="medium"))


def stream(tmp_path: Path, *events: object) -> Path:
    """A stream file holding these events, one JSON object per line."""
    path = tmp_path / "agent-events.jsonl"
    path.write_text("".join(f"{json.dumps(event)}\n" for event in events), encoding="utf-8")
    return path


def turn(**usage: object) -> dict[str, object]:
    """A turn.completed event, which is where codex reports what a turn cost."""
    return {"type": "turn.completed", "usage": usage}


def item(identity: str, kind: str, **fields: object) -> dict[str, object]:
    """An item.completed event of this item type."""
    return {"type": "item.completed", "item": {"id": identity, "type": kind, **fields}}


def test_a_real_stream_gives_its_token_and_tool_counts() -> None:
    parsed = stats(CAPTURE)

    # 63,388 input of which 46,336 cached; 789 output of which 41 reasoning.
    assert parsed.input_tokens == 17_052
    assert parsed.cache_read_tokens == 46_336
    assert parsed.output_tokens == 748
    assert parsed.reasoning_tokens == 41
    assert parsed.tool_calls == {"command_execution": 3}


def test_the_reviewer_and_its_effort_survive_the_parse() -> None:
    parsed = stats(CAPTURE)

    assert (parsed.agent, parsed.model, parsed.effort) == ("codex", "gpt-6.1-sol", "medium")


def test_cached_input_and_reasoning_are_not_counted_twice(tmp_path: Path) -> None:
    parsed = stats(
        stream(
            tmp_path,
            turn(
                input_tokens=1_000,
                cached_input_tokens=800,
                output_tokens=100,
                reasoning_output_tokens=30,
            ),
        )
    )

    assert (parsed.input_tokens, parsed.cache_read_tokens) == (200, 800)
    assert (parsed.output_tokens, parsed.reasoning_tokens) == (70, 30)


def test_every_completed_turn_is_summed(tmp_path: Path) -> None:
    usage = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}

    parsed = stats(stream(tmp_path, turn(**usage), turn(**usage)))

    assert (parsed.input_tokens, parsed.output_tokens) == (20, 10)


def test_tools_are_counted_by_kind_most_used_first(tmp_path: Path) -> None:
    parsed = stats(
        stream(
            tmp_path,
            item("item_1", "file_change"),
            item("item_2", "command_execution"),
            item("item_3", "command_execution"),
            item("item_4", "mcp_tool_call", server="docs", tool="search"),
            item("item_5", "agent_message", text="not a tool"),
            item("item_6", "reasoning", text="nor this"),
        )
    )

    assert parsed.tool_calls == {"command_execution": 2, "file_change": 1, "docs.search": 1}
    assert list(parsed.tool_calls)[0] == "command_execution"


def test_an_item_that_arrives_twice_is_counted_once(tmp_path: Path) -> None:
    parsed = stats(
        stream(tmp_path, item("item_1", "command_execution"), item("item_1", "command_execution"))
    )

    assert parsed.tool_calls == {"command_execution": 1}


def test_started_items_are_not_counted_until_they_complete(tmp_path: Path) -> None:
    started = {"type": "item.started", "item": {"id": "item_1", "type": "command_execution"}}

    assert stats(stream(tmp_path, started)).tool_calls == {}


def test_a_stream_cut_mid_line_keeps_the_turns_it_finished(tmp_path: Path) -> None:
    path = stream(tmp_path, turn(input_tokens=10, cached_input_tokens=0, output_tokens=5))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "turn.completed", "usage": {"input_tok')

    assert stats(path).input_tokens == 10


def test_a_stream_that_is_missing_or_nonsense_leaves_the_counts_at_zero(tmp_path: Path) -> None:
    nonsense = tmp_path / "nonsense.jsonl"
    nonsense.write_text('[1, 2]\n"text"\n{"type": "turn.completed", "usage": 7}\n')

    for path in (tmp_path / "missing.jsonl", nonsense):
        parsed = stats(path)
        assert (parsed.input_tokens, parsed.output_tokens, parsed.tool_calls) == (0, 0, {})


def test_counts_the_stream_did_not_report_as_numbers_are_ignored(tmp_path: Path) -> None:
    parsed = stats(
        stream(tmp_path, turn(input_tokens="12", cached_input_tokens=True, output_tokens=None))
    )

    assert (parsed.input_tokens, parsed.cache_read_tokens, parsed.output_tokens) == (0, 0, 0)
