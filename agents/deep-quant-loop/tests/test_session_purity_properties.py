"""Property-based test for classifier purity (session.py, task 2.4).

Feature: session-expiry-awareness

This module implements design **Property 2: Calculator functions are pure (no
input mutation, no network)**:

    The ``Session_Classifier`` functions — in particular the top-level
    ``classify_session`` and the pure date-math helpers (``to_local_datetime``,
    ``classify_session_phase``, ``compute_minutes_since_open`` /
    ``compute_minutes_until_close``, ``compute_expiry_context``,
    ``derive_time_favorability``) — produce NO observable change to their
    inputs, perform zero network calls, and never read the host wall clock.

This single Hypothesis property exercises all three guarantees across the whole
timestamp input space (valid epoch-millisecond numbers spanning pre-open /
in-session / post-close windows on every weekday, plus the degenerate
``None`` / ``NaN`` / ``+-inf`` / non-numeric / out-of-range inputs that drive the
Unavailable_Marker path):

  * No input mutation (R1.7): after every call the resolved ``SessionConfig`` is
    deep-equal to a snapshot taken before the call, and the (immutable) scalar
    inputs are unchanged.
  * No network (R1.1, R13.2): ``session`` imports no HTTP client (asserted at
    import time below), and a socket guard installed for the duration of each
    call raises if any network connection is attempted.
  * No host wall clock (R1.1): a clock guard replaces ``session.datetime`` with a
    wrapper that raises on ``now`` / ``utcnow`` / ``today`` (but delegates
    ``fromtimestamp``) and replaces ``time.time`` with a raising stub, proving
    classification depends only on the provided timestamp — never the host clock.

Validates: Requirements 1.1, 1.7, 13.2.

The sys.path / import pattern mirrors the sibling ``test_session_*`` and
``test_regime_purity_properties`` / ``test_of_purity_properties`` modules.
"""

import copy
import math
import os
import socket
import sys
import time as _time_mod
from datetime import datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import session  # noqa: E402
from session import (  # noqa: E402
    SessionConfig,
    classify_session,
    classify_session_phase,
    compute_expiry_context,
    compute_minutes_since_open,
    compute_minutes_until_close,
    derive_time_favorability,
    resolve_session_config,
    to_local_datetime,
)


# ─────────────────────────────────────────────────────────────────────────────
# No network client at import (R1.1, R13.2): the classifier is pure date math
# and must not pull in an HTTP client. There is no client through which it could
# reach the network.
# ─────────────────────────────────────────────────────────────────────────────


