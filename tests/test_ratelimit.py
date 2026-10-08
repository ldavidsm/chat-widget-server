import time

import pytest

from chat_widget_server import RateLimiter


def test_allows_up_to_the_limit_then_refuses():
    limiter = RateLimiter(3)

    assert [limiter.check("a") for _ in range(3)] == [None, None, None]
    assert limiter.check("a") is not None


def test_tells_you_how_long_to_wait():
    limiter = RateLimiter(1, window_seconds=60)
    limiter.check("a")

    wait = limiter.check("a")
    assert wait is not None and 0 < wait <= 60


def test_keys_are_independent():
    limiter = RateLimiter(1)
    assert limiter.check("a") is None
    assert limiter.check("b") is None, "one visitor must not throttle another"


def test_the_window_slides_so_old_hits_stop_counting():
    limiter = RateLimiter(2, window_seconds=0.15)
    limiter.check("a")
    limiter.check("a")
    assert limiter.check("a") is not None

    time.sleep(0.2)
    assert limiter.check("a") is None


def test_reset_clears_one_key_or_all():
    limiter = RateLimiter(1)
    limiter.check("a")
    limiter.reset("a")
    assert limiter.check("a") is None

    limiter.check("a")
    limiter.reset()
    assert limiter.check("a") is None


def test_a_flood_of_distinct_keys_does_not_grow_forever():
    limiter = RateLimiter(5, window_seconds=0.01, max_keys=10)
    for i in range(500):
        limiter.check(f"key{i}")
    assert len(limiter._hits) <= 60, "idle keys must be evicted"


def test_a_zero_limit_is_a_programming_error_not_a_silent_block():
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(0)
