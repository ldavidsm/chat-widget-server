from datetime import datetime, timedelta, timezone

import pytest

from chat_widget_server import blocks


def test_slot_refuses_a_naive_datetime():
    # The whole point: a naive instant is read as the viewer's local time.
    with pytest.raises(ValueError, match="naive datetime"):
        blocks.slot(datetime(2026, 10, 7, 10))


def test_slot_accepts_an_aware_datetime():
    out = blocks.slot(datetime(2026, 10, 7, 10, tzinfo=timezone.utc))
    assert out["start"] == "2026-10-07T10:00:00+00:00"


def test_slot_passes_strings_through_untouched():
    # Already-formatted values from a database driver are left alone.
    assert blocks.slot("2026-10-07T10:00:00+02:00")["start"] == "2026-10-07T10:00:00+02:00"


def test_slot_derives_minutes_from_a_timedelta():
    out = blocks.slot("2026-10-07T10:00:00+02:00", duration=timedelta(hours=1, minutes=30))
    assert out["duration"] == 90


def test_slot_rejects_a_nonpositive_duration():
    with pytest.raises(ValueError, match="positive"):
        blocks.slot("2026-10-07T10:00:00+02:00", duration=0)


def test_slot_normalizes_resources():
    out = blocks.slot(
        "2026-10-07T10:00:00+02:00",
        resources=["Bay 2", {"label": "Ana", "type": "staff"}],
    )
    assert out["resources"] == [
        {"label": "Bay 2", "type": "resource"},
        {"label": "Ana", "type": "staff"},
    ]


def test_slot_end_must_also_be_aware():
    with pytest.raises(ValueError, match="end is a naive datetime"):
        blocks.slot("2026-10-07T10:00:00+02:00", end=datetime(2026, 10, 7, 13))


def test_calendar_omits_what_was_not_given():
    assert blocks.calendar() == {"type": "calendar"}


def test_calendar_carries_slots_and_service():
    out = blocks.calendar(service="Facial", slots=[blocks.slot("2026-10-07T10:00:00+02:00")])
    assert out["type"] == "calendar"
    assert out["service"] == "Facial"
    assert len(out["slots"]) == 1


def test_calendar_uses_the_camelcase_the_widget_reads():
    assert blocks.calendar(months_ahead=6)["monthsAhead"] == 6


def test_cards_and_quick_replies_shapes():
    assert blocks.cards([{"title": "x"}]) == {"type": "cards", "items": [{"title": "x"}]}
    assert blocks.quick_replies(["Yes"]) == {"type": "quickReplies", "options": ["Yes"]}


@pytest.mark.parametrize("builder", [blocks.cards, blocks.quick_replies])
def test_empty_blocks_are_a_mistake_not_a_no_op(builder):
    with pytest.raises(ValueError):
        builder([])
