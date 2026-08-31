"""Unit test for task 5.4 — the get_relative_strength tool fetches BOTH candle
series (the symbol and its resolved benchmark) from the Rust Tool_Server.

Feature: relative-strength-context

Validates: Requirements 4.4, 13.2

R4.4 — the Relative_Strength_Tool fetches both the symbol candles and the
       Benchmark_Index candles from the Rust_Tool_Server.
R13.2 — the tool derives its result exclusively from OHLCV candle data of the
       symbol and the benchmark; it consumes no options-chain / non-candle data
       source.

The test mocks ``tools.httpx.post`` to return valid candle lists, invokes
``get_relative_strength`` for a symbol whose benchmark resolves to a DIFFERENT
index (HDFCBANK -> BANKNIFTY via the Benchmark_Map), and asserts:
  * ``httpx.post`` was called exactly twice,
  * both calls hit ``/tools/get_candles`` (no other / non-candle endpoint),
  * one call requested the symbol and the other the resolved benchmark.

The underlying function behind the LangChain ``@tool`` object is reached via
``.func`` (the convention used in test_integration_wiring.py).
"""

import json
import os
import sys
from unittest import mock

# Make the service package importable (tools.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
import tools  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


def _valid_candles(n, base):
    """A list of ``n`` well-formed, strictly-timestamped OHLCV candles."""
    candles = []
    for i in range(n):
        price = base + i
        candles.append({
            "timestamp_ms": 1_000 + i * 1_000,
            "open": float(price),
            "high": float(price + 1),
            "low": float(price - 1),
            "close": float(price + 0.5),
            "volume": 10_000.0 + i,
        })
    return candles


def _call_url(call):
    """Extract the URL from an httpx.post call (positional or ``url=`` kwarg)."""
    if call.args:
        return call.args[0]
    return call.kwargs.get("url")


def test_get_relative_strength_fetches_both_candle_series_from_rust():
    """Validates: Requirements 4.4, 13.2

    The tool fetches the symbol candles and the resolved-benchmark candles from
    the Rust Tool_Server's ``/tools/get_candles`` endpoint — exactly two candle
    fetches, and nothing else (no options-chain / non-candle source).
    """
    symbol = "HDFCBANK"
    # The Benchmark_Map resolves this bank symbol to a DIFFERENT index. The tool
    # REPORTS this recognisable identity ("BANKNIFTY")...
    expected_benchmark = rs.resolve_benchmark(symbol)
    assert expected_benchmark == "BANKNIFTY"
    assert expected_benchmark != symbol

    # ...but FETCHES the benchmark's candles under the NSE spot tradingsymbol they
    # are stored under ("NIFTY BANK"). This is the fix's crux: fetching under
    # "BANKNIFTY" returned zero candles (QuestDB stores none under that name) and
    # relative strength was unavailable for every bank stock. Assert the fetch
    # uses the candle name so a regression to "BANKNIFTY" fails here.
    expected_benchmark_fetch = rs.benchmark_candle_name(expected_benchmark)
    assert expected_benchmark_fetch == "NIFTY BANK"
    assert expected_benchmark_fetch != expected_benchmark

    # Return enough valid, time-aligned candles for either series so the call
    # succeeds; the exact classification outcome is irrelevant to this test.
    sym_candles = _valid_candles(80, base=100.0)
    bench_candles = _valid_candles(80, base=200.0)

    def _fake_post(url, json=None, timeout=None, **kwargs):
        requested_symbol = (json or {}).get("symbol")
        if requested_symbol == symbol:
            return _mock_response(sym_candles)
        if requested_symbol == expected_benchmark_fetch:
            return _mock_response(bench_candles)
        raise AssertionError(f"unexpected candle request for {requested_symbol!r}")

    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post) as mock_post:
        result = _raw(tools.get_relative_strength)(
            symbol=symbol, timeframe="15m", proposed_direction="BUY"
        )

    # Exactly two HTTP calls were made (symbol + benchmark), no more.
    assert mock_post.call_count == 2, (
        f"expected exactly 2 candle fetches, got {mock_post.call_count}"
    )

    # BOTH calls hit /tools/get_candles — and no other (non-candle) endpoint.
    urls = [_call_url(c) for c in mock_post.call_args_list]
    for url in urls:
        assert url.endswith("/tools/get_candles"), (
            f"non-candle endpoint consumed: {url}"
        )
    # No options-chain / consensus / news / prediction / SR endpoint was hit.
    forbidden = (
        "get_news_context", "get_prediction", "get_consensus",
        "get_support_resistance", "get_multi_tf_trend", "get_chart_patterns",
        "options", "option_chain",
    )
    for url in urls:
        assert not any(f in url for f in forbidden), (
            f"unexpected non-candle source consumed: {url}"
        )

    # One fetch requested the symbol, the other the benchmark's CANDLE name (the
    # spot tradingsymbol), never the "BANKNIFTY" identity that has no candles.
    requested_symbols = [
        (c.kwargs.get("json") or {}).get("symbol") for c in mock_post.call_args_list
    ]
    assert set(requested_symbols) == {symbol, expected_benchmark_fetch}, (
        f"expected fetches for {symbol!r} and {expected_benchmark_fetch!r}, "
        f"got {requested_symbols!r}"
    )

    # Sanity: the tool produced a usable label (not an unavailable degrade),
    # confirming both fetches were actually consumed by the calculator, and the
    # resolved benchmark is surfaced on the result.
    assert "error" not in result
    assert result.get("benchmark") == expected_benchmark
    assert not result.get("unavailable"), (
        f"tool unexpectedly degraded to unavailable: {result.get('reason')}"
    )
