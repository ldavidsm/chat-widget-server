"""Backend for the browser chat widget — streaming, UI blocks, sessions, limits.

Everything here is provider-neutral except one class. The widget contract, the
blocks, the session memory, the rate limits and the SSE route do not know which
model answers; an agent is anything implementing `AgentProtocol`.

The Claude implementation ships with the package and needs the Anthropic SDK:

    pip install "chat-widget-server[claude]"

    from chat_widget_server import Agent, blocks, create_app, emit_block, tool

    @tool
    async def check_availability(service: str, start: str, end: str) -> str:
        '''Free openings for a service between two dates (YYYY-MM-DD).'''
        slots = await my_database.slots(service, start, end)
        emit_block(blocks.calendar(service=service, slots=slots))
        return f"{len(slots)} openings found."

    agent = Agent(tools=[check_availability], system="You book appointments.")
    app = create_app(agent, allowed_origins=["https://my-clinic.com"])

For GPT, Gemini, a local model or anything else, write your own agent against
`AgentProtocol` and pass it to the same `create_app`. The base install pulls no
provider SDK at all.
"""

from typing import TYPE_CHECKING, Any

from . import blocks
from .blocks import Block, calendar, cards, quick_replies, slot
from .events import (
    AgentProtocol,
    BlockEmitted,
    Completed,
    Event,
    Refused,
    TextChunk,
    Usage,
    emit_block,
)
from .protocol import ChatReply, ChatTurn, HistoryEntry
from .ratelimit import Limiter, RateLimiter
from .router import create_app, create_router
from .sessions import MemorySessionStore, SessionStore
from .testing import ScriptedAgent

if TYPE_CHECKING:  # pragma: no cover
    from .agent import Agent, ClaudeAgent, tool

__version__ = "0.3.0"

# The Anthropic SDK is an extra, so the Claude agent loads on first use. That
# keeps `import chat_widget_server` working for someone running GPT or Gemini.
_LAZY = {"Agent", "ClaudeAgent", "tool"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        try:
            from . import agent as _agent
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
            raise ModuleNotFoundError(
                f"{name} needs the Anthropic SDK, which is an optional extra:\n\n"
                '    pip install "chat-widget-server[claude]"\n\n'
                "Using another provider? Write an agent against AgentProtocol instead — "
                "nothing else in this package depends on a provider."
            ) from exc
        return getattr(_agent, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "Agent",
    "AgentProtocol",
    "Block",
    "BlockEmitted",
    "ChatReply",
    "ChatTurn",
    "ClaudeAgent",
    "Completed",
    "Event",
    "HistoryEntry",
    "Limiter",
    "MemorySessionStore",
    "RateLimiter",
    "Refused",
    "ScriptedAgent",
    "SessionStore",
    "TextChunk",
    "Usage",
    "blocks",
    "calendar",
    "cards",
    "create_app",
    "create_router",
    "emit_block",
    "quick_replies",
    "slot",
    "tool",
]
