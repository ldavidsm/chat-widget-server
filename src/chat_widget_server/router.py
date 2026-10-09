"""FastAPI wiring: the endpoint the widget talks to.

One route handles both widget modes by content negotiation. The widget sends
``Accept: text/event-stream`` when it was configured with ``stream: true``, so
the same URL answers either Server-Sent Events or a single JSON object.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Sequence

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .events import AgentProtocol, BlockEmitted, Completed, Refused, TextChunk
from .protocol import ChatReply, ChatTurn
from .ratelimit import Limiter, RateLimiter
from .sessions import MemorySessionStore, SessionStore

logger = logging.getLogger("chat_widget_server")

__all__ = ["create_router", "create_app"]

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx buffers SSE into uselessness without this.
    "X-Accel-Buffering": "no",
}

DEFAULT_ERROR = "Lo siento, ha habido un problema. ¿Puedes intentarlo de nuevo?"
DEFAULT_THROTTLED = "Vas un poco rápido 😅 Espera unos segundos y sigo contigo."


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _client_ip(request: Request, trust_forwarded_for: bool, trusted_proxy_hops: int) -> str:
    """Best-effort caller identity for rate limiting.

    `X-Forwarded-For` is only read when you opt in: anyone can set that header,
    so trusting it without a proxy in front turns the IP limit into a header
    the caller chooses. Behind a proxy you do need it, or every request looks
    like the proxy and shares one bucket.

    *Which* entry to read matters as much as whether to read it. A proxy
    **appends** the address it saw, so the last entry is the only one your own
    infrastructure wrote and everything before it is whatever the caller sent.
    Reading the leftmost entry — the usual shortcut — hands the limit straight
    back to the person being limited: `X-Forwarded-For: <random>` on every
    request and the bucket is never the same twice. `trusted_proxy_hops` is how
    many proxies you actually run, counted from the outside in.
    """
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-min(max(trusted_proxy_hops, 1), len(hops))]

    return request.client.host if request.client else "unknown"


def create_router(
    agent: AgentProtocol,
    *,
    path: str = "/chat",
    store: SessionStore | None = None,
    trust_client_history: bool = False,
    max_per_minute: int = 15,
    max_per_minute_per_ip: int = 40,
    limiter: Limiter | None = None,
    ip_limiter: Limiter | None = None,
    trust_forwarded_for: bool = False,
    trusted_proxy_hops: int = 1,
    heartbeat_seconds: float = 15.0,
    error_message: str = DEFAULT_ERROR,
    throttled_message: str = DEFAULT_THROTTLED,
) -> APIRouter:
    """Build the chat route.

    trust_client_history
        Off by default, and leave it off. The widget's `history` comes from a
        browser, so a forged assistant turn is a prompt-injection channel into
        your system prompt. Turning it on is only reasonable when the store is
        stateless by design and you accept that.
    max_per_minute, max_per_minute_per_ip
        Messages allowed per session and per caller. Both are on by default
        because this endpoint spends money per call; pass 0 to disable either.
        In-process counters — see `ratelimit` for the multi-worker caveat.
    limiter, ip_limiter
        Swap in your own (anything with ``check(key) -> float | None``), e.g.
        Redis-backed, instead of the built-in windows.
    trusted_proxy_hops
        How many proxies sit in front of this app, counted from the outside in.
        Only read when `trust_forwarded_for` is on — see `_client_ip` for why
        the count is what keeps the IP limit honest.
    heartbeat_seconds
        How long a streaming turn may go without producing anything before a
        keep-alive comment is written. A tool-calling turn is routinely quiet
        for 20-40 seconds while the model thinks and the tools run, and nginx,
        Cloudflare and most PaaS proxies close an SSE response that has gone
        idle — the visitor then gets a half answer with no error anywhere.
        Pass 0 to disable.
    """
    router = APIRouter()
    sessions = store if store is not None else MemorySessionStore()

    session_limiter = limiter or (RateLimiter(max_per_minute) if max_per_minute else None)
    caller_limiter = ip_limiter or (
        RateLimiter(max_per_minute_per_ip) if max_per_minute_per_ip else None
    )

    async def _history(turn: ChatTurn, session_id: str) -> list[dict[str, Any]]:
        stored = await sessions.load(session_id)
        if stored:
            return stored
        if trust_client_history and turn.history:
            return [{"role": h.role, "content": h.content} for h in turn.history]
        return []

    def _throttle(session_id: str, request: Request) -> float | None:
        if caller_limiter is not None:
            caller = _client_ip(request, trust_forwarded_for, trusted_proxy_hops)
            wait = caller_limiter.check(f"ip:{caller}")
            if wait is not None:
                return wait
        if session_limiter is not None:
            return session_limiter.check(f"session:{session_id}")
        return None

    @router.post(path)
    async def chat(turn: ChatTurn, request: Request):  # noqa: ANN202
        session_id = turn.session_id or f"anon_{uuid.uuid4().hex[:12]}"

        wait = _throttle(session_id, request)
        if wait is not None:
            logger.info("throttled (session=%s, retry_after=%ss)", session_id, wait)
            return JSONResponse(
                ChatReply(reply=throttled_message).to_payload(),
                status_code=429,
                headers={"Retry-After": str(int(wait) + 1)},
            )

        history = await _history(turn, session_id)
        wants_stream = "text/event-stream" in request.headers.get("accept", "")

        def _log_usage(result: Completed) -> None:
            usage = result.usage
            logger.info(
                "turn done (session=%s, requests=%s, in=%s, out=%s, cache_read=%s, cache_write=%s)",
                session_id, usage.requests, usage.input, usage.output,
                usage.cache_read, usage.cache_write,
            )

        if not wants_stream:
            try:
                result = await agent.reply(turn.message, history)
            except Exception:
                logger.exception("chat failed (session=%s)", session_id)
                return JSONResponse(ChatReply(reply=error_message).to_payload(), status_code=200)

            _log_usage(result)
            await sessions.save(session_id, result.messages)
            return JSONResponse(
                ChatReply(reply=result.text or error_message, blocks=result.blocks).to_payload()
            )

        async def events() -> AsyncIterator[str]:
            streamed_any = False
            apologized = False

            # The agent runs in its own task feeding a queue rather than being
            # iterated directly, so that a quiet stretch can be filled with a
            # keep-alive instead of looking like a dead connection. See the
            # `heartbeat_seconds` note on create_router.
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

            async def pump() -> None:
                try:
                    async for event in agent.stream(turn.message, history):
                        await queue.put(("event", event))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # handed over, not swallowed
                    await queue.put(("error", exc))
                finally:
                    await queue.put(("end", None))

            pumping = asyncio.create_task(pump())

            try:
                while True:
                    if heartbeat_seconds:
                        try:
                            kind, payload = await asyncio.wait_for(
                                queue.get(), timeout=heartbeat_seconds
                            )
                        except asyncio.TimeoutError:
                            # An SSE comment: it keeps proxies from calling the
                            # connection idle, and the widget skips any line
                            # starting with ':' so nothing reaches the thread.
                            yield ": keepalive\n\n"
                            continue
                    else:
                        kind, payload = await queue.get()

                    if kind == "end":
                        break

                    if kind == "error":
                        logger.error(
                            "stream failed (session=%s)", session_id, exc_info=payload
                        )
                        # Only apologize if the visitor has not already seen an
                        # answer start; we cannot retract what is on their screen.
                        if not streamed_any and not apologized:
                            yield _sse({"text": error_message})
                            apologized = True
                        continue

                    event = payload

                    if await request.is_disconnected():
                        logger.info("visitor left mid-answer (session=%s)", session_id)
                        return

                    if isinstance(event, TextChunk):
                        streamed_any = True
                        yield _sse({"text": event.text})

                    elif isinstance(event, BlockEmitted):
                        yield _sse({"blocks": [event.block]})

                    elif isinstance(event, Refused):
                        logger.warning(
                            "refused (session=%s, category=%s)", session_id, event.category
                        )
                        if not streamed_any:
                            yield _sse({"text": error_message})
                            apologized = True

                    elif isinstance(event, Completed):
                        # `apologized` matters here: a refusal is followed by a
                        # Completed carrying no text, and without the flag the
                        # visitor reads the same apology twice.
                        if not streamed_any and not event.blocks and not apologized:
                            yield _sse({"text": error_message})
                            apologized = True
                        _log_usage(event)
                        await sessions.save(session_id, event.messages)
            finally:
                pumping.cancel()
                yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)

    return router


def create_app(
    agent: AgentProtocol,
    *,
    allowed_origins: Sequence[str],
    path: str = "/chat",
    store: SessionStore | None = None,
    trust_client_history: bool = False,
    title: str = "chat-widget-server",
    **router_options: Any,
) -> FastAPI:
    """A ready app with CORS locked to the sites you name.

    `allowed_origins` is required on purpose: the widget runs on someone's
    page and calls this from the browser, so a permissive `*` would let any
    site on the internet spend your tokens. Extra keyword arguments go through
    to `create_router` (rate limits, custom limiters, messages).
    """
    if not allowed_origins:
        raise ValueError(
            "allowed_origins is required — list the sites allowed to embed the widget, "
            "e.g. ['https://clinica.com']. Never '*': any page could then use your API."
        )
    if "*" in allowed_origins:
        raise ValueError("'*' is not accepted: name the origins that may call this endpoint.")

    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title=title)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization"],
        expose_headers=["Retry-After"],
        max_age=600,
    )
    app.include_router(
        create_router(
            agent,
            path=path,
            store=store,
            trust_client_history=trust_client_history,
            **router_options,
        )
    )

    @app.get("/health")
    async def health():  # noqa: ANN202
        return {"ok": True}

    return app
