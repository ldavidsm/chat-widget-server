import asyncio

from chat_widget_server import MemorySessionStore


def run(coro):
    return asyncio.run(coro)


def test_round_trip():
    store = MemorySessionStore()
    run(store.save("s1", [{"role": "user", "content": "hola"}]))
    assert run(store.load("s1")) == [{"role": "user", "content": "hola"}]


def test_unknown_session_is_empty_not_an_error():
    assert run(MemorySessionStore().load("nope")) == []


def test_load_returns_a_copy_so_callers_cannot_corrupt_the_store():
    store = MemorySessionStore()
    run(store.save("s1", [{"role": "user", "content": "hola"}]))
    run(store.load("s1")).append({"role": "user", "content": "injected"})
    assert len(run(store.load("s1"))) == 1


def test_trimming_never_starts_a_history_on_an_assistant_turn():
    # A history that opens with a tool_result orphaned from its tool_use is
    # rejected by the API, so the cut has to land on a user turn.
    store = MemorySessionStore(max_turns=2)
    messages = []
    for i in range(6):
        messages.append({"role": "user", "content": f"q{i}"})
        messages.append({"role": "assistant", "content": f"a{i}"})

    run(store.save("s1", messages))
    kept = run(store.load("s1"))

    assert kept[0]["role"] == "user"
    assert len(kept) <= 4


def test_forget_drops_the_conversation():
    store = MemorySessionStore()
    run(store.save("s1", [{"role": "user", "content": "hola"}]))
    run(store.forget("s1"))
    assert run(store.load("s1")) == []


def test_session_count_is_bounded():
    store = MemorySessionStore(max_sessions=3)
    for i in range(10):
        run(store.save(f"s{i}", [{"role": "user", "content": "x"}]))
    assert len(store._data) <= 3


def test_trimming_never_orphans_a_tool_result():
    # Regression: a tool_result travels as a `user` message, so checking the
    # role alone let the cut land on one. The provider then rejects the whole
    # request — every later message in that session fails until it expires.
    # Two tool rounds per turn is six messages, which is what the default
    # max_turns cut through.
    store = MemorySessionStore()
    messages = []
    for i in range(12):
        messages.append({"role": "user", "content": f"q{i}"})
        for call in range(2):
            messages.append(
                {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}{call}"}]}
            )
            messages.append(
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}{call}"}]}
            )
        messages.append({"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]})

    run(store.save("s1", messages))
    first = run(store.load("s1"))[0]

    assert first["role"] == "user"
    assert isinstance(first["content"], str), "a history cannot open on a tool result"


def test_trimming_survives_sdk_content_objects():
    # Assistant turns are stored as the SDK handed them over, so the blocks are
    # objects with a `.type`, not dicts.
    class Block:
        def __init__(self, type):
            self.type = type

    store = MemorySessionStore(max_turns=1)
    run(store.save("s1", [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [Block("tool_use")]},
        {"role": "user", "content": [Block("tool_result")]},
        {"role": "assistant", "content": [Block("text")]},
    ]))

    kept = run(store.load("s1"))
    assert kept == [] or isinstance(kept[0]["content"], str)
