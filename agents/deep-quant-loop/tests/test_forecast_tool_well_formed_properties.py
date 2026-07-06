"""Property-based test for a well-formed successful forecast tool result (tools.py, task 6.6).

Feature: volatility-aware-forecaster

This Hypothesis property exercises the ``get_forecast`` tool in ``tools.py`` with
its candle retrieval MOCKED. It covers design **Property 16: A successful tool
result is well-formed**: for any symbol candle data sufficient to forecast (the
Rust candle retrieval mocked) and any proposed trade direction, a successful
(non-unavailable, non-error) ``get_forecast`` result is well-formed — it carries
``projected_direction`` in {up, down, flat}, an ``up_probability`` that is a
finite number in [0.0, 1.0], an ``expected_move_atr`` that is a finite number or
``null`` (None), a ``forecast_confidence`` that is a finite number in [0.0, 1.0],
and a ``forecast_alignment`` in {aligned, misaligned, neutral} (Requirement 5.5).

The tool's single I/O call is mocked at the ``httpx`` level, exactly where
``_fetch_candles_for_rs`` reaches the network:

  * ``tools.httpx.post`` -> the Rust ``/tools/get_candles`` POST. Patched to
    return a generated valid OHLCV candle list (>= 35 candles, comfortably past
    the forecaster's largest-lookback / min-candle gate), read by the tool via
    ``response.json()``.

The full tool path runs (arg validation -> config resolution -> candle fetch ->
forecast -> contract re-validation) with NO live Rust Tool_Server. Candle sets
are generated as random walks with clear positive variance so the forecaster
reliably produces a Forecast_Label; should a generated set still degenerate to
an Unavailable_Marker, the marker shape is asserted instead and the label
assertions are skipped.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_of_tool_wellformed_properties.py`` and
``tests/test_rs_tool_wellformed_properties.py``.
"""

import json
import math
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    FORECAST_DIRECTIONS,
    get_forecast,
)

# The default resolved config gates on
# ``required = max(min_candles=30, largest_lookback=max(20,20,14)+1=21) = 30``
# valid candles. Generate comfortably more than that (and always >= 35) so the
# forecaster reliably produces a Forecast_Label (not an Unavailable_Marker).
_MIN_CANDLES = 45
_MAX_CANDLES = 80

