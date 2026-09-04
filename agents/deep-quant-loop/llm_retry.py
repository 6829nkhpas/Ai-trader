"""Quota-aware LLM invocation — absorb per-minute 429s the client's backoff cannot.

Why the client's own retries are not enough
-------------------------------------------
``ChatOpenAI`` is already built with ``max_retries`` (default 4) and the OpenAI client
honors ``Retry-After`` — which absorbs per-second throttles. But its exponential
backoff spans roughly fifteen seconds in total, and the gateway's per-MINUTE usage
quota answers ``429 ... (reset after 1m)``: every client-side attempt lands inside the
same closed window, the retries exhaust, and the whole analysis run dies on a wait
whose exact length the provider stated in the error body. Measured in production:
33 of 38 failed messages were exactly this shape.

So this wrapper does the one thing the client cannot: parse the reset hint out of the
429 body, sleep it out (capped), and try again. It wraps ONLY genuine rate-limit
errors — everything else re-raises unchanged on the first attempt.

Sync on purpose: every LLM ``.invoke()`` in ``graph.py`` runs inside LangGraph's
executor thread, so ``time.sleep`` here blocks no event loop and stalls no SSE stream
machinery (the run's heartbeat tracker beats per node step, and the stall threshold is
far above the capped wait).

Separate module rather than more of ``graph.py`` so the logic has a runnable check
that does not need the full graph import (LLM construction, tool binding, prompt
loading) just to test a regex and a loop.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

# How many quota waits a single LLM call may absorb before the error propagates.
ENV_QUOTA_RETRIES = "LLM_QUOTA_RETRIES"
DEFAULT_QUOTA_RETRIES = 2

# Upper bound on one wait, so a pathological hint ("reset after 4h") cannot park a
# live run for hours — past the cap the honest outcome is the error itself.
ENV_QUOTA_WAIT_CAP = "LLM_QUOTA_WAIT_CAP_SECS"
DEFAULT_QUOTA_WAIT_CAP_S = 120.0

# A 429 whose body carries no parseable hint still gets one conservative
# minute-window wait: the observed quotas reset per minute, and 65s covers a full
# window plus clock skew.
_NO_HINT_WAIT_S = 65.0

# Padding added to a parsed hint, because "reset after 1m" is truncated toward zero
# by the provider (59.4s prints as 59s) and arriving a hair early wastes the retry.
_HINT_PAD_S = 2.0

_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

_RESET_HINT = re.compile(r"reset(?:s)?\s+after\s+(\d+(?:\.\d+)?)\s*(ms|s|m|h)\b", re.IGNORECASE)


def _quota_retries() -> int:
    raw = (os.getenv(ENV_QUOTA_RETRIES) or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_QUOTA_RETRIES
    except ValueError:
        return DEFAULT_QUOTA_RETRIES
    return max(0, value)


def _wait_cap_s() -> float:
    raw = (os.getenv(ENV_QUOTA_WAIT_CAP) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_QUOTA_WAIT_CAP_S
    except ValueError:
        return DEFAULT_QUOTA_WAIT_CAP_S
    return max(1.0, value)


def is_rate_limit_error(exc: Exception) -> bool:
    """True only for a genuine HTTP 429 / rate-limit error.

    Checked by the exception's own ``status_code`` and class name rather than by
    searching the message for "429" — an error string that merely CONTAINS 429 (a
    price, a line number) must not trigger a sixty-second sleep and a retry of a
    call that failed for a real reason.
    """
    if getattr(exc, "status_code", None) == 429:
        return True
    return exc.__class__.__name__ == "RateLimitError"


def reset_hint_seconds(exc: Exception) -> Optional[float]:
    """The provider's own reset window in seconds, or ``None`` when it gave none.

    Parses the "(reset after 1m)" / "(reset after 30s)" fragment the gateway puts in
    the 429 body. Purely lexical, never raises.
    """
    match = _RESET_HINT.search(str(exc))
    if not match:
        return None
    try:
        return float(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
    except (ValueError, KeyError):  # pragma: no cover - the regex admits only valid forms
        return None


def invoke_with_quota_retry(runnable: Any, messages: Any, *, sleep=time.sleep) -> Any:
    """``runnable.invoke(messages)``, absorbing per-minute quota 429s.

    Any non-rate-limit error re-raises immediately and unchanged. A rate-limit error
    sleeps out the provider's stated reset window (padded, capped) and retries, up to
    ``LLM_QUOTA_RETRIES`` times; when the budget is spent the last error propagates,
    so a genuinely exhausted daily quota still fails honestly rather than looping.

    ``sleep`` is injectable so the check for this logic runs in milliseconds.
    """
    attempts = _quota_retries()
    for attempt in range(attempts + 1):
        try:
            return runnable.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - inspected, then re-raised or retried
            if attempt >= attempts or not is_rate_limit_error(exc):
                raise
            hint = reset_hint_seconds(exc)
            wait = min((hint + _HINT_PAD_S) if hint is not None else _NO_HINT_WAIT_S, _wait_cap_s())
            print(
                f"[llm_retry] 429 rate limit; waiting {wait:.0f}s then retrying "
                f"(attempt {attempt + 1}/{attempts})."
            )
            sleep(wait)
    raise RuntimeError("unreachable")  # pragma: no cover - the loop always returns or raises
