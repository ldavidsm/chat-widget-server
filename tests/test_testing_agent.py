"""The scripted agent is what lets someone build a front end for free."""

import asyncio
import json

from fastapi.testclient import TestClient

from chat_widget_server import BlockEmitted, Completed, ScriptedAgent, TextChunk, blocks, create_app


def run(coro):
    return asyncio.run(coro)


async def collect(stream):
    return [event async for event in stream]


def test_a_keyword_rule_wins_and_carries_its_blocks():
    agent = ScriptedAgent(
        rules=[(["cita", "reservar"], "Elige el día", [blocks.calendar(service="Facial")])],
        delay=0,
    )

    events = run(collect(agent.stream("quiero una cita")))

    assert "".join(e.text for e in events if isinstance(e, TextChunk)) == "Elige el día"
    assert [e.block for e in events if isinstance(e, BlockEmitted)] == [
        {"type": "calendar", "service": "Facial"}
    ]


def test_the_fallback_answers_anything_unmatched():
    agent = ScriptedAgent(rules=[(["cita"], "x", [])], fallback="No te sigo.", delay=0)
    assert run(agent.reply("hola")).text == "No te sigo."


def test_a_script_function_can_replace_the_rules():
    agent = ScriptedAgent(script=lambda msg: (f"dijiste {msg}", []), delay=0)
    assert run(agent.reply("hey")).text == "dijiste hey"


def test_it_ends_with_completed_and_a_usable_history():
    agent = ScriptedAgent(fallback="hola", delay=0)
    completed = run(collect(agent.stream("hey", [{"role": "user", "content": "antes"}])))[-1]

    assert isinstance(completed, Completed)
    assert [m["role"] for m in completed.messages] == ["user", "user", "assistant"]


def test_streaming_does_not_lose_or_add_spaces():
    agent = ScriptedAgent(fallback="una frase con varias palabras", delay=0)
    events = run(collect(agent.stream("hey")))
    assert "".join(e.text for e in events if isinstance(e, TextChunk)) == "una frase con varias palabras"


def test_it_drops_into_the_real_app_untouched():
    agent = ScriptedAgent(
        rules=[(["cita"], "Elige el día", [blocks.calendar()])], delay=0
    )
    client = TestClient(create_app(agent, allowed_origins=["http://localhost:8000"]))

    response = client.post(
        "/chat", json={"message": "una cita"}, headers={"accept": "text/event-stream"}
    )
    payloads = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]

    assert {"blocks": [{"type": "calendar"}]} in payloads
