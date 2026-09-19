"""Run stats parsed out of the Reviewer's event stream, against a real capture of one."""

import json
from pathlib import Path

from deep_review.events import read_stats
from deep_review.report import RunStats

# An excerpt of a real opencode stream, captured from the Run in #2 with tool output stripped.
CAPTURE = Path(__file__).parent / "fixtures" / "opencode-events.jsonl"


def parse(path: Path) -> RunStats:
    """The stats a Run would report, off the stream at `path`."""
    return read_stats(path, RunStats(agent="opencode", model="zai-coding-plan/glm-5.3"))


def stream(tmp_path: Path, *events: object) -> Path:
    """A stream file holding these events, one JSON object per line."""
    path = tmp_path / "agent-events.jsonl"
    path.write_text("".join(f"{json.dumps(event)}\n" for event in events), encoding="utf-8")
    return path


def step(identity: str, tokens: object) -> dict[str, object]:
    """A step-finish event, spelled as opencode spells it: underscore outside, hyphen inside."""
    part = {"id": identity, "type": "step-finish", "tokens": tokens}
    return {"type": "step_finish", "part": part}


def call(identity: str, tool: str) -> dict[str, object]:
    """One tool call, in the terminal state opencode prints tool parts in."""
    part = {"callID": identity, "type": "tool", "tool": tool, "state": {"status": "completed"}}
    return {"type": "tool_use", "part": part}


def test_a_real_stream_gives_its_token_and_tool_counts() -> None:
    stats = parse(CAPTURE)

    # Five steps, and the stream's own `tokens.total` is input + output + reasoning + cache.read,
    # which is what makes `input` the fresh half of the input rather than all of it.
    assert stats.input_tokens == 23536
    assert stats.output_tokens == 532
    assert stats.reasoning_tokens == 1123
    assert stats.cache_read_tokens == 303040
    assert stats.tool_calls == {"bash": 3, "read": 1}


def test_the_reviewer_and_the_time_it_took_survive_the_parse() -> None:
    stats = read_stats(CAPTURE, RunStats(agent="opencode", model="glm-5.3", seconds=42.1))

    assert (stats.agent, stats.model, stats.seconds) == ("opencode", "glm-5.3", 42.1)


def test_tools_are_counted_most_used_first(tmp_path: Path) -> None:
    calls = [call(f"c{n}", name) for n, name in enumerate(["read", "bash", "bash"])]

    assert list(parse(stream(tmp_path, *calls)).tool_calls) == ["bash", "read"]


def test_a_part_that_arrives_twice_is_counted_once(tmp_path: Path) -> None:
    tokens = {"input": 10, "output": 1, "reasoning": 0, "cache": {"read": 5}}
    bash = call("call_1", "bash")

    stats = parse(stream(tmp_path, step("prt_1", tokens), step("prt_1", tokens), bash, bash))

    assert (stats.input_tokens, stats.cache_read_tokens) == (10, 5)
    assert stats.tool_calls == {"bash": 1}


def test_a_stream_cut_mid_line_keeps_the_steps_it_finished(tmp_path: Path) -> None:
    path = stream(tmp_path, step("prt_1", {"input": 900, "output": 20}))
    path.write_text(path.read_text(encoding="utf-8") + '{"type":"step_fin', encoding="utf-8")

    stats = parse(path)

    assert (stats.input_tokens, stats.output_tokens) == (900, 20)


def test_a_stream_that_is_missing_or_nonsense_leaves_the_counts_at_zero(tmp_path: Path) -> None:
    nonsense = tmp_path / "nonsense.jsonl"
    nonsense.write_text("not json at all\n\n[]\n3\n{}\n", encoding="utf-8")

    for stats in (parse(tmp_path / "never-written.jsonl"), parse(nonsense)):
        assert (stats.input_tokens, stats.output_tokens, stats.tool_calls) == (0, 0, {})


def test_token_counts_the_stream_did_not_report_as_numbers_are_ignored(tmp_path: Path) -> None:
    shapes = [
        step("prt_1", {"input": 100, "output": None, "reasoning": "lots", "cache": "none"}),
        step("prt_2", {"input": True, "cache": {"read": 7}}),
        step("prt_3", "no tokens at all"),
    ]

    stats = parse(stream(tmp_path, *shapes))

    assert (stats.input_tokens, stats.output_tokens, stats.reasoning_tokens) == (100, 0, 0)
    assert stats.cache_read_tokens == 7
