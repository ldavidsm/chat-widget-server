"""Provider-neutral core: the events an agent emits and the block sink.

Nothing here knows or cares which model is behind the conversation. An agent
is anything that can `stream()` these events — see `AgentProtocol` — so the
router, the sessions, the rate limits and the blocks all work the same whether
you run Claude, GPT, Gemini, a local model or a canned script.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, Sequence, runtime_checkable

from .blocks import Block

__all__ = [
    "AgentProtocol",
    "BlockEmitted",
    "Completed",
    "Event",
    "Refused",
    "TextChunk",
    "Usage",
    "emit_block",
    "block_sink",
]

block_sink: ContextVar[list[Block] | None] = ContextVar("chat_widget_blocks", default=None)


def emit_block(block: Block) -> None:
    """Queue a UI block from inside a tool.

    Raises outside a request so a misplaced call fails in your tests rather
    than silently dropping the calendar in production.
    """
    sink = block_sink.get()
    if sink is None:
        raise RuntimeError(
            "emit_block() was called outside a request. It only works inside a tool "
            "running under an agent's stream()/reply()."
        )
    sink.append(block)


@dataclass(slots=True)
class TextChunk:
    """A piece of the answer, as the model writes it."""

    text: str


@dataclass(slots=True)
class BlockEmitted:
    """A tool asked for something to be drawn."""

    block: Block


@dataclass(slots=True)
class Refused:
    """The provider declined the request; nothing usable was produced."""

    category: str | None = None
    explanation: str | None = None


@dataclass(slots=True)
class Usage:
    """Token counts for one turn, summed over every API round-trip in it.

    `cache_read` is the number to watch: in a healthy multi-turn loop it grows
    with the conversation while `cache_write` stays small. If `cache_read` is
    stuck at zero, something is rewriting the prefix on every request — most
    often a value interpolated into the system prompt.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    requests: int = 0

    def add(self, usage: Any) -> None:
        """Accumulate one response's usage, whatever shape the SDK gives it."""
        self.requests += 1
        self.input += getattr(usage, "input_tokens", 0) or 0
        self.output += getattr(usage, "output_tokens", 0) or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0


@dataclass(slots=True)
class Completed:
    """End of turn, with everything that was produced."""

    text: str
    blocks: list[Block] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    usage: Usage = field(default_factory=lambda: Usage())


Event = TextChunk | BlockEmitted | Refused | Completed


@runtime_checkable
class AgentProtocol(Protocol):
    """What the router needs from an agent. Implement it for any provider.

        class OpenAIAgent:
            async def stream(self, message, history=None):
                ...
                yield TextChunk(delta)
                yield Completed(text=full, messages=mirrored)

            async def reply(self, message, history=None):
                ...

    Two rules the router relies on:

    * `stream()` ends with exactly one `Completed`, always — even after a
      `Refused`. That is what closes the SSE stream and saves the session.
    * `Completed.messages` is the full conversation to persist, in the
      provider's own message format. Nothing else reads it, so its shape is
      yours; it is handed straight back as `history` on the next turn.
    """

    def stream(
        self,
        message: str,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Event]: ...

    async def reply(
        self,
        message: str,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> Completed: ...
