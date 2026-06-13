"""Property-based test for graceful degradation of the get_forecast tool to an
Unavailable_Marker (tools.py, task 6.10).

Feature: volatility-aware-forecaster

This Hypothesis property exercises the ``get_forecast`` tool in ``tools.py`` with
candle retrieval / processing FORCED to fail in every distinct way the tool can
observe a failure. It covers design Property 20: the tool degrades to an
Unavailable_Marker on any retrieval or processing failure, and NEVER propagates
an exception into the Deep_Quant_Agent loop.

The tool fetches the symbol candles via
``tools.httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)``, calls
``response.raise_for_status()`` and reads ``response.json()`` (a list of OHLCV
candle dicts), then classifies them with the pure ``forecaster.forecast``. To
exercise the failure paths we patch ``tools.httpx.post`` (and, for the
processing-failure mode, ``tools.forecaster.forecast``) with a VALID symbol and
VALID timeframe (so argument validation passes and the failure occurs at/after
retrieval) and force, per Hypothesis example, one of:

  (a) ``httpx.post`` raising — a timeout, a connection error, a generic
      exception — (a retrieval failure);
  (b) ``response.json()`` returning an error payload (``[{"error": ...}]``) or a
      non-list (a dict / a bare string / ``None`` / an int);
  (c) a valid-but-too-short / empty candle list, so the forecaster degrades to an
      insufficient-data Unavailable_Marker (a processing-derived degrade);
  (d) ``forecaster.forecast`` itself raising, so the tool's catch-all returns a
      marker rather than propagating the exception.

For every failure mode the result MUST be an Unavailable_Marker — a dict with
``unavailable is True`` and a non-empty ``reason`` string that OMITS the five
projection fields (``projected_direction`` / ``up_probability`` /
``expected_move_atr`` / ``forecast_confidence`` / ``forecast_alignment``) — and
the tool must never raise.

The mock helpers (``_raw``, ``_mock_response``) follow the same pattern as
``test_rs_tool_degradation_properties.py`` and
``test_regime_tool_degradation_properties.py``.
"""

import contextlib
import json
import os
import sys
from unittest import mock

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import SUPPORTED_TIMEFRAMES, get_forecast  # noqa: E402


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
    # (c) empty / valid-but-too-short candle lists -> insufficient-data degrade.
    "empty_candles",
    "too_few_candles",
    # (d) forecaster.forecast raises -> tool catch-all returns a marker.
    "processing_failure",
])

_valid_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"])
_valid_timeframes = st.sampled_from(sorted(SUPPORTED_TIMEFRAMES))

# The five projection fields an Unavailable_Marker must NEVER carry (R6.3).
_PROJECTION_FIELDS = (
    "projected_direction",
    "up_probability",
    "expected_move_atr",
    "forecast_confidence",
    "forecast_alignment",
)


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


def _make_post_side_effect(mode):
    """Build an ``httpx.post`` side_effect implementing failure ``mode``."""
    short = _valid_candles(4, base=100.0)  # < min candles -> insufficient

    def _side_effect(url, json=None, timeout=None, **kwargs):
        if mode in ("post_timeout", "post_connect_error", "post_read_timeout",
                    "post_generic_exception"):
            raise _exc_for(mode)

        if mode in ("error_payload", "non_list_dict", "non_list_string",
                    "non_list_none", "non_list_int"):
            return _mock_response(json_data=_bad_payload_for(mode))

        if mode == "empty_candles":
            return _mock_response(json_data=[])

        if mode == "too_few_candles":
            return _mock_response(json_data=short)

        if mode == "processing_failure":
            # Valid candles reach the forecaster, which we make raise so the
            # tool's catch-all must produce a marker.
            return _mock_response(json_data=_valid_candles(120, base=100.0))

        raise AssertionError(f"unhandled failure mode {mode!r}")  # pragma: no cover

    return _side_effect


def _assert_unavailable_marker(result, mode):
    """Assert ``result`` is a well-formed Unavailable_Marker with no projections."""
    assert isinstance(result, dict), f"[{mode}] result is not a dict: {result!r}"
    assert result.get("unavailable") is True, (
        f"[{mode}] result is not an Unavailable_Marker (unavailable!=True): {result!r}"
    )
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"[{mode}] Unavailable_Marker carries no non-empty reason: {result!r}"
    )
    # The five projection fields MUST be omitted, never fabricated (R6.3).
    for field in _PROJECTION_FIELDS:
        assert field not in result, (
            f"[{mode}] Unavailable_Marker fabricated projection field "
            f"'{field}'={result.get(field)!r}: {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Feature: volatility-aware-forecaster, Property 20: The tool degrades to an
# Unavailable_Marker on any retrieval or processing failure
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    symbol=_valid_symbols,
    timeframe=_valid_timeframes,
    mode=_failure_modes,
)
def test_property_20_degrades_to_unavailable_marker(symbol, timeframe, mode):
    """Feature: volatility-aware-forecaster, Property 20: The tool degrades to an
    Unavailable_Marker on any retrieval or processing failure.

    For a VALID symbol and timeframe (so argument validation passes), every way
    forecast retrieval / processing can fail — ``httpx.post`` raising (timeout /
    connection error / generic exception), ``response.json()`` yielding an error
    payload or a non-list, an empty / too-few candle list, or ``forecast``
    itself raising — must make ``get_forecast`` return an Unavailable_Marker
    (``unavailable: True`` with a non-empty ``reason``) that OMITS
    projected_direction / up_probability / expected_move_atr /
    forecast_confidence / forecast_alignment, never raising or propagating an
    exception.

    Validates: Requirements 6.1, 6.5
    """
    side_effect = _make_post_side_effect(mode)

    with mock.patch.object(tools.httpx, "post", side_effect=side_effect):
        if mode == "processing_failure":
            # Force a processing failure inside the pure forecaster to confirm
            # the tool's catch-all degrades rather than propagating.
            forecast_patch = mock.patch.object(
                tools.forecaster,
                "forecast",
                side_effect=RuntimeError("forecast math blew up"),
            )
        else:
            forecast_patch = contextlib.nullcontext()

        with forecast_patch:
            # The tool must NOT raise — any escape of an exception fails the property.
            try:
                result = _raw(get_forecast)(
                    symbol=symbol, timeframe=timeframe, proposed_direction="BUY"
                )
            except Exception as exc:  # pragma: no cover - property failure path
                raise AssertionError(
                    f"get_forecast propagated an exception on failure mode "
                    f"{mode!r}: {exc!r}"
                )

    _assert_unavailable_marker(result, mode)
