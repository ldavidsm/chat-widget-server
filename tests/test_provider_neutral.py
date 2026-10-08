"""The core must work with no provider SDK installed at all.

Someone running GPT or Gemini installs the base package and writes their own
agent; nothing here may drag in the Anthropic SDK. These run in a subprocess
with `anthropic` blocked at the import system, because the test session itself
has it installed.
"""

import subprocess
import sys
import textwrap

BLOCK_ANTHROPIC = """
import sys

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ModuleNotFoundError(f"No module named '{name}'")
        return None

sys.meta_path.insert(0, Blocker())
"""


def run_without_anthropic(body: str) -> str:
    script = BLOCK_ANTHROPIC + textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_package_imports_without_the_anthropic_sdk():
    out = run_without_anthropic(
        """
        import chat_widget_server as cws
        print(cws.__version__)
        """
    )
    assert out


def test_the_whole_route_works_without_the_anthropic_sdk():
    out = run_without_anthropic(
        """
        import json
        from fastapi.testclient import TestClient
        from chat_widget_server import ScriptedAgent, blocks, create_app

        agent = ScriptedAgent(
            rules=[(["cita"], "Elige el día", [blocks.calendar(service="Facial")])],
            delay=0,
        )
        client = TestClient(create_app(agent, allowed_origins=["https://x.com"]))

        response = client.post(
            "/chat",
            json={"message": "una cita", "sessionId": "s1"},
            headers={"accept": "text/event-stream"},
        )
        frames = [l[6:] for l in response.text.splitlines() if l.startswith("data: ")]
        print(json.dumps(frames))
        """
    )
    frames = __import__("json").loads(out)

    assert frames[-1] == "[DONE]"
    assert any("calendar" in frame for frame in frames)


def test_blocks_sessions_and_limits_need_no_provider():
    out = run_without_anthropic(
        """
        import asyncio
        from chat_widget_server import MemorySessionStore, RateLimiter, blocks

        store = MemorySessionStore()
        asyncio.run(store.save("s1", [{"role": "user", "content": "hola"}]))

        print(
            blocks.calendar(service="x")["type"],
            RateLimiter(1).check("a"),
            len(asyncio.run(store.load("s1"))),
        )
        """
    )
    assert out == "calendar None 1"


def test_asking_for_the_claude_agent_explains_the_extra():
    out = run_without_anthropic(
        """
        import chat_widget_server as cws
        try:
            cws.Agent
        except ModuleNotFoundError as exc:
            print(str(exc))
        """
    )
    assert "chat-widget-server[claude]" in out
    assert "AgentProtocol" in out, "the message should point elsewhere, not just fail"


def test_an_unknown_attribute_still_raises_attribute_error():
    out = run_without_anthropic(
        """
        import chat_widget_server as cws
        try:
            cws.NopeNotHere
        except AttributeError as exc:
            print("AttributeError")
        """
    )
    assert out == "AttributeError"
