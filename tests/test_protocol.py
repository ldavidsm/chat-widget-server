import pytest
from pydantic import ValidationError

from chat_widget_server import ChatReply, ChatTurn


def test_accepts_the_camelcase_the_widget_sends():
    turn = ChatTurn.model_validate({"message": "hola", "sessionId": "sess_1"})
    assert turn.session_id == "sess_1"


def test_ignores_fields_the_widget_adds():
    # The widget sends `timestamp` and may gain more; unknown keys must not 422.
    turn = ChatTurn.model_validate({"message": "hola", "whatever": 1, "timestamp": "2026-10-05"})
    assert turn.message == "hola"


def test_history_is_parsed_into_turns():
    turn = ChatTurn.model_validate(
        {"message": "y el martes?", "history": [{"role": "user", "content": "hola"}]}
    )
    assert turn.history[0].role == "user"


def test_an_empty_message_is_rejected():
    with pytest.raises(ValidationError):
        ChatTurn.model_validate({"message": ""})


def test_a_bad_history_role_is_rejected():
    with pytest.raises(ValidationError):
        ChatTurn.model_validate({"message": "x", "history": [{"role": "system", "content": "·"}]})


def test_reply_payload_matches_what_the_widget_reads():
    payload = ChatReply(reply="hola", blocks=[{"type": "calendar"}]).to_payload()
    assert payload == {"reply": "hola", "blocks": [{"type": "calendar"}]}


def test_quick_replies_are_serialized_camelcase_and_dropped_when_unset():
    assert "quickReplies" not in ChatReply(reply="x").to_payload()
    assert ChatReply(reply="x", quick_replies=["Yes"]).to_payload()["quickReplies"] == ["Yes"]
