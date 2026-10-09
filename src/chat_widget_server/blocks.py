"""Builders for the widget's UI blocks.

A block is a plain dict that travels in the reply and tells the widget to
render something structured — a calendar, cards, buttons — instead of more
text. These helpers exist so a typo becomes a Python error here rather than a
silently ignored block in someone's browser.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

Block = dict[str, Any]

#: A date *and time* carrying no offset and no trailing Z. A date on its own is
#: unambiguous and passes; anything with "+02:00" or "Z" passes.
_NAIVE_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")

__all__ = ["Block", "slot", "calendar", "cards", "quick_replies"]


def _isoformat(value: datetime | str, *, field: str) -> str:
    """Serialize an instant, refusing anything the browser would misread.

    An offset-less instant is the one mistake that produces a wrong answer with
    no error anywhere: the browser reads it as the *viewer's* local time, so a
    customer in another timezone is shown — and books — the wrong hour. We make
    it loud here instead, for a datetime object and for a string alike.
    """
    if isinstance(value, str):
        # A string is the common path — rows come out of a database driver
        # already formatted — and letting it through unchecked left the exact
        # hole this function exists to close.
        if _NAIVE_DATETIME.match(value):
            raise ValueError(
                f"{field} is a datetime with no offset ({value!r}). The browser would "
                "read it as the viewer's local time, not your business's. Append the "
                "offset ('2026-10-07T10:00:00+02:00'), or pass an aware datetime and "
                "let slot() format it."
            )
        return value

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field} is a naive datetime ({value!r}). The browser would read it as the "
            "viewer's local time, not your business's. Attach a timezone: "
            "datetime(..., tzinfo=ZoneInfo('Europe/Madrid')) or .astimezone()."
        )
    return value.isoformat()


def slot(
    start: datetime | str,
    *,
    end: datetime | str | None = None,
    duration: int | timedelta | None = None,
    resources: Sequence[str | dict[str, str]] | None = None,
    label: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One bookable opening.

    `resources` is anything reserved alongside the time — a person, a room, a
    bay, a machine. Strings are treated as generic resources; use
    ``{"label": "Ana", "type": "staff"}`` for someone whose first name should
    be shown and who reads as "with Ana" in the confirmation.
    """
    payload: dict[str, Any] = {"start": _isoformat(start, field="start")}

    if end is not None:
        payload["end"] = _isoformat(end, field="end")

    if duration is not None:
        minutes = int(duration.total_seconds() // 60) if isinstance(duration, timedelta) else int(duration)
        if minutes <= 0:
            raise ValueError(f"duration must be positive, got {minutes} minutes")
        payload["duration"] = minutes

    if resources:
        payload["resources"] = [
            r if isinstance(r, dict) else {"label": str(r), "type": "resource"} for r in resources
        ]

    if label is not None:
        payload["label"] = label

    payload.update(extra)
    return payload


def calendar(
    *,
    slots: Iterable[dict[str, Any]] | None = None,
    service: str | None = None,
    title: str | None = None,
    months_ahead: int | None = None,
) -> Block:
    """A date picker.

    Pass `slots` to hand the openings over directly. Leave it out when the
    widget was registered with its own ``loadSlots`` and will fetch per month.
    """
    block: Block = {"type": "calendar"}

    if slots is not None:
        block["slots"] = list(slots)
    if service is not None:
        block["service"] = service
    if title is not None:
        block["title"] = title
    if months_ahead is not None:
        block["monthsAhead"] = months_ahead

    return block


def cards(items: Sequence[dict[str, Any]]) -> Block:
    """A list of cards. Each item takes title, text, image, url, button, value.

    An item with `url` renders as a link; one with `value` sends that text back
    into the conversation as if the visitor had typed it.
    """
    if not items:
        raise ValueError("cards() needs at least one item")
    return {"type": "cards", "items": list(items)}


def quick_replies(options: Sequence[str | dict[str, str]]) -> Block:
    """Suggested replies as buttons.

    Strings are used as both label and value; use ``{"label": ..., "value": ...}``
    when the visible text and what gets sent should differ.
    """
    if not options:
        raise ValueError("quick_replies() needs at least one option")
    return {"type": "quickReplies", "options": list(options)}