def test_session_module_imports_no_network_client():
    """Validates: Requirements 1.1, 13.2

    ``session`` performs zero network calls; it imports no ``httpx`` (or any
    other HTTP client) module attribute, so there is no client through which it
    could reach the network or any external data source.
    """
    assert not hasattr(session, "httpx"), (
        "session must not import a network client (httpx)"
    )
    for name in dir(session):
        attr = getattr(session, name)
        mod = getattr(attr, "__module__", "") or ""
        assert not str(mod).startswith(("httpx", "requests", "aiohttp", "urllib3")), (
            f"session.{name} pulls in a network client: {mod}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Guards: prove the classifier touches neither the network nor the host clock.
# ─────────────────────────────────────────────────────────────────────────────


class _NoNetworkSocket(socket.socket):
    """A ``socket.socket`` subclass that refuses to connect.

    Installed in place of ``socket.socket`` for the duration of a classify call
    so that any attempt to open a network connection raises rather than silently
    succeeding.
    """

    def connect(self, *args, **kwargs):  # noqa: D401 - guard
        raise AssertionError("session classifier attempted a network connection")

    def connect_ex(self, *args, **kwargs):  # noqa: D401 - guard
        raise AssertionError("session classifier attempted a network connection")


class _ClockGuard:
    """Stand-in for ``session.datetime`` that forbids reading the host clock.

    ``fromtimestamp`` (the only ``datetime`` constructor the classifier is
    allowed to use) delegates to the real ``datetime``; ``now`` / ``utcnow`` /
    ``today`` raise, so any reliance on the host wall clock is caught.
    """

    @staticmethod
    def fromtimestamp(*args, **kwargs):
        return datetime.fromtimestamp(*args, **kwargs)

    @staticmethod
    def now(*args, **kwargs):
        raise AssertionError("session classifier read the host clock (datetime.now)")

    @staticmethod
    def utcnow(*args, **kwargs):
        raise AssertionError("session classifier read the host clock (datetime.utcnow)")

    @staticmethod
    def today(*args, **kwargs):
        raise AssertionError("session classifier read the host clock (datetime.today)")


def _raise_clock(*args, **kwargs):
    raise AssertionError("session classifier read the host clock (time.time)")


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp generation: valid epoch-millisecond numbers spanning every weekday
# and every session window, plus the degenerate inputs that drive the
# Unavailable_Marker path, so purity is exercised across all code paths.
# ─────────────────────────────────────────────────────────────────────────────

# 2021-01-01 .. ~2031 in epoch milliseconds: comfortably representable as a
# datetime, and spread across weekdays / times-of-day so pre_open / opening /
# morning / midday / afternoon / closing / post_close and every expiry-day flag
# are all hit.
_VALID_MS = st.integers(min_value=1_609_459_200_000, max_value=1_924_991_999_000)

# Values that drive the invalid-timestamp / Unavailable_Marker path (R3.1).
_BAD_MS = st.sampled_from(
    [None, float("nan"), float("inf"), float("-inf"), "x", "", True, False, [], {}]
)

# Extreme magnitudes (out-of-range epoch values) exercise the overflow path.
_EXTREME_MS = st.sampled_from([10**18, -(10**18), 1e308, -1e308, 0, -1])

_TIMESTAMP = st.one_of(_VALID_MS, _BAD_MS, _EXTREME_MS)

_SYMBOL = st.one_of(st.none(), st.sampled_from(["RELIANCE", "NIFTY", "", "  ", "x"]))
_TIMEFRAME = st.one_of(st.none(), st.sampled_from(["1m", "15m", "1d", "weird", ""]))


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Calculator functions are pure (no input mutation, no network)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 2: Calculator functions are pure (no input mutation, no network)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.large_base_example, HealthCheck.too_slow],
)
@given(timestamp_ms=_TIMESTAMP, symbol=_SYMBOL, timeframe=_TIMEFRAME)
def test_property_2_classifier_functions_are_pure(timestamp_ms, symbol, timeframe):
    """Feature: session-expiry-awareness, Property 2: Calculator functions are
    pure (no input mutation, no network).

    For any timestamp / configuration, ``classify_session`` and the pure helpers
    leave the resolved ``SessionConfig`` deep-equal to its pre-call snapshot
    (no input mutation, R1.7), perform no network connection (a socket guard
    raises on any attempt, R1.1/R13.2), and never read the host wall clock (a
    clock guard makes ``datetime.now`` / ``utcnow`` / ``today`` and ``time.time``
    raise while ``fromtimestamp`` still works, R1.1). Because the classifier
    relies solely on the provided timestamp, classification still succeeds under
    the clock guard.

    Validates: Requirements 1.1, 1.7, 13.2
    """
    config = resolve_session_config()
    assert isinstance(config, SessionConfig)
    config_snapshot = copy.deepcopy(config)  # frozen dataclass -> deep-equal compare

    # Install the network + clock guards for the duration of every classifier
    # call. fromtimestamp (epoch -> datetime) is the ONLY clock-adjacent call the
    # classifier is permitted to make, and it is fed exclusively by the provided
    # timestamp, so all calls below must succeed without touching the host clock
    # or the network.
    orig_session_datetime = session.datetime
    orig_socket = socket.socket
    orig_time = _time_mod.time
    session.datetime = _ClockGuard
    socket.socket = _NoNetworkSocket
    _time_mod.time = _raise_clock
    try:
        result = classify_session(
            timestamp_ms, config, symbol=symbol, timeframe=timeframe
        )
        # Idempotent second call (identical args, also under the guards).
        result_again = classify_session(
            timestamp_ms, config, symbol=symbol, timeframe=timeframe
        )

        # Exercise the individual pure helpers too. They only run for a valid
        # timestamp (an invalid one short-circuits to an Unavailable_Marker).
        local_dt = to_local_datetime(timestamp_ms, config)
        if local_dt is not None:
            phase = classify_session_phase(local_dt, config)
            compute_minutes_since_open(local_dt, config)
            compute_minutes_until_close(local_dt, config)
            expiry = compute_expiry_context(local_dt, config)
            derive_time_favorability(phase, expiry, config)
    finally:
        session.datetime = orig_session_datetime
        socket.socket = orig_socket
        _time_mod.time = orig_time

    # ── No input mutation (R1.7) ─────────────────────────────────────────────
    assert config == config_snapshot, "classify_session mutated its config input"

    # ── Output is only a label or a marker; classification worked w/o the clock.
    assert isinstance(result, dict)
    assert result == result_again, "classify_session is not deterministic"

    is_finite_number = (
        isinstance(timestamp_ms, (int, float))
        and not isinstance(timestamp_ms, bool)
        and math.isfinite(timestamp_ms)
    )
    if is_finite_number and local_dt is not None:
        # A representable timestamp must yield a full Session_Label even with the
        # host clock guarded off — proving fromtimestamp is the only time source.
        assert "session_phase" in result, (
            "valid timestamp must classify without reading the host clock"
        )
        assert "unavailable" not in result
    else:
        # Missing / non-finite / out-of-range timestamps degrade honestly.
        assert result.get("unavailable") is True
        assert "session_phase" not in result
        assert "time_favorability" not in result
