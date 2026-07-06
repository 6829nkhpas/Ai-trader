"""Property-based test for graceful degradation of the get_relative_strength
tool to an Unavailable_Marker (tools.py, task 5.11).

Feature: relative-strength-context

This Hypothesis property exercises the ``get_relative_strength`` tool in
``tools.py`` with candle retrieval FORCED to fail in every distinct way the tool
can observe a failure. It covers design Property 18: the tool degrades to an
Unavailable_Marker on a missing benchmark or any retrieval / processing failure,
and NEVER propagates an exception into the Deep_Quant_Agent loop.

The tool fetches BOTH the symbol candles and the Benchmark_Index candles via
``tools.httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)``, calls
``response.raise_for_status()`` and reads ``response.json()`` (a list of OHLCV
candle dicts). To exercise the failure paths we patch ``tools.httpx.post`` with
a VALID symbol and VALID timeframe (so argument validation passes and the
failure occurs at/after retrieval) and force, per Hypothesis example, one of:

  (a) ``httpx.post`` raising — a timeout, a connection error, a generic
      exception — on every fetch (a retrieval failure);
  (b) ``response.json()`` returning an error payload (``[{"error": ...}]``) or a
      non-list (a dict / a bare string / ``None``) on every fetch;
  (c) both fetches returning a valid-but-too-short candle list, so the
      calculator degrades to an insufficient-data Unavailable_Marker (a
      processing-derived degrade);
  (d) the SYMBOL fetch succeeding while the BENCHMARK fetch fails (raises or
      returns an error payload) — a *missing benchmark* whose marker must NAME
      the benchmark.

For every failure mode the result MUST be an Unavailable_Marker — a dict with
``unavailable is True`` and a non-empty ``reason`` string that OMITS the three
state fields (``index_direction`` / ``relative_strength_state`` / ``alignment``)
— and the tool must never raise.

The mock helpers (``_raw``, ``_mock_response``) follow the same pattern as
``test_regime_tool_degradation_properties.py`` and ``test_rs_tool_fetches_both.py``.
"""

import json
import os
import sys
from unittest import mock

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
import tools  # noqa: E402
from tools import SUPPORTED_TIMEFRAMES, get_relative_strength  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data=None, status_code=200, raise_exc=None):
    """Build a stand-in for an httpx.Response.

    ``.json()`` yields ``json_data``; ``.raise_for_status()`` is a no-op unless
    ``raise_exc`` is provided, in which case it raises that exception.
    """
    resp = mock.Mock()
    resp.status_code = status_code
    try:
        resp.text = json.dumps(json_data)
    except (TypeError, ValueError):
        resp.text = str(json_data)
    resp.json = mock.Mock(return_value=json_data)
    if raise_exc is not None:
        resp.raise_for_status = mock.Mock(side_effect=raise_exc)
    else:
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


# A request stub the httpx error types need for construction.
_DUMMY_REQUEST = httpx.Request("POST", f"{tools.RUST_SERVER_URL}/tools/get_candles")


# ── failure-mode strategy ────────────────────────────────────────────────────
# Each example names a distinct way the candle retrieval / processing can fail.
_failure_modes = st.sampled_from([
    # (a) httpx.post itself raises — timeout / connection / generic.
    "post_timeout",
    "post_connect_error",
    "post_read_timeout",
    "post_generic_exception",
    # (b) response.json() yields an error payload or a non-list.
    "error_payload",
    "non_list_dict",
    "non_list_string",
    "non_list_none",
    "non_list_int",
    # (c) valid-but-too-short candle lists -> insufficient-data degrade.
    "too_few_candles",
    # (d) symbol ok, benchmark fetch fails -> missing benchmark (names it).
    "missing_benchmark_error_payload",
    "missing_benchmark_raises",
])

# Symbols whose Benchmark_Map resolution yields a DISTINCT benchmark, so the
# missing-benchmark modes exercise a real second (benchmark) fetch.
_valid_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"])
_valid_timeframes = st.sampled_from(sorted(SUPPORTED_TIMEFRAMES))

_STATE_FIELDS = ("index_direction", "relative_strength_state", "alignment")


def _exc_for(mode):
    """The exception object a 'post raises' mode should raise."""
    return {
        "post_timeout": httpx.TimeoutException("read timed out", request=_DUMMY_REQUEST),
        "post_connect_error": httpx.ConnectError("connection refused", request=_DUMMY_REQUEST),
        "post_read_timeout": httpx.ReadTimeout("read timed out", request=_DUMMY_REQUEST),
        "post_generic_exception": RuntimeError("unexpected boom"),
    }[mode]


