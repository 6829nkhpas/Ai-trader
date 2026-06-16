"""Property-based test that the classifier emits no trade decision (session.py, task 2.11).

Feature: session-expiry-awareness

This module implements design **Property 9: The classifier emits only a label or
a marker — never a trade decision**:

    For any timestamp and configuration, the ``classify_session`` result is a
    Session_Label or an Unavailable_Marker and contains no BUY, SELL, or HOLD
    action, no conviction score, and no decision field — so classification alone,
    even when the Time_Favorability is ``favorable``, never commits, generates, or
    triggers a trade.

Validates: Requirements 13.1, 13.3.

The strategies below generate arbitrary epoch-millisecond timestamps — finite
values spanning many years (which reach the Session_Label path across every
phase / expiry / favorability combination, including ``favorable``) mixed with
invalid timestamps (``None`` / ``NaN`` / ``+-inf`` / non-numeric / out-of-range)
that drive the Unavailable_Marker path — together with arbitrary ``SessionConfig``
values (and optional symbol/timeframe). For every result the test asserts:

  * the result is a dict that is exactly one of the two allowed shapes (a clean
    Session_Label or a clean Unavailable_Marker);
  * every top-level key is confined to the allowed Session_Label /
    Unavailable_Marker key set (no extra/decision keys);
  * no trade-decision field name (``action`` / ``conviction`` / ``entry`` /
    ``stop_loss`` / ``take_profit`` / ``order`` / ``signal`` / ``BUY`` / ``SELL``
    / ``HOLD`` / ...) appears as a key anywhere within the result (any nesting);
  * no string value anywhere within the result equals a BUY / SELL / HOLD action.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules:
the service directory (one level up) is prepended to ``sys.path`` so ``session``
is importable when pytest is run from anywhere.
"""

import os
import sys
from datetime import time as dtime

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from session import SessionConfig, classify_session  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Allowed key sets — the ONLY keys a Session_Label or Unavailable_Marker may carry
# ─────────────────────────────────────────────────────────────────────────────

# Top-level keys of a Session_Label (symbol/timeframe are optional, present only
# when the caller supplies them).
_LABEL_KEYS = frozenset(
    {
        "session_phase",
        "minutes_since_open",
        "minutes_until_close",
        "expiry_context",
        "time_favorability",
        "symbol",
        "timeframe",
    }
)

# Top-level keys of an Unavailable_Marker.
_MARKER_KEYS = frozenset({"symbol", "timeframe", "unavailable", "reason"})

# The union: any top-level key the classifier may ever emit.
_ALLOWED_TOP_LEVEL_KEYS = _LABEL_KEYS | _MARKER_KEYS

# Nested keys permitted inside ``expiry_context``.
_EXPIRY_CONTEXT_KEYS = frozenset({"is_expiry_day", "days_until_expiry"})

# Trade-decision field names that must NEVER appear as a key anywhere in the
# result (Requirements 13.1, 13.3). Compared case-insensitively.
_FORBIDDEN_KEYS = frozenset(
    {
        "action",
        "buy",
        "sell",
        "hold",
        "conviction",
        "conviction_score",
        "entry",
        "stop_loss",
        "take_profit",
        "order",
        "signal",
        "decision",
        "trade",
    }
)

# BUY / SELL / HOLD action words that must not appear as a value anywhere in the
# result (compared case-insensitively after stripping).
_ACTION_WORDS = frozenset({"BUY", "SELL", "HOLD"})


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite epoch-millisecond timestamps spanning roughly 1970-01-01 .. ~2065 so
# generated values land in every Session_Phase / expiry / favorability
# combination (including ``favorable``), reaching the full Session_Label path.
_valid_ts_ms = st.floats(
    min_value=0.0,
    max_value=3.0e12,
    allow_nan=False,
    allow_infinity=False,
)

# Integer epoch-ms timestamps (the natural candle representation) over the same
# range, mixed in so the property covers both int and float inputs.
_valid_ts_ms_int = st.integers(min_value=0, max_value=3_000_000_000_000)

# Invalid timestamps that drive the Unavailable_Marker path (Requirement 3.1):
# missing, non-finite, non-numeric, or out-of-range epoch values.
_invalid_ts = st.sampled_from(
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        "x",
        "1700000000000",
        True,
        [],
        {},
        1.0e30,   # out-of-range -> not representable as a datetime
        -1.0e30,
    ]
)

