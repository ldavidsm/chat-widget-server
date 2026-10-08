"""Conversation memory, keyed by the widget's session id.

The widget sends its own `history`, but it is a browser: anyone can forge an
assistant turn in it, which is a prompt-injection channel straight into your
system prompt. So the server keeps its own copy and ignores the client's
unless you explicitly opt in.

The in-memory store below is correct for one process. Swap it for Redis or
Postgres before you run more than one worker — the protocol is two methods.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

Messages = list[dict[str, Any]]


class SessionStore(Protocol):
    """Anything that can remember a conversation."""

    async def load(self, session_id: str) -> Messages: ...

    async def save(self, session_id: str, messages: Messages) -> None: ...


class MemorySessionStore:
    """Dict-backed store with a turn cap and idle expiry.

    Not for multi-process deployments, and it loses everything on restart —
    which is also why `max_turns` matters less than it looks: the cap is there
    to bound the prompt, not the memory.
    """

    def __init__(self, *, max_turns: int = 20, ttl_seconds: int = 60 * 60 * 6, max_sessions: int = 10_000):
        self._data: dict[str, tuple[float, Messages]] = {}
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions

    async def load(self, session_id: str) -> Messages:
        self._expire()
        entry = self._data.get(session_id)
        return list(entry[1]) if entry else []

    async def save(self, session_id: str, messages: Messages) -> None:
        self._expire()

        # Keep whole turns: cutting between an assistant tool_use and its
        # tool_result makes the next request invalid.
        trimmed = messages[-(self.max_turns * 2) :] if self.max_turns else messages
        while trimmed and trimmed[0].get("role") != "user":
            trimmed = trimmed[1:]

        self._data[session_id] = (time.monotonic(), trimmed)

        if len(self._data) > self.max_sessions:
            oldest = sorted(self._data.items(), key=lambda kv: kv[1][0])
            for key, _ in oldest[: len(self._data) - self.max_sessions]:
                self._data.pop(key, None)

    async def forget(self, session_id: str) -> None:
        """Drop one conversation — what a "delete my data" request needs."""
        self._data.pop(session_id, None)

    def _expire(self) -> None:
        if not self.ttl_seconds:
            return
        cutoff = time.monotonic() - self.ttl_seconds
        for key in [k for k, (seen, _) in self._data.items() if seen < cutoff]:
            self._data.pop(key, None)
