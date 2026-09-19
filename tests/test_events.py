"""Run stats parsed out of each Reviewer's event stream, against real captures of both."""

import json
from pathlib import Path

from deep_review.events import opencode_stats, pi_stats
from deep_review.report import RunStats

FIXTURES = Path(__file__).parent / "fixtures"

# Excerpts of real streams, captured from the Runs in #2 and #5 with tool output stripped.
OPENCODE_CAPTURE = FIXTURES / "opencode-events.jsonl"
PI_CAPTURE = FIXTURES / "pi-events.jsonl"


def opencode(path: Path) -> RunStats:
    """The stats a Run would report, off the opencode stream at `path`."""
    return opencode_stats(path, RunStats(agent="opencode", model="zai-coding-plan/glm-5.3"))


def pi(path: Path) -> RunStats:
    """The stats a Run would report, off the pi stream at `path`."""
    return pi_stats(path, RunStats(agent="pi", model="zai/glm-5.3", variant="medium"))


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


def message(usage: object, role: str = "assistant") -> dict[str, object]:
    """A message_end event, which is where pi reports what a message cost."""
    return {"type": "message_end", "message": {"role": role, "usage": usage}}


def execution(identity: str, tool: str) -> dict[str, object]:
    """A tool_execution_start event, which is where pi names a tool call."""
    return {"type": "tool_execution_start", "toolCallId": identity, "toolName": tool, "args": {}}


def test_a_real_opencode_stream_gives_its_token_and_tool_counts() -> None:
    stats = opencode(OPENCODE_CAPTURE)

    # Five steps, and the stream's own `tokens.total` is input + output + reasoning + cache.read,
    # which is what makes `input` the fresh half of the input rather than all of it.
    assert stats.input_tokens == 23536
    assert stats.output_tokens == 532
    assert stats.reasoning_tokens == 1123
    assert stats.cache_read_tokens == 303040
    assert stats.tool_calls == {"bash": 3, "read": 1}


def test_the_reviewer_and_the_time_it_took_survive_the_parse() -> None:
    stats = opencode_stats(OPENCODE_CAPTURE, RunStats(agent="opencode", model="glm", seconds=42.1))

    assert (stats.agent, stats.model, stats.seconds) == ("opencode", "glm", 42.1)


def test_tools_are_counted_most_used_first(tmp_path: Path) -> None:
    calls = [call(f"c{n}", name) for n, name in enumerate(["read", "bash", "bash"])]

    assert list(opencode(stream(tmp_path, *calls)).tool_calls) == ["bash", "read"]


def test_a_part_that_arrives_twice_is_counted_once(tmp_path: Path) -> None:
    tokens = {"input": 10, "output": 1, "reasoning": 0, "cache": {"read": 5}}
    bash = call("call_1", "bash")

    stats = opencode(stream(tmp_path, step("prt_1", tokens), step("prt_1", tokens), bash, bash))

    assert (stats.input_tokens, stats.cache_read_tokens) == (10, 5)
    assert stats.tool_calls == {"bash": 1}


def test_a_stream_cut_mid_line_keeps_the_steps_it_finished(tmp_path: Path) -> None:
    path = stream(tmp_path, step("prt_1", {"input": 900, "output": 20}))
    path.write_text(path.read_text(encoding="utf-8") + '{"type":"step_fin', encoding="utf-8")

    stats = opencode(path)

    assert (stats.input_tokens, stats.output_tokens) == (900, 20)


def test_a_stream_that_is_missing_or_nonsense_leaves_the_counts_at_zero(tmp_path: Path) -> None:
    nonsense = tmp_path / "nonsense.jsonl"
    nonsense.write_text("not json at all\n\n[]\n3\n{}\n", encoding="utf-8")

    for read in (opencode, pi):
        for stats in (read(tmp_path / "never-written.jsonl"), read(nonsense)):
            assert (stats.input_tokens, stats.output_tokens, stats.tool_calls) == (0, 0, {})


