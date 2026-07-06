"""Unit tests for task 4.9 — get_session_context registration & most-recent-candle wiring.

Feature: session-expiry-awareness

Validates: Requirements 4.1, 4.2, 4.4, 13.2

R4.1  — get_session_context is exposed as an ``@tool``-decorated function named
        ``get_session_context`` following the existing tool pattern in tools.py.
R4.2  — the tool accepts a ``symbol`` and a ``timeframe`` and classifies the
        session for the MOST RECENT available candle of that symbol/timeframe.
R4.4  — the reference timestamp is obtained from the MOST RECENT candle fetched
        from the Rust_Tool_Server for the symbol/timeframe.
R13.2 — the tool derives its result EXCLUSIVELY from the candle timestamp and the
        configured parameters; it consumes NO economic-events calendar, earnings
        dates, options-chain data, or any other external data source.

These are plain pytest unit tests (no hypothesis). The single HTTP entry point
the tool uses — ``tools.httpx.post`` against the Rust ``/tools/get_candles``
endpoint — is mocked so the tests run in-memory with no live Rust Tool_Server.

The undecorated function behind the LangChain ``@tool`` object is reached via
``.func`` (the convention used by the sibling tool tests).
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest import mock

# Make the service package importable (tools.py / session.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import session  # noqa: E402
import tools  # noqa: E402
from tools import SESSION_PHASES, TIME_FAVORABILITY, get_session_context  # noqa: E402

_SYMBOL = "RELIANCE"
_TIMEFRAME = "15m"

# A non-expiry weekday (Monday, weekday 0; default expiry weekday is Thursday=3)
# so the expiry override never muddies the phase->favorability check. 2024-01-08
# is a Monday.
_IST = ZoneInfo("Asia/Kolkata")


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


def _ist_ms(hour, minute):
    """Epoch milliseconds for 2024-01-08 (a Monday) at ``hour:minute`` IST.

    Built via ``zoneinfo`` so the timestamp is host-timezone independent — the
    same wall-clock IST instant regardless of where the test runs.
    """
    dt = datetime(2024, 1, 8, hour, minute, 0, tzinfo=_IST)
    return int(dt.timestamp() * 1000)


def _candle(hour, minute, price=100.0):
    """A well-formed OHLCV candle whose ``timestamp_ms`` is the given IST time."""
    return {
        "timestamp_ms": _ist_ms(hour, minute),
        "open": float(price),
        "high": float(price + 1),
        "low": float(price - 1),
        "close": float(price + 0.5),
        "volume": 10_000.0,
    }


def _call_url(call):
    """Extract the URL from an httpx call (positional or ``url=`` kwarg)."""
    if call.args:
        return call.args[0]
    return call.kwargs.get("url")


# Default-config phase representatives (default open 09:15, close 15:30,
# opening 15m -> 09:15-09:30, midday 11:30-13:30, closing 30m -> 15:00-15:30).
_OPENING = (9, 20)
_MORNING = (10, 0)
_MIDDAY = (12, 0)
_AFTERNOON = (14, 0)
_CLOSING = (15, 10)


# ─────────────────────────────────────────────────────────────────────────────
# R4.1 — registration: the tool exists, is @tool-decorated, and is named.
# ─────────────────────────────────────────────────────────────────────────────
def test_get_session_context_is_registered_tool():
    """Validates: Requirements 4.1

    ``get_session_context`` exists, is a LangChain ``@tool`` object (carries
    ``.name`` / ``.func`` / ``.invoke``), and is named ``get_session_context``.
    """
    # The symbol is importable from tools.
    assert get_session_context is tools.get_session_context

    # A LangChain @tool object exposes a stable ``.name``.
    assert hasattr(get_session_context, "name"), "tool is missing a .name attribute"
    assert get_session_context.name == "get_session_context", (
        f"tool name is {get_session_context.name!r}, expected 'get_session_context'"
    )

    # It wraps an undecorated callable via ``.func`` (the @tool convention used
    # across this codebase) and is invocable via ``.invoke``.
    assert hasattr(get_session_context, "func"), "tool is missing a .func attribute"
    assert callable(get_session_context.func), ".func is not callable"
    assert hasattr(get_session_context, "invoke"), "tool is missing an .invoke method"

    # The two declared arguments are part of the tool's args schema.
    arg_names = set(getattr(get_session_context, "args", {}).keys())
    assert {"symbol", "timeframe"} <= arg_names, (
        f"tool args {arg_names} do not include both 'symbol' and 'timeframe'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R4.2 / R4.4 — the tool classifies the MOST RECENT (last) candle.
# ─────────────────────────────────────────────────────────────────────────────
def _run_with_candles(candles):
    """Invoke get_session_context with ``tools.httpx.post`` mocked to return
    ``candles`` for the candle fetch. Returns (result, mock_post)."""
    def _fake_post(url, json=None, timeout=None, **kwargs):
        assert url.endswith("/tools/get_candles"), (
            f"unexpected non-candle POST endpoint: {url}"
        )
        return _mock_response(candles)

    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post) as mock_post:
        result = _raw(get_session_context)(symbol=_SYMBOL, timeframe=_TIMEFRAME)
    return result, mock_post


def test_tool_classifies_the_most_recent_candle():
    """Validates: Requirements 4.2, 4.4

    Given a multi-candle fetch whose candles land in DISTINCT session phases, the
    tool classifies the LAST (most recent) candle — the result corresponds to the
    last candle's timestamp, not any earlier candle's.
    """
    config = session.resolve_session_config()

    # A chronologically-ordered list spanning several distinct phases; the LAST
    # candle is in the afternoon window.
    candles = [
        _candle(*_OPENING),    # opening
        _candle(*_MORNING),    # morning
        _candle(*_MIDDAY),     # midday
        _candle(*_AFTERNOON),  # afternoon  <- most recent
    ]
    last_ts = candles[-1]["timestamp_ms"]
    first_ts = candles[0]["timestamp_ms"]

    result, _ = _run_with_candles(candles)

    assert isinstance(result, dict), f"expected a dict result, got {type(result)}"
    assert "error" not in result, f"tool returned an error: {result.get('error')}"
    assert "unavailable" not in result, (
        f"tool unexpectedly degraded to unavailable: {result.get('reason')}"
    )

    # The result equals classifying the LAST candle's timestamp...
    expected_last = session.classify_session(
        last_ts, config, symbol=_SYMBOL, timeframe=_TIMEFRAME
    )
    assert result == expected_last, (
        "tool result does not correspond to the most recent (last) candle"
    )

    # ...and crucially NOT the first candle's (the phases are deliberately
    # different, so a tool that read the wrong candle would be caught here).
    expected_first = session.classify_session(
        first_ts, config, symbol=_SYMBOL, timeframe=_TIMEFRAME
    )
    assert expected_last["session_phase"] != expected_first["session_phase"], (
        "test setup error: first and last candle share a phase"
    )
    assert result["session_phase"] != expected_first["session_phase"], (
        "tool classified an earlier candle instead of the most recent one"
    )

    # Concretely: the last candle is in the afternoon window.
    assert result["session_phase"] == "afternoon", (
        f"expected most-recent-candle phase 'afternoon', got "
        f"{result['session_phase']!r}"
    )
    assert result["session_phase"] in SESSION_PHASES
    assert result["time_favorability"] in TIME_FAVORABILITY


def test_tool_tracks_the_last_candle_across_different_orderings():
    """Validates: Requirements 4.2, 4.4

    The classification follows whichever candle is last in the fetched list — a
    second ordering whose final candle is the closing window resolves to the
    closing phase, confirming the tool keys on the most-recent candle rather than
    a fixed position.
    """
    config = session.resolve_session_config()

    candles = [
        _candle(*_MORNING),
        _candle(*_AFTERNOON),
        _candle(*_CLOSING),  # most recent
    ]
    last_ts = candles[-1]["timestamp_ms"]

    result, _ = _run_with_candles(candles)

    assert "error" not in result and "unavailable" not in result, result
    expected_last = session.classify_session(
        last_ts, config, symbol=_SYMBOL, timeframe=_TIMEFRAME
    )
    assert result == expected_last
    assert result["session_phase"] == "closing", (
        f"expected most-recent-candle phase 'closing', got {result['session_phase']!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R13.2 — the tool consults ONLY the candle source (timestamp) + resolved config.
# ─────────────────────────────────────────────────────────────────────────────
def test_tool_consults_only_candle_source_and_config():
    """Validates: Requirements 13.2, 4.4

    The tool derives its result exclusively from the candle timestamp and the
    resolved configuration: the ONLY external call is the Rust
    ``/tools/get_candles`` fetch — no economic-events calendar, earnings, or
    options-chain source is consulted (no GET-based source at all).
    """
    candles = [_candle(*_MORNING), _candle(*_AFTERNOON)]

    def _fake_post(url, json=None, timeout=None, **kwargs):
        return _mock_response(candles)

    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post) as mock_post, \
         mock.patch.object(
             tools.httpx,
             "get",
             side_effect=AssertionError(
                 "get_session_context must not consult any GET data source"
             ),
         ) as mock_get:
        result = _raw(get_session_context)(symbol=_SYMBOL, timeframe=_TIMEFRAME)

    # At least one POST to the candle endpoint was made (R4.4).
    assert mock_post.call_count >= 1, "expected at least one candle fetch"

    post_urls = [_call_url(c) for c in mock_post.call_args_list]

    # EVERY POST URL is the Rust get_candles endpoint — no other source (R13.2).
    for url in post_urls:
        assert url == f"{tools.RUST_SERVER_URL}/tools/get_candles", (
            f"tool consulted a non-candle data endpoint: {url}"
        )

    # No GET-based data source was touched at all (R13.2).
    assert mock_get.call_count == 0, (
        "get_session_context consulted a GET data source; it must derive its "
        "result from the candle timestamp and configured parameters only"
    )

    # No economic / earnings / options / other forbidden endpoint was consulted.
    forbidden = (
        "options", "option_chain", "earnings", "economic", "calendar",
        "events", "get_news_context", "get_prediction", "get_consensus",
    )
    for url in post_urls:
        assert not any(token in url for token in forbidden), (
            f"unexpected non-allowed data source consumed: {url}"
        )

    # The candles were requested for the symbol under analysis.
    post_symbols = [
        (c.kwargs.get("json") or {}).get("symbol") for c in mock_post.call_args_list
    ]
    assert _SYMBOL in post_symbols, f"symbol candles were not fetched: {post_symbols!r}"

    # And a usable label came back from the candle-derived classification.
    assert isinstance(result, dict)
    assert "error" not in result and "unavailable" not in result, result
    assert result.get("session_phase") in SESSION_PHASES
