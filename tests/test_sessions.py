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
