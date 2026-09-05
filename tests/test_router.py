from app.providers import Outcome, ScriptedProvider
from app.router import CircuitBreaker, ProviderRouter


def test_breaker_opens_after_threshold():
    b = CircuitBreaker(threshold=3, reset_after_seconds=100)
    assert not b.is_open()
    b.record_failure()
    b.record_failure()
    assert not b.is_open()
    b.record_failure()
    assert b.is_open()


def test_breaker_success_resets_count():
    b = CircuitBreaker(threshold=2, reset_after_seconds=100)
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert not b.is_open()


def test_breaker_half_opens_after_reset_window():
    b = CircuitBreaker(threshold=1, reset_after_seconds=0)
    b.record_failure()
    # reset window of 0 means the next check half-opens immediately
    assert not b.is_open()


def test_router_skips_open_provider():
    r = ProviderRouter(
        [ScriptedProvider("P1", [Outcome.SUCCESS]), ScriptedProvider("P2", [Outcome.SUCCESS])],
        threshold=2,
    )
    r.record_failure("P1")
    r.record_failure("P1")
    assert [p.name for p in r.available()] == ["P2"]


def test_router_provider_lookup():
    p = ScriptedProvider("P1", [Outcome.SUCCESS])
    r = ProviderRouter([p])
    assert r.provider("P1") is p
