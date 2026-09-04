"""Checks for llm_retry — the quota-aware LLM invocation wrapper.

Deliberately imports ONLY ``llm_retry`` (not ``graph``), so these run without the
full LLM/tool construction. The waits are injected, so the suite runs in
milliseconds while still exercising the real retry loop.
"""

import llm_retry
from llm_retry import (
    invoke_with_quota_retry,
    is_rate_limit_error,
    reset_hint_seconds,
)


class _RateLimitError(Exception):
    """Shaped like openai.RateLimitError: carries status_code=429."""

    status_code = 429


class _Runnable:
    """Fails ``failures`` times with ``exc``, then returns 'ok'."""

    def __init__(self, failures: int, exc: Exception):
        self.failures = failures
        self.exc = exc
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return "ok"


QUOTA_429 = _RateLimitError(
    "Error code: 429 - {'error': {'message': '[codex/gpt-5.5-high] [429]: "
    "The usage limit has been reached (reset after 1m)'}}"
)


def test_reset_hint_parses_the_production_429_body():
    assert reset_hint_seconds(QUOTA_429) == 60.0
    assert reset_hint_seconds(_RateLimitError("(reset after 5s)")) == 5.0
    assert reset_hint_seconds(_RateLimitError("no hint here")) is None


def test_rate_limit_detection_is_by_status_not_by_string():
    assert is_rate_limit_error(QUOTA_429)
    # A message merely CONTAINING 429 is not a rate limit.
    assert not is_rate_limit_error(ValueError("price crossed 429.5 at line 429"))


def test_retries_a_quota_429_and_sleeps_the_stated_window(monkeypatch):
    monkeypatch.delenv(llm_retry.ENV_QUOTA_RETRIES, raising=False)
    runnable = _Runnable(failures=1, exc=QUOTA_429)
    waits = []
    assert invoke_with_quota_retry(runnable, [], sleep=waits.append) == "ok"
    assert runnable.calls == 2
    assert waits == [62.0]  # 1m hint + 2s pad, under the 120s cap


def test_a_non_rate_limit_error_raises_immediately_with_no_sleep():
    runnable = _Runnable(failures=1, exc=ValueError("boom"))
    waits = []
    try:
        invoke_with_quota_retry(runnable, [], sleep=waits.append)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert runnable.calls == 1
    assert waits == []


def test_exhausted_budget_propagates_the_429(monkeypatch):
    monkeypatch.setenv(llm_retry.ENV_QUOTA_RETRIES, "1")
    runnable = _Runnable(failures=5, exc=QUOTA_429)
    waits = []
    try:
        invoke_with_quota_retry(runnable, [], sleep=waits.append)
        raise AssertionError("expected the 429 to propagate")
    except _RateLimitError:
        pass
    assert runnable.calls == 2  # initial + 1 retry
    assert len(waits) == 1


def test_wait_is_capped(monkeypatch):
    monkeypatch.delenv(llm_retry.ENV_QUOTA_WAIT_CAP, raising=False)
    runnable = _Runnable(failures=1, exc=_RateLimitError("(reset after 4h)"))
    waits = []
    assert invoke_with_quota_retry(runnable, [], sleep=waits.append) == "ok"
    assert waits == [120.0]
