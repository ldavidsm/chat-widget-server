"""Prompt caching is the cheapest cost lever there is, so pin its shape."""

from types import SimpleNamespace

from chat_widget_server import Agent, Usage

from .test_agent import FakeClient, FakeRunner, collect, message, run, text_delta


def kwargs_for(**options):
    client = FakeClient(FakeRunner([([], message())]))
    agent = Agent(client=client, **options)
    run(collect(agent.stream("hey")))
    return client.kwargs


def test_the_static_system_prefix_gets_an_explicit_breakpoint():
    sent = kwargs_for(system="Eres una asistente.")

    assert sent["system"] == [
        {
            "type": "text",
            "text": "Eres una asistente.",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_the_growing_conversation_tail_is_cached_automatically():
    assert kwargs_for(system="x")["cache_control"] == {"type": "ephemeral"}


def test_caching_applies_even_without_a_system_prompt():
    sent = kwargs_for()
    assert "system" not in sent
    assert sent["cache_control"] == {"type": "ephemeral"}


def test_caching_can_be_turned_off_and_then_system_stays_plain_text():
    sent = kwargs_for(system="Eres una asistente.", cache=False)

    assert sent["system"] == "Eres una asistente."
    assert "cache_control" not in sent


# ── Usage ──


def usage_message(**counts):
    return message(), SimpleNamespace(**counts)


def test_usage_is_summed_across_the_round_trips_of_one_turn():
    tool_use = SimpleNamespace(type="tool_use")

    first = message(stop_reason="tool_use", content=[tool_use])
    first.usage = SimpleNamespace(
        input_tokens=100, output_tokens=20, cache_read_input_tokens=600, cache_creation_input_tokens=50
    )
    second = message()
    second.usage = SimpleNamespace(
        input_tokens=40, output_tokens=80, cache_read_input_tokens=700, cache_creation_input_tokens=10
    )

    client = FakeClient(
        FakeRunner(
            [([], first), ([text_delta("listo")], second)],
            on_tools=[(None, {"role": "user", "content": "tool_result"})],
        )
    )
    completed = run(collect(Agent(client=client).stream("cita")))[-1]

    assert completed.usage.requests == 2
    assert completed.usage.input == 140
    assert completed.usage.output == 100
    assert completed.usage.cache_read == 1300
    assert completed.usage.cache_write == 60


def test_a_response_without_usage_does_not_crash_the_turn():
    client = FakeClient(FakeRunner([([text_delta("hola")], message())]))
    completed = run(collect(Agent(client=client).stream("hey")))[-1]
    assert completed.usage == Usage()
