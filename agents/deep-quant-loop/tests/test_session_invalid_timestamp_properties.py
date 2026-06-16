"""Property-based test for the invalid-timestamp path (session.py, task 2.10).

Feature: session-expiry-awareness

This module implements design **Property 8: An invalid timestamp yields an
Unavailable_Marker, never a fabricated label**:

    For any missing, non-numeric, or non-finite timestamp, ``classify_session``
    returns an Unavailable_Marker whose reason identifies the invalid-timestamp
    condition, omits the ``session_phase`` and ``time_favorability`` keys
    entirely (no fabricated values), leaves its inputs unmodified, and never
    raises.

Validates: Requirements 3.1, 5.2, 13.1.

The strategies below generate invalid timestamps — ``None``, ``NaN``,
``+-inf``, non-numeric strings, ``bool`` (which Python would otherwise treat as
an int), ``list`` / ``dict`` containers, and out-of-range magnitudes such as
``1e30`` / ``-1e30`` that cannot be represented as a datetime — together with
arbitrary, internally consistent ``SessionConfig`` values. For every such input
the result must be an honest Unavailable_Marker that never fabricates a
session label. A handful of *valid* timestamps are mixed in as a control to
confirm they instead yield a full Session_Label (so the property is not
vacuously asserting "everything is unavailable").

The sys.path / import pattern mirrors the existing session/regime test modules:
the service directory (one level up) is prepended to ``sys.path`` so ``session``
is importable when pytest is run from anywhere.
"""

import copy
import math
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

# The five Session_Label fields a successful classification always carries. The
# Unavailable_Marker must omit session_phase and time_favorability (R5.2); we
# additionally confirm none of the label-only fields leak into a marker.
_LABEL_ONLY_KEYS = {
    "session_phase",
    "minutes_since_open",
    "minutes_until_close",
    "expiry_context",
    "time_favorability",
}

# Decision-shaped keys that must NEVER appear in any classifier output (R13.1):
# the classifier is a filter, never a trade generator.
_DECISION_KEYS = {"action", "conviction", "decision", "side", "order", "signal"}


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Invalid timestamps that must drive the Unavailable_Marker path (R3.1):
#   * None                         -> missing
#   * NaN / +inf / -inf            -> non-finite
#   * non-numeric strings          -> non-numeric (a numeric-looking string is
#                                     still a string, not a number)
#   * bool                         -> excluded from "number" by convention
#   * list / dict                  -> non-numeric containers
#   * 1e30 / -1e30                 -> finite but out-of-range for a datetime
_invalid_timestamp = st.sampled_from(
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        "x",
        "",
        "not-a-number",
        "1700000000000",      # numeric-looking, still a string
        True,
        False,
        [],
        [1, 2, 3],
        {},
        {"timestamp_ms": 1700000000000},
        1.0e30,
        -1.0e30,
        2.0e30,
    ]
)