def test_token_counts_the_stream_did_not_report_as_numbers_are_ignored(tmp_path: Path) -> None:
    shapes = [
        step("prt_1", {"input": 100, "output": None, "reasoning": "lots", "cache": "none"}),
        step("prt_2", {"input": True, "cache": {"read": 7}}),
        step("prt_3", "no tokens at all"),
    ]

    stats = opencode(stream(tmp_path, *shapes))

    assert (stats.input_tokens, stats.output_tokens, stats.reasoning_tokens) == (100, 0, 0)
    assert stats.cache_read_tokens == 7


def test_a_real_pi_stream_gives_its_token_and_tool_counts() -> None:
    stats = pi(PI_CAPTURE)

    # Four assistant messages. The capture's own `output` sums to 2,141 with the reasoning still
    # inside it; what is reported is the 251 the model wrote on top of the 1,890 it thought.
    assert stats.input_tokens == 13282
    assert stats.output_tokens == 251
    assert stats.reasoning_tokens == 1890
    assert stats.cache_read_tokens == 17408
    assert stats.tool_calls == {"read": 4, "bash": 3}


def test_pis_reasoning_is_taken_out_of_its_output(tmp_path: Path) -> None:
    usage = {"input": 300, "output": 250, "reasoning": 200, "cacheRead": 9000, "cacheWrite": 40}

    stats = pi(stream(tmp_path, message(usage)))

    # pi counts its reasoning inside `output` and opencode counts the two side by side. Reporting
    # pi's `output` as it arrives would count the reasoning twice and make the Run look dearer.
    assert (stats.output_tokens, stats.reasoning_tokens) == (50, 200)
    assert (stats.input_tokens, stats.cache_read_tokens) == (300, 9000)


def test_pi_usage_without_a_reasoning_breakdown_is_all_output(tmp_path: Path) -> None:
    stats = pi(stream(tmp_path, message({"input": 300, "output": 250, "cacheRead": 10})))

    assert (stats.output_tokens, stats.reasoning_tokens) == (250, 0)


def test_only_pis_assistant_messages_are_counted(tmp_path: Path) -> None:
    spent = {"input": 40, "output": 5}
    events = [message(spent), message(spent, role="user"), message(spent, role="toolResult")]

    assert pi(stream(tmp_path, *events)).input_tokens == 40


def test_pi_tool_calls_are_counted_by_name_most_used_first(tmp_path: Path) -> None:
    names = ["read", "bash", "bash", "bash"]
    calls = [execution(f"toolu_{n}", name) for n, name in enumerate(names)]

    stats = pi(stream(tmp_path, *calls))

    assert stats.tool_calls == {"bash": 3, "read": 1}
    assert list(stats.tool_calls) == ["bash", "read"]


def test_a_pi_stream_cut_mid_line_keeps_the_messages_it_finished(tmp_path: Path) -> None:
    path = stream(tmp_path, message({"input": 900, "output": 20}))
    path.write_text(path.read_text(encoding="utf-8") + '{"type":"message_', encoding="utf-8")

    stats = pi(path)

    assert (stats.input_tokens, stats.output_tokens) == (900, 20)


def test_pi_usage_the_stream_did_not_report_as_numbers_is_ignored(tmp_path: Path) -> None:
    shapes = [
        message({"input": 100, "output": None, "reasoning": "lots", "cacheRead": True}),
        message("no usage at all"),
        {"type": "tool_execution_start", "toolCallId": "toolu_1", "toolName": None},
    ]

    stats = pi(stream(tmp_path, *shapes))

    assert (stats.input_tokens, stats.output_tokens, stats.reasoning_tokens) == (100, 0, 0)
    assert (stats.cache_read_tokens, stats.tool_calls) == (0, {})


def test_each_reviewer_reads_only_its_own_stream() -> None:
    """The two shapes share no ground, so a stream read by the wrong parser reports nothing."""
    crossed = (pi(OPENCODE_CAPTURE), opencode(PI_CAPTURE))

    for stats in crossed:
        assert (stats.input_tokens, stats.output_tokens, stats.tool_calls) == (0, 0, {})
