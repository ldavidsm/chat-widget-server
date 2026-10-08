"""A scripted agent, so you can build the front end without spending anything.

It streams canned answers and blocks with the same event types as the real
`Agent`, which means the whole path — SSE framing, blocks, CORS, your page —
can be checked end to end with no API key and no bill. What it does not check
is the model: whether your prompt and tool descriptions actually make it pick
the right tool is something only a real request can tell you.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, Sequence

from .events import BlockEmitted, Completed, TextChunk, Usage
from .blocks import Block

__all__ = ["ScriptedAgent", "Script"]

#: Either a fixed answer or a function of the incoming message.
Script = Callable[[str], "tuple[str, Sequence[Block]]"]


class ScriptedAgent:
    """Drop-in replacement for `Agent` that never calls an API.

        agent = ScriptedAgent(rules=[
            (["cita", "reservar"], "Elige el día 👇", [blocks.calendar(slots=[...])]),
        ], fallback="No he entendido.")
    """

    def __init__(
        self,
        *,
        rules: Sequence[tuple[Sequence[str], str, Sequence[Block]]] = (),
        fallback: str = "Soy una respuesta de prueba: no hay modelo detrás.",
        script: Script | None = None,
        delay: float = 0.04,
    ):
        """
        rules
            Tuples of (keywords, answer, blocks). The first rule with a
            keyword present in the message wins.
        script
            A function taking the message and returning (answer, blocks), for
            anything the keyword rules cannot express.
        delay
            Seconds between words, to make the streaming visible.
        """
        self.rules = list(rules)
        self.fallback = fallback
        self.script = script
        self.delay = delay

    def _answer(self, message: str) -> tuple[str, list[Block]]:
        if self.script is not None:
            text, blocks = self.script(message)
            return text, list(blocks)

        lowered = message.lower()
        for keywords, text, blocks in self.rules:
            if any(word.lower() in lowered for word in keywords):
                return text, list(blocks)

        return self.fallback, []

    async def stream(
        self,
        message: str,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[TextChunk | BlockEmitted | Completed]:
        text, blocks = self._answer(message)

        words = text.split(" ")
        for index, word in enumerate(words):
            chunk = word if index == len(words) - 1 else word + " "
            yield TextChunk(chunk)
            if self.delay:
                await asyncio.sleep(self.delay)

        for block in blocks:
            yield BlockEmitted(block)

        messages = [
            *(history or []),
            {"role": "user", "content": message},
            {"role": "assistant", "content": text},
        ]
        yield Completed(text=text, blocks=blocks, messages=messages, usage=Usage())

    async def reply(
        self,
        message: str,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> Completed:
        completed: Completed | None = None
        async for event in self.stream(message, history):
            if isinstance(event, Completed):
                completed = event
        assert completed is not None
        return completed
