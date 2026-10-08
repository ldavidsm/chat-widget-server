"""The wire contract with the browser widget.

The widget POSTs snake_case-ish JSON with a camelCase `sessionId`, and reads
`reply` / `blocks` back. These models pin that shape so a change here is a
deliberate, visible break rather than a silent 422.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoryEntry(BaseModel):
    """One past turn, as the widget remembers it."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str


class ChatTurn(BaseModel):
    """What the widget sends on every message."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, alias="sessionId", max_length=128)
    # Client-supplied history is ignored unless the store is told to trust it:
    # a browser can forge assistant turns, which is a prompt-injection channel.
    history: list[HistoryEntry] = Field(default_factory=list)
    timestamp: str | None = None


class ChatReply(BaseModel):
    """What a non-streaming answer looks like."""

    model_config = ConfigDict(populate_by_name=True)

    reply: str = ""
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    quick_replies: list[Any] | None = Field(default=None, serialization_alias="quickReplies")

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)
