"""Property-based test for a well-formed successful tool result (tools.py, task 5.5).

Feature: regime-detection-gate

This Hypothesis property exercises the ``get_market_regime`` tool in ``tools.py``
with the network call MOCKED. It covers design Property 11: for any candle data
sufficient to classify (retrieval mocked), the ``get_market_regime`` result
contains ``trend_state`` in its enum, ``volatility_state`` in its enum,
``favorability`` in its enum, and each named Regime_Measure present as a finite
number or ``null`` (None).

The tool fetches candles via ``httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)``
and reads them with ``response.json()`` (a list of OHLCV candle dicts with keys
``open`` / ``high`` / ``low`` / ``close`` / ``volume``). Here ``tools.httpx.post``
is patched to return a stand-in response whose ``.json()`` yields a generated
price walk and whose ``.raise_for_status()`` is a no-op, so the test exercises
the full tool path (arg validation → config resolution → classify → contract
re-validation) with NO live Rust Tool_Server.

Some generated walks may degenerate (e.g. an all-null measure window) and yield
an Unavailable_Marker; those are skipped via ``assume`` because this property
asserts only over produced Regime_Labels.
"""

import json
import math
import os
import sys
from unittest import mock

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    REGIME_FAVORABILITY,
    REGIME_TREND_STATES,
    REGIME_VOLATILITY_STATES,
    _REGIME_MEASURE_FIELDS,
    get_market_regime,
)

# The default resolved config gates on ``max(min_candles=50, largest_lookback)``
# where ``largest_lookback = vol_period + vol_pctl_window = 14 + 100 = 114``.
# Generate comfortably more than that many valid candles so the classifier
# reliably produces a Regime_Label (not an Unavailable_Marker).
_MIN_CANDLES = 120
_MAX_CANDLES = 160


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
def _ohlcv_candles(draw):
    """A sequence of valid OHLCV candle dicts following a random price walk.

    Every candle's OHLCV fields are finite numbers and consecutive closes move,
    so the price path has real movement and a real high-low range. This keeps
    the measure denominators generally non-zero so the tool clears the
    classifier gate and returns a Regime_Label for >= the largest configured
    lookback of valid candles.
    """
    n = draw(st.integers(min_value=_MIN_CANDLES, max_value=_MAX_CANDLES))
    price = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    candles = []
    for _ in range(n):
        step = draw(
            st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False)
        )
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(
                st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
            ),
            0.5,
        )
        candles.append(
            {
                "timestamp_ms": 0,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0,
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


# ─────────────────────────────────────────────────────────────────────────────
# Property 11: A successful tool result is well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 11
@settings(max_examples=150, deadline=None)
@given(candles=_ohlcv_candles())
def test_property_11_successful_tool_result_is_well_formed(candles):
    """Feature: regime-detection-gate, Property 11: A successful tool result is
    well-formed — for any candle data sufficient to classify (with retrieval
    MOCKED), the ``get_market_regime`` result contains ``trend_state`` in its
    enum, ``volatility_state`` in its enum, ``favorability`` in its enum, and
    each named Regime_Measure present as a finite number or null.

    Validates: Requirements 3.4
    """
    # Mock the candle retrieval so the tool runs against generated data with no
    # live Rust Tool_Server. ``.json()`` yields the candle list; the tool reads
    # it exactly as it reads the real Rust response.
    with mock.patch.object(
        tools.httpx, "post", return_value=_mock_response(candles)
    ):
        result = _raw(get_market_regime)(symbol="RELIANCE", timeframe="15m")

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # A degenerate walk can still yield an Unavailable_Marker (e.g. an all-null
    # measure window) — this property asserts only over produced Regime_Labels.
    assume("unavailable" not in result)
    assume("error" not in result)

    # The three categorical states must each be drawn from their fixed enums.
    assert result.get("trend_state") in REGIME_TREND_STATES, (
        f"trend_state {result.get('trend_state')!r} not in {REGIME_TREND_STATES}"
    )
    assert result.get("volatility_state") in REGIME_VOLATILITY_STATES, (
        f"volatility_state {result.get('volatility_state')!r} not in {REGIME_VOLATILITY_STATES}"
    )
    assert result.get("favorability") in REGIME_FAVORABILITY, (
        f"favorability {result.get('favorability')!r} not in {REGIME_FAVORABILITY}"
    )

    # Each named Regime_Measure must be present as a finite number or null.
    measures = result.get("measures")
    assert isinstance(measures, dict), f"'measures' is not a dict: {measures!r}"
    for field in _REGIME_MEASURE_FIELDS:
        assert field in measures, f"measure '{field}' missing from {measures!r}"
        value = measures[field]
        assert _is_finite_or_null(value), (
            f"measure '{field}' is neither a finite number nor null: {value!r}"
        )
