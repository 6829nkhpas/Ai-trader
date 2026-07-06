"""Unit test for task 6.4 — the get_forecast tool consults the candle source ONLY.

Feature: volatility-aware-forecaster

Validates: Requirements 5.4, 15.2

R5.4  — the Forecast_Tool fetches the symbol candles from the Rust Tool_Server
        (``RUST_SERVER_URL/tools/get_candles``).
R15.2 — the Forecast_Tool derives its result EXCLUSIVELY from OHLCV candle data
        and the configured parameters; it consumes NO options-chain data or any
        other non-candle data source.

The test mocks the single HTTP entry point the tool uses:
  * ``tools.httpx.post`` — the Rust ``/tools/get_candles`` candle fetch
    (``_fetch_candles_for_rs``),
returns a synthetic list of >= 30 valid OHLCV candles, invokes ``get_forecast``,
and asserts:
  * at least one POST was made to a URL ending in ``/tools/get_candles``,
  * EVERY ``httpx.post`` URL is the get_candles endpoint — no options-chain,
    consensus, prediction, or any other data endpoint is consulted,
  * the returned result is a valid Forecast_Label (carries projected_direction,
    up_probability, etc.) derived from the candle data, or an honest marker.

The undecorated function behind the LangChain ``@tool`` object is reached via
``.func`` (the convention used in the sibling order-flow / RS tool tests).
"""

import json
import os
import sys
from unittest import mock

# Make the service package importable (tools.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

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
    """A list of ``n`` well-formed, strictly-timestamped OHLCV candles.

    Prices drift upward with a little oscillation so drift and volatility are
    both non-zero, yielding a usable (non-degenerate) Forecast_Label.
    """
    candles = []
    for i in range(n):
        price = base + i + (1.5 if i % 2 == 0 else -1.0)
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
    """Extract the URL from an httpx call (positional or ``url=`` kwarg)."""
    if call.args:
        return call.args[0]
    return call.kwargs.get("url")


def test_get_forecast_consults_candle_source_only():
    """Validates: Requirements 5.4, 15.2

    The tool fetches the symbol candles from the Rust Tool_Server's
    ``/tools/get_candles`` endpoint and derives its result from candle data
    only — no options-chain or other data source is consulted.
    """
    symbol = "RELIANCE"
    timeframe = "15m"

    # >= 30 valid candles so the forecaster clears the min-candle / largest
    # lookback gate and returns a full Forecast_Label rather than a marker.
    sym_candles = _valid_candles(80, base=100.0)

    # ── Mock the (only) HTTP entry point: the Rust candle fetch (httpx.post) ──
    def _fake_post(url, json=None, timeout=None, **kwargs):
        assert url.endswith("/tools/get_candles"), (
            f"unexpected non-candle POST endpoint: {url}"
        )
        requested_symbol = (json or {}).get("symbol")
        assert requested_symbol == symbol, (
            f"unexpected candle request for {requested_symbol!r}"
        )
        return _mock_response(sym_candles)

    # Patch httpx.get too so any (unexpected) non-candle GET would be captured
    # rather than silently hitting the network.
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post) as mock_post, \
         mock.patch.object(tools.httpx, "get", side_effect=AssertionError(
             "get_forecast must not consult any GET data source")) as mock_get:
        result = _raw(tools.get_forecast)(
            symbol=symbol, timeframe=timeframe, proposed_direction="BUY"
        )

    # ── At least one POST to the candle endpoint was made (R5.4) ─────────────
    assert mock_post.call_count >= 1, "expected at least one candle fetch"

    post_urls = [_call_url(c) for c in mock_post.call_args_list]

    # ── EVERY POST URL is the get_candles endpoint — no other source (R15.2) ──
    for url in post_urls:
        assert url.endswith("/tools/get_candles"), (
            f"non-candle data endpoint consumed: {url}"
        )
        assert url == f"{tools.RUST_SERVER_URL}/tools/get_candles", (
            f"candle fetch did not target the Rust Tool_Server: {url}"
        )

    # The candles were requested for the symbol under analysis.
    post_symbols = [
        (c.kwargs.get("json") or {}).get("symbol") for c in mock_post.call_args_list
    ]
    assert symbol in post_symbols, f"symbol candles were not fetched: {post_symbols!r}"

    # ── No GET-based data source was touched at all (R15.2) ──────────────────
    assert mock_get.call_count == 0, (
        "get_forecast consulted a GET data source; it must derive its result "
        "from candle data only"
    )

    # ── No options-chain / other forbidden data endpoint was consulted ───────
    forbidden = (
        "options", "option_chain", "get_news_context", "get_prediction",
        "get_consensus", "get_support_resistance", "get_multi_tf_trend",
        "get_chart_patterns", "get_relative_strength", "get_order_flow",
    )
    for url in post_urls:
        assert not any(f in url for f in forbidden), (
            f"unexpected non-allowed data source consumed: {url}"
        )

    # ── The result is a valid Forecast_Label derived from the candle data ────
    assert isinstance(result, dict), f"expected a dict result, got {type(result)}"
    assert "error" not in result, f"tool returned an error: {result.get('error')}"

    if result.get("unavailable"):
        # An honest marker is acceptable, but with 80 valid candles we expect a
        # full label — fail loudly so a regression in the forecast path surfaces.
        raise AssertionError(
            f"tool unexpectedly degraded to unavailable: {result.get('reason')}"
        )

    assert result.get("projected_direction") in tools.FORECAST_DIRECTIONS, (
        f"missing/invalid projected_direction: {result.get('projected_direction')!r}"
    )
    up_probability = result.get("up_probability")
    assert isinstance(up_probability, (int, float)) and 0.0 <= up_probability <= 1.0, (
        f"up_probability not a number in [0.0, 1.0]: {up_probability!r}"
    )
    forecast_confidence = result.get("forecast_confidence")
    assert isinstance(forecast_confidence, (int, float)) and 0.0 <= forecast_confidence <= 1.0, (
        f"forecast_confidence not a number in [0.0, 1.0]: {forecast_confidence!r}"
    )
    assert result.get("forecast_alignment") in tools.ALIGNMENT_VALUES, (
        f"missing/invalid forecast_alignment: {result.get('forecast_alignment')!r}"
    )
    # The named measures are present (the result is derived from candle data).
    measures = result.get("measures")
    assert isinstance(measures, dict), f"missing 'measures' object: {measures!r}"
    for field in tools._FORECAST_MEASURE_FIELDS:
        assert field in measures, f"forecast measures missing '{field}'"
