"""Unit tests for tools._parse_candles_response unavailable-dict unwrapping."""

from tools import _parse_candles_response


def test_unavailable_dict_surfaces_rust_reason():
    payload = {
        "unavailable": True,
        "reason": "Insufficient data for HDFCBANK [10m]: 27 candle(s) available, need 30.",
        "available": 27,
        "needed": 30,
        "symbol": "HDFCBANK",
        "timeframe": "10m",
    }
    candles, reason = _parse_candles_response(payload)
    assert candles is None
    assert reason is not None
    assert "27" in reason
    assert "30" in reason
    assert "no usable data" not in reason


def test_unavailable_dict_appends_counts_when_missing_from_reason():
    payload = {
        "unavailable": True,
        "reason": "shortfall",
        "available": 5,
        "needed": 30,
    }
    _, reason = _parse_candles_response(payload)
    assert "available=5" in reason
    assert "needed=30" in reason


def test_error_list_payload():
    candles, reason = _parse_candles_response([{"error": "boom"}])
    assert candles is None
    assert "boom" in reason


def test_happy_list():
    bars = [{"timestamp_ms": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    candles, reason = _parse_candles_response(bars)
    assert reason is None
    assert candles == bars


def test_empty_list_rejected_by_default():
    candles, reason = _parse_candles_response([])
    assert candles is None
    assert "no usable data" in reason
