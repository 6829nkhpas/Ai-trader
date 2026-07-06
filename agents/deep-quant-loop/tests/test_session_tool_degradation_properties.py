# Feature: session-expiry-awareness, Property 15: The tool degrades to an Unavailable_Marker on any candle-retrieval or processing failure
"""Property-based test for graceful degradation of the get_session_context
tool to an Unavailable_Marker (tools.py, task 4.5).

Feature: session-expiry-awareness

This Hypothesis property exercises the ``get_session_context`` tool in
``tools.py`` with reference-candle retrieval / processing FORCED to fail in
every distinct way the tool can observe a failure. It covers design
**Property 15**: the tool degrades to an Unavailable_Marker on any
candle-retrieval or processing failure, and NEVER propagates an exception into
the Deep_Quant_Agent loop.

The tool fetches the most recent candle via
``tools.httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)``, calls
``response.raise_for_status()`` and reads ``response.json()`` (a list of OHLCV
candle dicts), then reads the last candle's ``timestamp_ms`` and classifies it.
To exercise the failure paths we patch ``tools.httpx.post`` with a VALID symbol
and VALID timeframe (so argument validation passes and the failure occurs
at/after retrieval) and force, per Hypothesis example, one of:

  (a) ``httpx.post`` raising — a timeout, a connection error, a read timeout, or
      a generic exception — on the fetch (a retrieval failure);
  (b) ``response.raise_for_status()`` raising an HTTP status error (a non-200
      response);
  (c) ``response.json()`` returning an error payload (``[{"error": ...}]``), an
      empty list, or a non-list (a dict / a bare string / ``None``);
  (d) a valid candle list whose most-recent candle has a missing / non-numeric /
      ``None`` ``timestamp_ms`` (a downstream processing degrade via
      ``session.classify_session`` -> invalid-timestamp Unavailable_Marker), or
      whose most-recent element is not an object at all.

For every failure mode the result MUST be an Unavailable_Marker — a dict with
``unavailable is True`` and a non-empty ``reason`` string that OMITS the
``session_phase`` and ``time_favorability`` fields — and the tool must never
raise (Requirements 5.1, 5.4).

The mock helpers (``_raw``, ``_mock_response``) follow the same pattern as
``test_rs_tool_degradation_properties.py`` /
``test_regime_tool_degradation_properties.py``.
"""

import json
import os
import sys
from unittest import mock

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / session.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import SUPPORTED_TIMEFRAMES, get_session_context  # noqa: E402


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


def _valid_candle(timestamp_ms, base=100.0):
    """A single well-formed OHLCV candle carrying ``timestamp_ms``."""
    return {
        "timestamp_ms": timestamp_ms,
        "open": float(base),
        "high": float(base + 1),
        "low": float(base - 1),
        "close": float(base + 0.5),
        "volume": 10_000.0,
    }


# A request stub the httpx error types need for construction.
_DUMMY_REQUEST = httpx.Request("POST", f"{tools.RUST_SERVER_URL}/tools/get_candles")


# ── failure-mode strategy ────────────────────────────────────────────────────
# Each example names a distinct way the candle retrieval / processing can fail.
_failure_modes = st.sampled_from([
    # (a) httpx.post itself raises — timeout / connection / read timeout / generic.
    "post_timeout",
    "post_connect_error",
    "post_read_timeout",
    "post_generic_exception",
    # (b) response.raise_for_status() raises (a non-200 response).
    "http_status_error",
    # (c) response.json() yields an error payload / empty list / non-list.
    "error_payload",
    "empty_list",
    "non_list_dict",
    "non_list_string",
    "non_list_none",
    "non_list_int",
    # (d) valid list but the most-recent candle's timestamp is unusable, or the
    #     most-recent element is not an object -> processing degrade.
    "candle_missing_timestamp",
    "candle_none_timestamp",
    "candle_nonnumeric_timestamp",
    "candle_nan_timestamp",
    "candle_inf_timestamp",
    "candle_not_object",
])

_valid_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"])
_valid_timeframes = st.sampled_from(sorted(SUPPORTED_TIMEFRAMES))

_OMITTED_FIELDS = ("session_phase", "time_favorability")


def _exc_for(mode):
    """The exception object a 'post raises' mode should raise."""
    return {
        "post_timeout": httpx.TimeoutException("read timed out", request=_DUMMY_REQUEST),
        "post_connect_error": httpx.ConnectError("connection refused", request=_DUMMY_REQUEST),
        "post_read_timeout": httpx.ReadTimeout("read timed out", request=_DUMMY_REQUEST),
        "post_generic_exception": RuntimeError("unexpected boom"),
    }[mode]


