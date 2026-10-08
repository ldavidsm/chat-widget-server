"""Agent-loop tests against a fake Anthropic client — no API key, no network.

These cover the parts that are easy to get wrong and impossible to see from
the outside: which turns get mirrored into the history, and when tools must
*not* be executed.
"""

import asyncio
from types import SimpleNamespace

from chat_widget_server import Agent, BlockEmitted, Completed, Refused, TextChunk, emit_block


def run(coro):
    return asyncio.run(coro)


def text_delta(text):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text))


def thinking_delta(text):
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking=text)
    )


def message(*, stop_reason="end_turn", content=(), category=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=list(content),
        stop_details=SimpleNamespace(category=category, explanation="nope") if category else None,
    )


class FakeStream:
    def __init__(self, events, final):
        self._events = events
        self._final = final

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final


class FakeRunner:
    """Yields scripted turns; `on_tools` stands in for the tools running."""

    def __init__(self, turns, on_tools=None):
        self._turns = list(turns)
        self._on_tools = on_tools or []
        self.tool_calls = 0

    def __aiter__(self):
        self._iter = iter(self._turns)
        return self

    async def __anext__(self):
        try:
            events, final = next(self._iter)
        except StopIteration:
            raise StopAsyncIteration
        return FakeStream(events, final)

    async def generate_tool_call_response(self):
        # Mirrors the real runner: calling this is what executes the tools.
        index = self.tool_calls
        self.tool_calls += 1
        if index < len(self._on_tools):
            side_effect, response = self._on_tools[index]
            if side_effect:
                side_effect()
            return response
        return None


class FakeClient:
    def __init__(self, runner):
        self._runner = runner
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self._tool_runner))

    def _tool_runner(self, **kwargs):
        self.kwargs = kwargs
        return self._runner


def agent_with(turns, on_tools=None, **options):
    runner = FakeRunner(turns, on_tools)
    client = FakeClient(runner)
    return Agent(client=client, **options), runner, client


# ── Text ──


def test_text_is_streamed_then_summed_up():
    agent, _, _ = agent_with([([text_delta("ho"), text_delta("la")], message())])

    events = run(collect(agent.stream("hey")))

    assert [e.text for e in events if isinstance(e, TextChunk)] == ["ho", "la"]
    assert events[-1].text == "hola"


def test_thinking_deltas_never_reach_the_visitor():
    agent, _, _ = agent_with([([thinking_delta("hmm"), text_delta("hola")], message())])
    events = run(collect(agent.stream("hey")))
    assert [e.text for e in events if isinstance(e, TextChunk)] == ["hola"]


def test_the_stream_always_ends_with_exactly_one_completed():
    agent, _, _ = agent_with([([text_delta("x")], message())])
    events = run(collect(agent.stream("hey")))
    assert sum(isinstance(e, Completed) for e in events) == 1
    assert isinstance(events[-1], Completed)


# ── Request shape ──


def test_the_request_carries_the_documented_defaults():
    agent, _, client = agent_with([([], message())])
    run(collect(agent.stream("hey")))

    assert client.kwargs["model"] == "claude-opus-5"
    assert client.kwargs["output_config"] == {"effort": "low"}
    assert client.kwargs["thinking"] == {"type": "adaptive"}
    assert client.kwargs["fallbacks"] == "default"
    assert client.kwargs["stream"] is True


def test_options_reach_the_request():
    agent, _, client = agent_with(
        [([], message())], effort="high", thinking=False, fallbacks=False, model="claude-sonnet-5"
    )
    run(collect(agent.stream("hey")))

    assert client.kwargs["model"] == "claude-sonnet-5"
    assert client.kwargs["output_config"] == {"effort": "high"}
    assert "thinking" not in client.kwargs
    assert "fallbacks" not in client.kwargs


def test_history_precedes_the_new_message():
    agent, _, client = agent_with([([], message())])
    history = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "¿qué tal?"}]
    run(collect(agent.stream("y ahora?", history)))

    sent = client.kwargs["messages"]
    assert sent[:2] == history
    assert sent[-1] == {"role": "user", "content": "y ahora?"}


# ── Tools and blocks ──


def test_a_block_emitted_by_a_tool_is_streamed_and_collected():
    tool_use = SimpleNamespace(type="tool_use")
    turns = [
        ([text_delta("mirando")], message(stop_reason="tool_use", content=[tool_use])),
        ([text_delta("hay hueco")], message()),
    ]
    on_tools = [
        (lambda: emit_block({"type": "calendar"}), {"role": "user", "content": "tool_result"}),
    ]
    agent, _, _ = agent_with(turns, on_tools)

    events = run(collect(agent.stream("cita")))

    assert [e.block for e in events if isinstance(e, BlockEmitted)] == [{"type": "calendar"}]
    assert events[-1].blocks == [{"type": "calendar"}]


def test_the_mirrored_history_keeps_both_halves_of_a_tool_turn():
    # A history holding an assistant tool_use without its tool_result is
    # rejected by the API on the next request.
    tool_use = SimpleNamespace(type="tool_use")
    turns = [
        ([], message(stop_reason="tool_use", content=[tool_use])),
        ([text_delta("listo")], message()),
    ]
    on_tools = [(None, {"role": "user", "content": "tool_result"})]
    agent, _, _ = agent_with(turns, on_tools)

    completed = run(collect(agent.stream("cita")))[-1]
    roles = [m["role"] for m in completed.messages]

    assert roles == ["user", "assistant", "user", "assistant"]


# ── Stop reasons that must not run tools ──


def test_a_refusal_stops_the_loop_without_executing_tools():
    turns = [([], message(stop_reason="refusal", category="cyber"))]
    agent, runner, _ = agent_with(turns)

    events = run(collect(agent.stream("hey")))
    refused = [e for e in events if isinstance(e, Refused)]

    assert refused and refused[0].category == "cyber"
    assert runner.tool_calls == 0, "generate_tool_call_response executes the tools"
    assert isinstance(events[-1], Completed)


def test_a_truncated_tool_call_stops_the_loop_without_executing_tools():
    tool_use = SimpleNamespace(type="tool_use")
    turns = [([], message(stop_reason="max_tokens", content=[tool_use]))]
    agent, runner, _ = agent_with(turns)

    completed = run(collect(agent.stream("hey")))[-1]

    assert runner.tool_calls == 0
    assert completed.truncated is True


def test_a_truncated_text_answer_is_flagged_but_kept():
    turns = [([text_delta("empiezo a cont")], message(stop_reason="max_tokens"))]
    agent, _, _ = agent_with(turns)

    completed = run(collect(agent.stream("hey")))[-1]

    assert completed.truncated is True
    assert completed.text == "empiezo a cont"


# ── reply() ──


def test_reply_returns_the_finished_turn():
    agent, _, _ = agent_with([([text_delta("hola")], message())])
    result = run(agent.reply("hey"))

    assert isinstance(result, Completed)
    assert result.text == "hola"


async def collect(stream):
    return [event async for event in stream]