_SYMBOL = "RELIANCE"
_TIMEFRAME = "15m"


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
def _candle_walk(draw, n):
    """A sequence of ``n`` valid OHLCV candle dicts following a random price walk.

    Every candle's OHLCV fields are finite numbers, consecutive closes move with
    a clear (positively-biased) variance, and the high-low range is strictly
    positive, so the path carries real movement (keeping the volatility / ATR
    denominators non-zero and producing a successful, non-marker forecast). Shape
    matches what the Rust ``/tools/get_candles`` endpoint returns and what the
    forecaster reads via ``candle.get(...)``.
    """
    price = draw(
        st.floats(min_value=50.0, max_value=5_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    candles = []
    for i in range(n):
        # A varied random walk with a small positive drift bias and a genuine
        # spread of steps so the window has clear (non-zero) variance.
        step = draw(
            st.floats(min_value=-25.0, max_value=30.0,
                      allow_nan=False, allow_infinity=False)
        )
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.5, max_value=10.0,
                      allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(
                st.floats(min_value=0.5, max_value=10.0,
                          allow_nan=False, allow_infinity=False)
            ),
            0.25,
        )
        candles.append(
            {
                "timestamp_ms": i * 1000,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0 + i,
            }
        )
        price = new_price
    return candles


def _is_finite_or_null(value) -> bool:
    """True when ``value`` is None or a finite real number (bool excluded)."""
    if value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_finite_number_in_unit_interval(value) -> bool:
    """True when ``value`` is a finite real number (bool excluded) within [0, 1]."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: A successful tool result is well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 16: A successful tool result is well-formed
@settings(max_examples=150, deadline=None)
@given(
    candle_count=st.integers(min_value=_MIN_CANDLES, max_value=_MAX_CANDLES),
    proposed_direction=st.sampled_from(["", "BUY", "SELL"]),
    data=st.data(),
)
def test_property_16_successful_tool_result_is_well_formed(
    candle_count, proposed_direction, data
):
    """Feature: volatility-aware-forecaster, Property 16: A successful tool result
    is well-formed — for any symbol candle data sufficient to forecast (the Rust
    candle retrieval MOCKED) and any proposed trade direction, a non-unavailable,
    non-error ``get_forecast`` result carries ``projected_direction`` in
    {up, down, flat}, an ``up_probability`` finite number in [0.0, 1.0], an
    ``expected_move_atr`` finite-number-or-null, a ``forecast_confidence`` finite
    number in [0.0, 1.0], and a ``forecast_alignment`` in {aligned, misaligned,
    neutral}. A degenerate set that yields an Unavailable_Marker is acceptable —
    the marker shape is asserted instead.

    Validates: Requirements 5.5
    """
    candles = data.draw(_candle_walk(candle_count))

    def _fake_post(url, json=None, timeout=None, **kwargs):
        # The forecaster fetches the symbol candles from the Rust Tool_Server.
        assert "/tools/get_candles" in url, f"unexpected POST url: {url!r}"
        assert (json or {}).get("symbol") == _SYMBOL
        return _mock_response(candles)

    # Mock the candle retrieval so the tool runs against generated data with no
    # live Rust Tool_Server.
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post):
        result = _raw(get_forecast)(
            symbol=_SYMBOL,
            timeframe=_TIMEFRAME,
            proposed_direction=proposed_direction,
        )

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"
    # The tool must never surface a structured error for a valid symbol/timeframe.
    assert "error" not in result, f"unexpected error result: {result!r}"

    # An Unavailable_Marker is an acceptable honest outcome — assert its shape and
    # that it carries NONE of the fabricated forecast fields, then stop.
    if "unavailable" in result:
        assert result.get("unavailable") is True
        assert isinstance(result.get("reason"), str) and result["reason"].strip(), (
            f"unavailable marker missing a reason string: {result!r}"
        )
        for forbidden in (
            "projected_direction",
            "up_probability",
            "expected_move_atr",
            "forecast_confidence",
            "forecast_alignment",
        ):
            assert forbidden not in result, (
                f"unavailable marker must not fabricate '{forbidden}': {result!r}"
            )
        return

    # ── A successful Forecast_Label must be well-formed (R5.5) ────────────────

    # projected_direction in {up, down, flat}
    assert result.get("projected_direction") in FORECAST_DIRECTIONS, (
        f"projected_direction {result.get('projected_direction')!r} "
        f"not in {FORECAST_DIRECTIONS}"
    )

    # up_probability a finite number in [0.0, 1.0]
    assert _is_finite_number_in_unit_interval(result.get("up_probability")), (
        f"up_probability is not a finite number in [0, 1]: "
        f"{result.get('up_probability')!r}"
    )

    # expected_move_atr a finite number or null
    assert "expected_move_atr" in result, "result missing 'expected_move_atr'"
    assert _is_finite_or_null(result["expected_move_atr"]), (
        f"expected_move_atr is neither a finite number nor null: "
        f"{result['expected_move_atr']!r}"
    )

    # forecast_confidence a finite number in [0.0, 1.0]
    assert _is_finite_number_in_unit_interval(result.get("forecast_confidence")), (
        f"forecast_confidence is not a finite number in [0, 1]: "
        f"{result.get('forecast_confidence')!r}"
    )

    # forecast_alignment in {aligned, misaligned, neutral}
    assert result.get("forecast_alignment") in ALIGNMENT_VALUES, (
        f"forecast_alignment {result.get('forecast_alignment')!r} "
        f"not in {ALIGNMENT_VALUES}"
    )
