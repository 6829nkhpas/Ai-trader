"""Unit test for task 6.4 — the get_order_flow tool consults the symbol candles
and the live-ticks source ONLY.

Feature: order-flow-context

Validates: Requirements 5.4, 14.2

R5.4  — the Order_Flow_Tool fetches the symbol candles from the Rust Tool_Server
        for the proxy layer AND attempts to read recent ticks for the symbol
        from the Live_Ticks_Source (the ``live_ticks`` table via the QuestDB
        HTTP ``/exec`` API) for the Tick_OFI layer.
R14.2 — the tool derives its result EXCLUSIVELY from OHLCV candle data and
        ``live_ticks`` tick data and the configured parameters; it consumes no
        options-chain data or any other data source.

The test mocks both HTTP entry points the tool uses:
  * ``tools.httpx.post`` — the Rust ``/tools/get_candles`` candle fetch
    (``_fetch_candles_for_rs``), and
  * ``tools.httpx.get``  — the QuestDB ``/exec`` ``live_ticks`` read
    (``_read_live_ticks``),
invokes ``get_order_flow`` and asserts:
  * the candle endpoint ``RUST_SERVER_URL/tools/get_candles`` was called for the
    symbol,
  * the QuestDB ``/exec`` ``live_ticks`` query was attempted against
    ``QUESTDB_HTTP_URL``,
  * NO other data source (options-chain / consensus / news / prediction / etc.)
    was touched.

The undecorated function behind the LangChain ``@tool`` object is reached via
``.func`` (the convention used in test_rs_tool_fetches_both.py).
"""

import json
import os
import sys
from unittest import mock

# Make the service package importable (tools.py / order_flow.py live one level up).
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


def _tick_dataset(n):
    """A QuestDB ``/exec`` dataset of ``n`` rows: [last_price, cum_volume, bid, ask].

    Cumulative volume increases each tick (positive traded deltas) and price
    drifts up so the tick-rule produces a usable, signed Tick_OFI. The QuestDB
    ``/exec`` query orders ``timestamp DESC`` (most-recent first) and
    ``_read_live_ticks`` reverses to oldest-first, so the dataset is emitted
    newest-first (highest cumulative volume first) here.
    """
    rows = []
    for i in range(n):
        price = 100.0 + i * 0.1
        rows.append([price, 1_000.0 + i * 50.0, price - 0.05, price + 0.05])
    rows.reverse()  # emit newest-first (DESC), as the QuestDB query returns
    return {
        "dataset": rows,
        "columns": [
            {"name": "last_traded_price"}, {"name": "volume"},
            {"name": "best_bid"}, {"name": "best_ask"},
        ],
    }


def _call_url(call):
    """Extract the URL from an httpx call (positional or ``url=`` kwarg)."""
    if call.args:
        return call.args[0]
    return call.kwargs.get("url")


def test_get_order_flow_consults_candles_and_live_ticks_only():
    """Validates: Requirements 5.4, 14.2

    The tool fetches the symbol candles from the Rust Tool_Server's
    ``/tools/get_candles`` endpoint AND attempts a ``live_ticks`` read against the
    QuestDB ``/exec`` API — and touches no other data source.
    """
    symbol = "RELIANCE"
    timeframe = "15m"

    sym_candles = _valid_candles(80, base=100.0)
    ticks_body = _tick_dataset(20)

    # ── Mock the Rust candle fetch (httpx.post) ──────────────────────────────
    def _fake_post(url, json=None, timeout=None, **kwargs):
        requested_symbol = (json or {}).get("symbol")
        assert url.endswith("/tools/get_candles"), (
            f"unexpected non-candle POST endpoint: {url}"
        )
        assert requested_symbol == symbol, (
            f"unexpected candle request for {requested_symbol!r}"
        )
        return _mock_response(sym_candles)

    # ── Mock the QuestDB live_ticks read (httpx.get) ─────────────────────────
    def _fake_get(url, params=None, timeout=None, **kwargs):
        assert url.endswith("/exec"), f"unexpected non-/exec GET endpoint: {url}"
        query = (params or {}).get("query", "")
        assert "live_ticks" in query, (
            f"live_ticks query not attempted; got: {query!r}"
        )
        return _mock_response(ticks_body)

    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post) as mock_post, \
         mock.patch.object(tools.httpx, "get", side_effect=_fake_get) as mock_get:
        result = _raw(tools.get_order_flow)(
            symbol=symbol, timeframe=timeframe, proposed_direction="BUY"
        )

    # ── The candle (proxy) layer was consulted via the Rust Tool_Server ──────
    assert mock_post.call_count >= 1, "expected at least one candle fetch"
    post_urls = [_call_url(c) for c in mock_post.call_args_list]
    for url in post_urls:
        assert url == f"{tools.RUST_SERVER_URL}/tools/get_candles", (
            f"non-candle endpoint consumed: {url}"
        )
    post_symbols = [
        (c.kwargs.get("json") or {}).get("symbol") for c in mock_post.call_args_list
    ]
    assert symbol in post_symbols, f"symbol candles were not fetched: {post_symbols!r}"

    # ── The live-ticks (Tick_OFI) layer read was attempted via QuestDB /exec ──
    assert mock_get.call_count >= 1, "expected at least one live_ticks read attempt"
    get_urls = [_call_url(c) for c in mock_get.call_args_list]
    for url in get_urls:
        assert url == f"{tools.QUESTDB_HTTP_URL}/exec", (
            f"unexpected non-/exec endpoint consumed: {url}"
        )
    queries = [(c.kwargs.get("params") or {}).get("query", "") for c in mock_get.call_args_list]
    assert any("live_ticks" in q for q in queries), (
        f"no live_ticks query was attempted; queries: {queries!r}"
    )
    # The query reads exactly the live-ticks fields and filters by the symbol.
    of_query = next(q for q in queries if "live_ticks" in q)
    assert symbol in of_query, f"live_ticks query did not filter by symbol: {of_query!r}"

    # ── NO other / non-allowed data source was touched (R14.2) ───────────────
    forbidden = (
        "get_news_context", "get_prediction", "get_consensus",
        "get_support_resistance", "get_multi_tf_trend", "get_chart_patterns",
        "options", "option_chain",
    )
    for url in post_urls + get_urls:
        assert not any(f in url for f in forbidden), (
            f"unexpected non-allowed data source consumed: {url}"
        )

    # ── Sanity: both layers were consumed -> a usable label (not an error) ───
    assert "error" not in result, f"tool returned an error: {result.get('error')}"
    assert not result.get("unavailable"), (
        f"tool unexpectedly degraded to unavailable: {result.get('reason')}"
    )
    # The live tick stream was present, so the Tick_OFI layer contributed.
    assert result.get("live_tick_contributed") is True, (
        f"expected live ticks to contribute; result: {result!r}"
    )
