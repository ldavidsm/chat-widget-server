"""The Claude implementation of `AgentProtocol`: a streaming tool-calling loop.

This is the only module in the package that is tied to a provider. Everything
else — the widget contract, the blocks, the sessions, the rate limits, the SSE
route — is provider-neutral, so swapping this out for GPT, Gemini or a local
model means writing one class, not forking the package. See
`events.AgentProtocol`.

Your tools do two things at once. They return text — that is what the model
reads and reasons about — and they may emit a block, which is what the visitor
*sees*. `emit_block` is how a tool says "and draw a calendar with these".
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

from anthropic import AsyncAnthropic, beta_async_tool

from .blocks import Block
from .events import BlockEmitted, Completed, Refused, TextChunk, Usage, block_sink, emit_block

#: Decorator for tool functions. Always `async def`: the block sink below is a
#: contextvar, and an async tool awaited inside the request keeps that context.
tool = beta_async_tool

__all__ = ["ClaudeAgent", "Agent", "tool", "emit_block"]

# ── Agent ────────────────────────────────────────────────


class ClaudeAgent:
    """Wraps the Claude tool-calling loop for one conversation at a time.

    Construct it once at import time and reuse it: it holds no per-request
    state, so it is safe to share across requests.
    """

    def __init__(
        self,
        *,
        tools: Sequence[Any] = (),
        system: str | None = None,
        model: str = "claude-opus-5",
        max_tokens: int = 16_000,
        effort: str = "low",
        thinking: bool = True,
        max_iterations: int = 8,
        fallbacks: bool = True,
        cache: bool = True,
        client: AsyncAnthropic | None = None,
    ):
        """
        effort
            Chat routes do well at ``"low"`` — it is cheaper and faster, and
            quality holds for this kind of work. Raise to ``"medium"`` or
            ``"high"`` if your tools need real reasoning to be chosen well.
        thinking
            Adaptive thinking, on by default. It is what makes the model pick
            the right tool instead of guessing.
        fallbacks
            Server-side refusal fallbacks: if safety classifiers decline the
            request, the API retries it on another model inside the same call
            rather than leaving the visitor with nothing.
        cache
            Prompt caching. The system prompt and the tool definitions are the
            biggest, most repeated part of every request, and a tool loop sends
            them several times per message — caching them cuts the bill
            substantially and costs nothing in quality.

            For it to work, **keep the system prompt frozen**. Interpolating
            today's date or the visitor's name into it puts volatile text at
            the front of the prefix and invalidates everything after it on
            every request. Pass that kind of context in the message instead.
        """
        self.tools = list(tools)
        self.system = system
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.thinking = thinking
        self.max_iterations = max_iterations
        self.fallbacks = fallbacks
        self.cache = cache
        self._client = client or AsyncAnthropic()

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
            "output_config": {"effort": self.effort},
        }
        if self.system:
            if self.cache:
                # Explicit breakpoint on the static prefix: a guaranteed read
                # point that survives whatever happens later in `messages`.
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": self.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                kwargs["system"] = self.system

        if self.cache:
            # Automatic caching of the growing conversation tail: the
            # breakpoint moves forward on its own as turns accumulate, so each
            # request reads the whole prior prefix and writes only the delta.
            kwargs["cache_control"] = {"type": "ephemeral"}
        if self.thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if self.fallbacks:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def stream(
        self,
        message: str,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[TextChunk | BlockEmitted | Refused | Completed]:
        """Run one turn, yielding events as they happen.

        Always ends with exactly one `Completed` (or a `Refused` followed by
        `Completed`), so a consumer can rely on that to close its stream.
        """
        # Our own mirror of the conversation: the runner keeps a private copy
        # and never exposes it, and we need it for the session store.
        messages: list[dict[str, Any]] = [*(history or []), {"role": "user", "content": message}]

        sink: list[Block] = []
        token = block_sink.set(sink)
        drained = 0
        parts: list[str] = []
        truncated = False
        refusal: Refused | None = None
        usage = Usage()

        try:
            runner = self._client.beta.messages.tool_runner(
                tools=self.tools,
                messages=messages.copy(),
                stream=True,
                **self._request_kwargs(),
            )

            async for turn in runner:
                async for event in turn:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        parts.append(event.delta.text)
                        yield TextChunk(event.delta.text)

                reply = await turn.get_final_message()
                messages.append({"role": "assistant", "content": reply.content})

                if getattr(reply, "usage", None) is not None:
                    usage.add(reply.usage)

                # Both of these mean the turn is over and the tools must NOT
                # run: generate_tool_call_response() is what executes them.
                if reply.stop_reason == "refusal":
                    details = getattr(reply, "stop_details", None)
                    refusal = Refused(
                        category=getattr(details, "category", None),
                        explanation=getattr(details, "explanation", None),
                    )
                    break

                if reply.stop_reason == "max_tokens":
                    truncated = True
                    if any(block.type == "tool_use" for block in reply.content):
                        break

                tool_response = await runner.generate_tool_call_response()

                # The tools have run by now, so anything they emitted is here.
                while drained < len(sink):
                    yield BlockEmitted(sink[drained])
                    drained += 1

                if tool_response is not None:
                    messages.append(tool_response)

        finally:
            block_sink.reset(token)

        while drained < len(sink):
            yield BlockEmitted(sink[drained])
            drained += 1

        if refusal is not None:
            yield refusal

        yield Completed(
            text="".join(parts),
            blocks=list(sink),
            messages=messages,
            truncated=truncated,
            usage=usage,
        )

    async def reply(
        self,
        message: str,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> Completed:
        """Run one turn and return only the finished result."""
        completed: Completed | None = None

        async for event in self.stream(message, history):
            if isinstance(event, Completed):
                completed = event

        assert completed is not None  # stream() always ends with one
        return completed


#: Short alias. `ClaudeAgent` is the honest name; this keeps the common case
#: terse now that other providers are a documented option.
Agent = ClaudeAgent