def _bad_payload_for(mode):
    """The non-usable JSON payload a 'bad payload' mode should return."""
    return {
        "error_payload": [{"error": "Failed to retrieve candles from Rust server: boom"}],
        "non_list_dict": {"unexpected": "object instead of list"},
        "non_list_string": "a bare string, not a list",
        "non_list_none": None,
        "non_list_int": 42,
    }[mode]


def _make_post_side_effect(mode, symbol, benchmark):
    """Build an ``httpx.post`` side_effect implementing failure ``mode``.

    Modes that target only the benchmark fetch return valid candles for the
    symbol and fail for the benchmark; all other modes fail uniformly.
    """
    valid = _valid_candles(80, base=100.0)
    short = _valid_candles(4, base=100.0)  # < min aligned candles -> insufficient

    def _requested_symbol(json_body):
        return (json_body or {}).get("symbol")

    def _side_effect(url, json=None, timeout=None, **kwargs):
        requested = _requested_symbol(json)

        if mode in ("post_timeout", "post_connect_error", "post_read_timeout",
                    "post_generic_exception"):
            raise _exc_for(mode)

        if mode in ("error_payload", "non_list_dict", "non_list_string",
                    "non_list_none", "non_list_int"):
            return _mock_response(json_data=_bad_payload_for(mode))

        if mode == "too_few_candles":
            return _mock_response(json_data=short)

        if mode == "missing_benchmark_error_payload":
            if requested == symbol:
                return _mock_response(json_data=valid)
            return _mock_response(
                json_data=[{"error": f"no candles for benchmark {requested}"}]
            )

        if mode == "missing_benchmark_raises":
            if requested == symbol:
                return _mock_response(json_data=valid)
            raise httpx.ConnectError("benchmark feed down", request=_DUMMY_REQUEST)

        raise AssertionError(f"unhandled failure mode {mode!r}")  # pragma: no cover

    return _side_effect


def _assert_unavailable_marker(result, benchmark, mode):
    """Assert ``result`` is a well-formed Unavailable_Marker with no states."""
    assert isinstance(result, dict), f"result is not a dict: {result!r}"
    assert result.get("unavailable") is True, (
        f"[{mode}] result is not an Unavailable_Marker (unavailable!=True): {result!r}"
    )
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"[{mode}] Unavailable_Marker carries no non-empty reason: {result!r}"
    )
    # The three state fields MUST be omitted, never fabricated (R5.3).
    for field in _STATE_FIELDS:
        assert field not in result, (
            f"[{mode}] Unavailable_Marker fabricated state field "
            f"'{field}'={result.get(field)!r}: {result!r}"
        )
    # A missing-benchmark marker must NAME the benchmark (R2.4).
    if mode.startswith("missing_benchmark"):
        assert benchmark in reason, (
            f"[{mode}] missing-benchmark reason does not name benchmark "
            f"{benchmark!r}: {reason!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Feature: relative-strength-context, Property 18: The tool degrades to an
# Unavailable_Marker on missing benchmark or any retrieval/processing failure
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    symbol=_valid_symbols,
    timeframe=_valid_timeframes,
    mode=_failure_modes,
)
def test_property_18_degrades_to_unavailable_marker(symbol, timeframe, mode):
    """Feature: relative-strength-context, Property 18: The tool degrades to an
    Unavailable_Marker on missing benchmark or any retrieval/processing failure.

    For a VALID symbol and timeframe (so argument validation passes), every way
    relative-strength retrieval / processing can fail — ``httpx.post`` raising
    (timeout / connection error / generic exception), ``response.json()``
    yielding an error payload or a non-list, valid-but-too-few candles, or a
    failing benchmark fetch (missing benchmark) — must make
    ``get_relative_strength`` return an Unavailable_Marker (``unavailable: True``
    with a non-empty ``reason``) that OMITS index_direction /
    relative_strength_state / alignment, never raising or propagating an
    exception.

    Validates: Requirements 2.4, 5.1, 5.5
    """
    benchmark = rs.resolve_benchmark(symbol)

    side_effect = _make_post_side_effect(mode, symbol, benchmark)
    with mock.patch.object(tools.httpx, "post", side_effect=side_effect):
        # The tool must NOT raise — any escape of an exception fails the property.
        try:
            result = _raw(get_relative_strength)(symbol=symbol, timeframe=timeframe,
                                                 proposed_direction="BUY")
        except Exception as exc:  # pragma: no cover - property failure path
            raise AssertionError(
                f"get_relative_strength propagated an exception on failure mode "
                f"{mode!r}: {exc!r}"
            )

    _assert_unavailable_marker(result, benchmark, mode)
