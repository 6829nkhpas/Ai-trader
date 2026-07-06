"""Example-based smoke test for the ``get_forecast`` tool against a stubbed Rust
candle endpoint (tools.py, task 16.3).

Feature: volatility-aware-forecaster

This is a DETERMINISTIC, example-based smoke test (NOT a property test) that
exercises the ``get_forecast`` tool end-to-end, fully OFFLINE. The tool's single
I/O call — the Rust ``/tools/get_candles`` POST performed by
``_fetch_candles_for_rs`` via ``tools.httpx.post`` — is stubbed to return a fixed,
hand-pinned list of >= 40 valid OHLCV candles following a clear uptrend with
genuine (positive) variance, so the REAL forecaster produces a usable,
non-marker ``Forecast_Label`` (not an ``Unavailable_Marker``) for a known
symbol/timeframe.

It asserts the documented success contract (R5.5):
  * the tool returns a dict, never an error and never an Unavailable_Marker for
    this sufficient, well-formed candle set;
  * the result is contract-valid — re-running it through
    ``tools.validate_contract("get_forecast", result)`` returns it UNCHANGED
    (no ``contract_violation``);
  * ``projected_direction`` is in {up, down, flat};
  * ``up_probability`` is a finite number in [0.0, 1.0];
  * ``expected_move_atr`` is a finite number or ``null`` (None);
  * ``forecast_confidence`` is a finite number in [0.0, 1.0];
  * ``forecast_alignment`` is in {aligned, misaligned, neutral};
  * ``measures`` carries drift / volatility / standardized_drift / atr.

The sys.path / import pattern, the ``_raw`` @tool-unwrap helper, and the
``_mock_response`` httpx stub mirror the sibling tests
``tests/test_forecast_tool_well_formed_properties.py`` and
``tests/test_rs_compare_smoke.py``.
"""

import json
import math
import os
import sys
from unittest import mock

# Make the service package importable (tools.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    FORECAST_DIRECTIONS,
    get_forecast,
    validate_contract,
)

_SYMBOL = "RELIANCE"
_TIMEFRAME = "15m"

# The default resolved config gates on
# ``required = max(min_candles=30, largest_lookback=max(20,20,14)+1=21) = 30``
# valid candles. 50 candles is comfortably past that (and the task's >= 40 floor).
_CANDLE_COUNT = 50
_BASE_TS = 1_700_000_000_000
_BAR_MS = 15 * 60_000  # 15m bars.

# The named forecast measures the contract requires under ``measures``.
_FORECAST_MEASURE_FIELDS = ("drift", "volatility", "standardized_drift", "atr")


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``.

    ``.json()`` yields the candle list the tool reads; ``.raise_for_status()`` is
    a no-op so the stubbed retrieval looks successful.
    """
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


def _uptrend_candles(n):
    """A fixed list of ``n`` valid OHLCV candle dicts following a clear uptrend.

    A deterministic, positively-drifting path with a small oscillation so the
    window carries genuine (non-zero) variance — keeping the volatility / ATR
    denominators positive and reliably producing a usable, non-marker forecast
    with a meaningful (upward) standardized drift. Shape matches what the Rust
    ``/tools/get_candles`` endpoint returns and what the forecaster reads via
    ``candle.get(...)``.
    """
    candles = []
    price = 100.0
    for i in range(n):
        # Steady positive drift with a deterministic oscillation for variance.
        step = 1.5 + (1.0 if i % 2 == 0 else -0.4)
        new_price = price + step
        open_ = price
        close = new_price
        high = max(open_, close) + 0.75
        low = min(open_, close) - 0.5
        candles.append(
            {
                "timestamp_ms": _BASE_TS + i * _BAR_MS,
                "open": round(open_, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
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


def test_forecast_tool_returns_contract_valid_label_against_stubbed_endpoint():
    """Validates: Requirements 5.5

    Against a stubbed Rust candle endpoint returning a clear-uptrend OHLCV set,
    ``get_forecast`` returns a contract-valid ``Forecast_Label`` for a known
    symbol/timeframe — well-formed direction / probability / expected-move /
    confidence / alignment and the named measures — fully offline.
    """
    candles = _uptrend_candles(_CANDLE_COUNT)
    assert len(candles) >= 40, "fixture must supply >= 40 candles to clear the gate"

    captured = {}

    def _fake_post(url, json=None, timeout=None, **kwargs):
        # The forecaster fetches the symbol candles from the Rust Tool_Server.
        captured["url"] = url
        captured["symbol"] = (json or {}).get("symbol")
        assert "/tools/get_candles" in url, f"unexpected POST url: {url!r}"
        return _mock_response(candles)

    # Stub the candle retrieval so the full tool path runs (arg validation ->
    # config resolution -> candle fetch -> forecast -> contract re-validation)
    # with NO live Rust Tool_Server.
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post):
        result = _raw(get_forecast)(symbol=_SYMBOL, timeframe=_TIMEFRAME)

    # The stubbed endpoint was consulted for the requested symbol.
    assert captured.get("symbol") == _SYMBOL

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"
    # For a valid symbol/timeframe the tool must not surface a structured error.
    assert "error" not in result, f"unexpected error result: {result!r}"
    # The clear-uptrend, sufficiently-long set must yield a usable label, NOT an
    # Unavailable_Marker.
    assert "unavailable" not in result, (
        f"expected a usable Forecast_Label, got an Unavailable_Marker: {result!r}"
    )

    # ── The result must be contract-valid: re-validation returns it UNCHANGED ──
    validated = validate_contract("get_forecast", result)
    assert validated == result, (
        f"validate_contract altered a conforming result: {validated!r} != {result!r}"
    )
    assert "contract_violation" not in validated, (
        f"conforming result flagged as a contract violation: {validated!r}"
    )
    assert "error" not in validated, (
        f"conforming result flagged with an error: {validated!r}"
    )

    # ── The Forecast_Label fields must be well-formed (R5.5) ──────────────────

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

    # measures carries drift / volatility / standardized_drift / atr, each a
    # finite number or null.
    measures = result.get("measures")
    assert isinstance(measures, dict), f"measures is not a dict: {measures!r}"
    for field in _FORECAST_MEASURE_FIELDS:
        assert field in measures, f"measures missing {field!r}: {measures!r}"
        assert _is_finite_or_null(measures[field]), (
            f"measure {field!r} is neither a finite number nor null: "
            f"{measures[field]!r}"
        )
