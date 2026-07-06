# Feature: session-expiry-awareness, Property 11: A successful tool result is well-formed
"""Property-based test for a well-formed successful tool result (tools.py, task 4.4).

Feature: session-expiry-awareness

This Hypothesis property exercises the ``get_session_context`` tool in
``tools.py`` with the candle retrieval MOCKED. It covers design Property 11: for
any candle data sufficient to classify (with candle retrieval mocked), a
successful (non-unavailable, non-error) ``get_session_context`` result is a
well-formed Session_Label — ``session_phase`` is drawn from the seven-value
``SESSION_PHASES`` enum, ``minutes_since_open`` and ``minutes_until_close`` are
each a finite number or ``null``, ``expiry_context`` is an object carrying a
boolean ``is_expiry_day`` and an integer ``days_until_expiry``, and
``time_favorability`` is drawn from the three-value ``TIME_FAVORABILITY`` enum.

The tool fetches the most recent candle via
``httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)`` and reads it with
``response.json()`` (a list of OHLCV candle dicts). Here ``tools.httpx.post`` is
patched to return a generated single-candle list carrying a valid
``timestamp_ms`` and OHLCV, so the test exercises the full tool path (arg
validation -> config resolution -> classify -> contract re-validation) with NO
live Rust Tool_Server.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_rs_tool_wellformed_properties.py``.

Validates: Requirements 4.5
"""

import json
import math
import os
import sys
from unittest import mock

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / session.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    SESSION_PHASES,
    TIME_FAVORABILITY,
    get_session_context,
)

# A non-empty symbol and a SUPPORTED timeframe so argument validation passes.
_SYMBOL = "RELIANCE"
_TIMEFRAME = "15m"

# Epoch-millisecond bounds spanning many years / weekdays / times-of-day so the
# generated reference timestamp lands in every Session_Phase and on / off the
# expiry weekday. 2015-01-01 .. 2035-01-01 (UTC), in milliseconds.
_TS_MIN_MS = 1_420_070_400_000
_TS_MAX_MS = 2_051_222_400_000


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``.

    ``.json()`` yields the candle list the tool reads; ``.raise_for_status()`` is
    a no-op so the mocked retrieval looks successful.
    """
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


@st.composite
def _single_candle_list(draw):
    """A one-element list holding a valid OHLCV candle with a generated timestamp.

    The tool reads the LAST element's ``timestamp_ms``; a single candle keeps the
    "most recent candle" unambiguous. The timestamp spans many days / weekdays /
    times-of-day so the classifier reaches every phase and the expiry override.
    """
    timestamp_ms = draw(st.integers(min_value=_TS_MIN_MS, max_value=_TS_MAX_MS))
    open_ = draw(
        st.floats(min_value=10.0, max_value=10_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    close = draw(
        st.floats(min_value=10.0, max_value=10_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    high = max(open_, close) + draw(
        st.floats(min_value=0.0, max_value=10.0,
                  allow_nan=False, allow_infinity=False)
    )
    low = max(
        min(open_, close)
        - draw(
            st.floats(min_value=0.0, max_value=10.0,
                      allow_nan=False, allow_infinity=False)
        ),
        0.5,
    )
    volume = draw(
        st.floats(min_value=0.0, max_value=1_000_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    return [
        {
            "timestamp_ms": timestamp_ms,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    ]


def _is_finite_or_null(value) -> bool:
    """True when ``value`` is None or a finite real number (bool excluded)."""
    if value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 11: A successful tool result is well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 11: A successful tool result is well-formed
@settings(max_examples=100, deadline=None)
@given(candles=_single_candle_list())
def test_property_11_successful_tool_result_is_well_formed(candles):
    """Feature: session-expiry-awareness, Property 11: A successful tool result
    is well-formed — for any candle data sufficient to classify (with retrieval
    MOCKED), a non-unavailable, non-error ``get_session_context`` result carries
    ``session_phase`` in SESSION_PHASES, finite-or-null ``minutes_since_open`` /
    ``minutes_until_close``, an ``expiry_context`` object with a boolean
    ``is_expiry_day`` and an integer ``days_until_expiry``, and
    ``time_favorability`` in TIME_FAVORABILITY.

    Validates: Requirements 4.5
    """
    def _fake_post(url, json=None, timeout=None, **kwargs):
        # Every fetch returns the generated single-candle list; the tool reads
        # the most recent (last) candle's timestamp from it.
        return _mock_response(candles)

    # Mock the candle retrieval so the tool runs against generated data with no
    # live Rust Tool_Server.
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post):
        result = _raw(get_session_context)(symbol=_SYMBOL, timeframe=_TIMEFRAME)

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # Any valid timestamp yields a full Session_Label; this property asserts only
    # over produced labels (an Unavailable_Marker / error is not the subject here).
    assume("unavailable" not in result)
    assume("error" not in result)

    # session_phase ∈ SESSION_PHASES (the seven-value enum).
    assert result.get("session_phase") in SESSION_PHASES, (
        f"session_phase {result.get('session_phase')!r} not in {SESSION_PHASES}"
    )

    # minutes_since_open / minutes_until_close are each a finite number or null.
    assert "minutes_since_open" in result, "minutes_since_open missing"
    assert _is_finite_or_null(result["minutes_since_open"]), (
        f"minutes_since_open is neither a finite number nor null: "
        f"{result['minutes_since_open']!r}"
    )
    assert "minutes_until_close" in result, "minutes_until_close missing"
    assert _is_finite_or_null(result["minutes_until_close"]), (
        f"minutes_until_close is neither a finite number nor null: "
        f"{result['minutes_until_close']!r}"
    )

    # expiry_context is an object carrying a boolean is_expiry_day and an integer
    # days_until_expiry.
    expiry_context = result.get("expiry_context")
    assert isinstance(expiry_context, dict), (
        f"'expiry_context' is not an object: {expiry_context!r}"
    )
    is_expiry_day = expiry_context.get("is_expiry_day")
    assert isinstance(is_expiry_day, bool), (
        f"'is_expiry_day' is not a boolean: {is_expiry_day!r}"
    )
    days_until_expiry = expiry_context.get("days_until_expiry")
    assert isinstance(days_until_expiry, int) and not isinstance(
        days_until_expiry, bool
    ), (
        f"'days_until_expiry' is not an integer: {days_until_expiry!r}"
    )

    # time_favorability ∈ TIME_FAVORABILITY (the three-value enum).
    assert result.get("time_favorability") in TIME_FAVORABILITY, (
        f"time_favorability {result.get('time_favorability')!r} "
        f"not in {TIME_FAVORABILITY}"
    )