# Arbitrary timestamps: mostly valid (label path), occasionally invalid (marker
# path), so the property covers both result families.
_timestamp = st.one_of(_valid_ts_ms, _valid_ts_ms_int, _invalid_ts)


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``SessionConfig``.

    Mirrors what ``resolve_session_config`` would produce: a loadable timezone,
    ``open_time < close_time``, non-negative window lengths, and an expiry
    weekday in ``[0, 6]``.
    """
    tz = draw(
        st.sampled_from(
            [
                "Asia/Kolkata",
                "UTC",
                "America/New_York",
                "Europe/London",
                "Asia/Tokyo",
                "Australia/Sydney",
            ]
        )
    )
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    open_time = dtime(open_minutes // 60, open_minutes % 60)
    close_time = dtime(close_minutes // 60, close_minutes % 60)
    return SessionConfig(
        timezone=tz,
        open_time=open_time,
        close_time=close_time,
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


# Symbol / timeframe context the caller may attach. Kept to non-action-word
# values (the property concerns values the classifier *produces*, not arbitrary
# caller-echoed action strings), plus ``None`` for the bare call shape.
_SYMBOL = st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS", "INFY", "NIFTY"]))
_TIMEFRAME = st.one_of(st.none(), st.sampled_from(["1m", "5m", "15m", "1h", "1d"]))


def _walk_keys_and_values(obj):
    """Yield ``("key", k)`` for every mapping key and ``("value", v)`` for every
    leaf value reached by recursively walking dicts / lists / tuples in ``obj``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("key", k)
            yield from _walk_keys_and_values(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys_and_values(item)
    else:
        yield ("value", obj)


# ─────────────────────────────────────────────────────────────────────────────
# Property 9: The classifier emits only a label or a marker — never a trade decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 9: The classifier emits only a label or a marker — never a trade decision
@settings(max_examples=200, deadline=None)
@given(timestamp_ms=_timestamp, config=_config(), symbol=_SYMBOL, timeframe=_TIMEFRAME)
def test_property_9_classifier_emits_only_label_or_marker(
    timestamp_ms, config, symbol, timeframe
):
    """Validates: Requirements 13.1, 13.3

    For any timestamp / configuration (driving both the Session_Label and the
    Unavailable_Marker paths, including the ``favorable`` favorability), the
    ``classify_session`` result is a dict that is exactly one of the two allowed
    shapes, whose top-level keys are confined to the allowed key set, and that
    carries no trade-decision key and no BUY/SELL/HOLD action value anywhere.
    """
    result = classify_session(
        timestamp_ms, config, symbol=symbol, timeframe=timeframe
    )

    # The classifier only ever emits a dict (a label or an Unavailable_Marker).
    assert isinstance(result, dict), f"result is not a dict: {result!r}"

    # It is exactly one of the two allowed shapes: an Unavailable_Marker (carries
    # ``unavailable``) XOR a Session_Label (carries ``session_phase``).
    is_marker = result.get("unavailable") is True
    is_label = "session_phase" in result
    assert is_marker ^ is_label, (
        f"result is neither a clean marker nor a clean label: {result!r}"
    )

    # Every top-level key is confined to the allowed Session_Label /
    # Unavailable_Marker key set — no decision or other extraneous key.
    extra_keys = set(result.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    assert not extra_keys, (
        f"result carries keys outside the allowed set: {extra_keys} in {result!r}"
    )

    # No trade-decision field name appears as a key at any nesting level.
    for kind, item in _walk_keys_and_values(result):
        if kind == "key" and isinstance(item, str):
            assert item.lower() not in _FORBIDDEN_KEYS, (
                f"forbidden trade-decision key {item!r} present in result: {result!r}"
            )

    # No string value anywhere within the result equals a BUY/SELL/HOLD action,
    # so a ``favorable`` classification never doubles as a trade trigger.
    for kind, item in _walk_keys_and_values(result):
        if kind == "value" and isinstance(item, str):
            assert item.strip().upper() not in _ACTION_WORDS, (
                f"BUY/SELL/HOLD action value {item!r} present in result: {result!r}"
            )

    # Shape-specific structural checks: a marker omits the decision-adjacent
    # label fields; a label carries the categorical context fields only and a
    # well-formed expiry_context with no extraneous nested keys.
    if is_marker:
        assert "session_phase" not in result
        assert "time_favorability" not in result
        assert set(result.keys()) <= _MARKER_KEYS
    else:
        assert result["session_phase"] in {
            "pre_open",
            "opening",
            "morning",
            "midday",
            "afternoon",
            "closing",
            "post_close",
        }
        assert result["time_favorability"] in {"favorable", "unfavorable", "neutral"}
        expiry_context = result["expiry_context"]
        assert isinstance(expiry_context, dict)
        assert set(expiry_context.keys()) == _EXPIRY_CONTEXT_KEYS