def _make_post_side_effect(mode):
    """Build an ``httpx.post`` side_effect implementing failure ``mode``."""

    def _side_effect(url, json=None, timeout=None, **kwargs):
        # (a) the POST itself raises.
        if mode in ("post_timeout", "post_connect_error", "post_read_timeout",
                    "post_generic_exception"):
            raise _exc_for(mode)

        # (b) the response is a non-200 -> raise_for_status raises.
        if mode == "http_status_error":
            resp = _mock_response(json_data=[], status_code=503)
            resp.raise_for_status = mock.Mock(
                side_effect=httpx.HTTPStatusError(
                    "503 Service Unavailable",
                    request=_DUMMY_REQUEST,
                    response=resp,
                )
            )
            return resp

        # (c) usable-status response but the JSON payload is not usable.
        if mode == "error_payload":
            return _mock_response(json_data=[{"error": "no candles for symbol"}])
        if mode == "empty_list":
            return _mock_response(json_data=[])
        if mode == "non_list_dict":
            return _mock_response(json_data={"unexpected": "object instead of list"})
        if mode == "non_list_string":
            return _mock_response(json_data="a bare string, not a list")
        if mode == "non_list_none":
            return _mock_response(json_data=None)
        if mode == "non_list_int":
            return _mock_response(json_data=42)

        # (d) valid list, but the most-recent candle's timestamp is unusable.
        if mode == "candle_missing_timestamp":
            candle = _valid_candle(0)
            candle.pop("timestamp_ms")
            return _mock_response(json_data=[candle])
        if mode == "candle_none_timestamp":
            return _mock_response(json_data=[_valid_candle(None)])
        if mode == "candle_nonnumeric_timestamp":
            return _mock_response(json_data=[_valid_candle("not-a-number")])
        if mode == "candle_nan_timestamp":
            return _mock_response(json_data=[_valid_candle(float("nan"))])
        if mode == "candle_inf_timestamp":
            return _mock_response(json_data=[_valid_candle(float("inf"))])
        if mode == "candle_not_object":
            return _mock_response(json_data=["not-a-candle-object"])

        raise AssertionError(f"unhandled failure mode {mode!r}")  # pragma: no cover

    return _side_effect


def _assert_unavailable_marker(result, mode):
    """Assert ``result`` is a well-formed Unavailable_Marker with no label fields."""
    assert isinstance(result, dict), f"[{mode}] result is not a dict: {result!r}"
    assert result.get("unavailable") is True, (
        f"[{mode}] result is not an Unavailable_Marker (unavailable!=True): {result!r}"
    )
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"[{mode}] Unavailable_Marker carries no non-empty reason: {result!r}"
    )
    # session_phase / time_favorability MUST be omitted, never fabricated (R5.2).
    for field in _OMITTED_FIELDS:
        assert field not in result, (
            f"[{mode}] Unavailable_Marker fabricated field "
            f"'{field}'={result.get(field)!r}: {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Property 15: The tool degrades to an Unavailable_Marker on any candle-retrieval
#              or processing failure
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=150, deadline=None)
@given(
    symbol=_valid_symbols,
    timeframe=_valid_timeframes,
    mode=_failure_modes,
)
def test_property_15_degrades_to_unavailable_marker(symbol, timeframe, mode):
    """Feature: session-expiry-awareness, Property 15: The tool degrades to an
    Unavailable_Marker on any candle-retrieval or processing failure.

    For a VALID symbol and timeframe (so argument validation passes), every way
    reference-candle retrieval / processing can fail — ``httpx.post`` raising
    (timeout / connection error / read timeout / generic exception), a non-200
    response (``raise_for_status`` raising), ``response.json()`` yielding an
    error payload / empty list / non-list, or a most-recent candle whose
    ``timestamp_ms`` is missing / non-numeric / non-finite (or which is not an
    object at all) — must make ``get_session_context`` return an
    Unavailable_Marker (``unavailable: True`` with a non-empty ``reason``) that
    OMITS session_phase / time_favorability, never raising or propagating an
    exception into the agent loop.

    Validates: Requirements 5.1, 5.4
    """
    side_effect = _make_post_side_effect(mode)
    with mock.patch.object(tools.httpx, "post", side_effect=side_effect):
        # The tool must NOT raise — any escape of an exception fails the property.
        try:
            result = _raw(get_session_context)(symbol=symbol, timeframe=timeframe)
        except Exception as exc:  # pragma: no cover - property failure path
            raise AssertionError(
                f"get_session_context propagated an exception on failure mode "
                f"{mode!r}: {exc!r}"
            )

    _assert_unavailable_marker(result, mode)