# A few valid timestamps used as a control (these must yield a full label).
_valid_timestamp = st.one_of(
    st.integers(min_value=0, max_value=3_000_000_000_000),
    st.floats(
        min_value=0.0,
        max_value=3.0e12,
        allow_nan=False,
        allow_infinity=False,
    ),
)


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
    return SessionConfig(
        timezone=tz,
        open_time=dtime(open_minutes // 60, open_minutes % 60),
        close_time=dtime(close_minutes // 60, close_minutes % 60),
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


def _assert_no_decision_fields(result):
    """A classifier result must never carry a trade-decision field (R13.1)."""
    assert _DECISION_KEYS.isdisjoint(result.keys()), (
        f"classifier fabricated a decision field: {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 8: An invalid timestamp yields an Unavailable_Marker, never a
# fabricated label
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 8: An invalid timestamp yields an Unavailable_Marker, never a fabricated label
@settings(max_examples=300, deadline=None)
@given(timestamp_ms=_invalid_timestamp, config=_config())
def test_property_8_invalid_timestamp_yields_unavailable_marker(timestamp_ms, config):
    """Validates: Requirements 3.1, 5.2, 13.1

    For any missing / non-numeric / non-finite / out-of-range timestamp,
    ``classify_session``:
      * never raises;
      * returns an Unavailable_Marker (``unavailable`` is ``True``) carrying a
        non-empty ``reason`` string identifying the invalid-timestamp condition;
      * omits ``session_phase`` and ``time_favorability`` entirely (no
        fabricated values, R5.2) — and indeed every label-only field;
      * carries no trade-decision field (R13.1);
      * leaves its inputs unmodified.
    """
    ts_snapshot = copy.deepcopy(timestamp_ms)
    config_snapshot = copy.deepcopy(config)

    # Never raises (R3.1): the call itself must succeed for any invalid input.
    result = classify_session(
        timestamp_ms, config, symbol="RELIANCE", timeframe="15m"
    )

    assert isinstance(result, dict), f"expected a dict result, got {result!r}"

    # It is an honest Unavailable_Marker, not a label.
    assert result.get("unavailable") is True, (
        f"invalid timestamp {timestamp_ms!r} did not yield an Unavailable_Marker: "
        f"{result!r}"
    )

    # The reason identifies the invalid-timestamp condition (R3.1).
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker is missing a reason string: {result!r}"
    )
    assert "timestamp" in reason.lower(), (
        f"reason does not identify the invalid-timestamp condition: {reason!r}"
    )

    # session_phase and time_favorability are OMITTED entirely (R5.2) — no
    # fabricated values. We assert on the full set of label-only fields so a
    # marker can never leak a single fabricated session field.
    leaked = _LABEL_ONLY_KEYS.intersection(result.keys())
    assert not leaked, (
        f"Unavailable_Marker fabricated label field(s) {leaked}: {result!r}"
    )

    # The marker emits no trade decision (R13.1).
    _assert_no_decision_fields(result)

    # Inputs were left unmodified.
    assert (timestamp_ms == ts_snapshot) or (
        isinstance(timestamp_ms, float)
        and isinstance(ts_snapshot, float)
        and math.isnan(timestamp_ms)
        and math.isnan(ts_snapshot)
    ), "classify_session mutated its timestamp input"
    assert config == config_snapshot, "classify_session mutated its config input"

    # The bare (no symbol/timeframe) call shape is also an omitting marker.
    bare = classify_session(timestamp_ms, config)
    assert bare.get("unavailable") is True
    assert not _LABEL_ONLY_KEYS.intersection(bare.keys())


# Feature: session-expiry-awareness, Property 8: An invalid timestamp yields an Unavailable_Marker, never a fabricated label
@settings(max_examples=200, deadline=None)
@given(timestamp_ms=_valid_timestamp, config=_config())
def test_property_8_control_valid_timestamp_yields_full_label(timestamp_ms, config):
    """Validates: Requirements 3.1, 5.2, 13.1 (control)

    Control case: a *valid* timestamp must instead produce a full Session_Label
    carrying both ``session_phase`` and ``time_favorability`` (and the rest of
    the five fields) and must NOT be an Unavailable_Marker. This guards against a
    vacuous Property 8 in which the classifier marked *everything* unavailable.
    """
    result = classify_session(
        timestamp_ms, config, symbol="RELIANCE", timeframe="15m"
    )

    assert isinstance(result, dict)
    # Not an Unavailable_Marker.
    assert "unavailable" not in result, (
        f"valid timestamp {timestamp_ms!r} unexpectedly marked unavailable: "
        f"{result!r}"
    )
    # A full label carries every one of the five session fields, including the
    # two that an Unavailable_Marker omits.
    assert _LABEL_ONLY_KEYS.issubset(result.keys()), (
        f"valid timestamp produced an incomplete label: {result!r}"
    )
    assert result["session_phase"] is not None
    assert result["time_favorability"] is not None
    # Even a favorable label is never a trade decision (R13.1).
    _assert_no_decision_fields(result)
