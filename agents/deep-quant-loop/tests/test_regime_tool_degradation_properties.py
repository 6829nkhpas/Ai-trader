"""Property-based test for graceful degradation to an Unavailable_Marker (tools.py, task 5.9).

Feature: regime-detection-gate

This Hypothesis property exercises the ``get_market_regime`` tool in ``tools.py``
with the candle retrieval FORCED to fail in every distinct way the tool can
observe a failure. It covers design Property 15: the tool degrades to an
Unavailable_Marker on any retrieval or processing failure, and NEVER propagates
an exception into the Deep_Quant_Agent loop.

The tool fetches candles via ``tools.httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)``
then calls ``response.raise_for_status()`` and reads ``response.json()`` (a list
of OHLCV candle dicts). To exercise the failure paths we patch ``tools.httpx.post``
to, for a VALID symbol and VALID timeframe (so argument validation passes and the
failure occurs at/after retrieval):

  (a) raise various exceptions directly from ``post`` — a timeout, a connection
      error, and a generic ``Exception``;
  (b) return a stand-in response whose ``.raise_for_status()`` raises (a non-200
      HTTP status surfacing as an ``HTTPStatusError``);
  (c) return a stand-in response whose ``.json()`` yields an error payload
      (``[{"error": ...}]``) or a non-list (a dict / a string / ``None``).

For every failure mode the result MUST be an Unavailable_Marker — a dict with
``unavailable is True`` and a non-empty ``reason`` string — and the tool must
never raise.

The mock helpers (``_raw``, ``_mock_response``) follow the same pattern as
``test_regime_tool_success_properties.py``.
"""

import json
import os
import sys
from unittest import mock

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    SUPPORTED_TIMEFRAMES,
    get_market_regime,
)


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data=None, status_code=200, raise_exc=None):
    """Build a stand-in for an httpx.Response.

    ``.json()`` yields ``json_data``; ``.raise_for_status()`` is a no-op unless
    ``raise_exc`` is provided, in which case it raises that exception (modelling
    a non-200 HTTP status).
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


# A request/response stub the httpx error types need for construction.
_DUMMY_REQUEST = httpx.Request("POST", f"{tools.RUST_SERVER_URL}/tools/get_candles")


def _post_raises(exc):
    """A side_effect callable that raises ``exc`` when httpx.post is called."""

    def _side_effect(*args, **kwargs):
        raise exc

    return _side_effect


# ── Failure-mode strategies ──────────────────────────────────────────────────
# (a) httpx.post itself raises — timeout, connection error, generic Exception.
_post_raise_modes = st.sampled_from(
    [
        httpx.TimeoutException("read timed out", request=_DUMMY_REQUEST),
        httpx.ConnectError("connection refused", request=_DUMMY_REQUEST),
        httpx.ReadTimeout("read timed out", request=_DUMMY_REQUEST),
        RuntimeError("unexpected boom"),
        Exception("generic failure"),
    ]
).map(lambda exc: ("post_raises", exc))

# (b) response.raise_for_status() raises (non-200 HTTP status).
_raise_for_status_modes = st.sampled_from([500, 502, 404, 400, 503]).map(
    lambda code: (
        "raise_for_status",
        httpx.HTTPStatusError(
            f"server returned {code}",
            request=_DUMMY_REQUEST,
            response=httpx.Response(code, request=_DUMMY_REQUEST),
        ),
    )
)

# (c) response.json() yields an error payload or a non-list.
_bad_payload_modes = st.sampled_from(
    [
        [{"error": "Failed to retrieve candles from Rust server: boom"}],
        [{"error": "upstream timeout"}],
        {"unexpected": "object instead of list"},
        "a bare string, not a list",
        None,
        42,
    ]
).map(lambda payload: ("bad_payload", payload))

_failure_modes = st.one_of(
    _post_raise_modes, _raise_for_status_modes, _bad_payload_modes
)

_valid_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"])
_valid_timeframes = st.sampled_from(sorted(SUPPORTED_TIMEFRAMES))


def _assert_unavailable_marker(result):
    """Assert ``result`` is a well-formed Unavailable_Marker (no fabricated states)."""
    assert isinstance(result, dict), f"result is not a dict: {result!r}"
    assert result.get("unavailable") is True, (
        f"result is not an Unavailable_Marker (unavailable!=True): {result!r}"
    )
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker carries no non-empty reason: {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 15: The tool degrades to an Unavailable_Marker on any retrieval or
# processing failure
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 15
@settings(max_examples=200, deadline=None)
@given(
    symbol=_valid_symbols,
    timeframe=_valid_timeframes,
    mode=_failure_modes,
)
def test_property_15_degrades_to_unavailable_on_failure(symbol, timeframe, mode):
    """Feature: regime-detection-gate, Property 15: The tool degrades to an
    Unavailable_Marker on any retrieval or processing failure.

    For a VALID symbol and timeframe (so argument validation passes), every way
    the candle retrieval can fail — ``httpx.post`` raising (timeout / connection
    error / generic exception), ``raise_for_status`` raising on a non-200 status,
    or ``response.json()`` yielding an error payload or a non-list — must make
    ``get_market_regime`` return an Unavailable_Marker (``unavailable: True`` with
    a ``reason``), never raising or propagating an exception.

    Validates: Requirements 4.1, 4.5
    """
    kind, value = mode

    if kind == "post_raises":
        post_patch = mock.patch.object(
            tools.httpx, "post", side_effect=_post_raises(value)
        )
    elif kind == "raise_for_status":
        post_patch = mock.patch.object(
            tools.httpx,
            "post",
            return_value=_mock_response(raise_exc=value),
        )
    else:  # bad_payload
        post_patch = mock.patch.object(
            tools.httpx,
            "post",
            return_value=_mock_response(json_data=value),
        )

    with post_patch:
        # The tool must NOT raise — any escape of an exception fails the property.
        try:
            result = _raw(get_market_regime)(symbol=symbol, timeframe=timeframe)
        except Exception as exc:  # pragma: no cover - property failure path
            raise AssertionError(
                f"get_market_regime propagated an exception on failure mode "
                f"{kind}/{value!r}: {exc!r}"
            )

    _assert_unavailable_marker(result)
