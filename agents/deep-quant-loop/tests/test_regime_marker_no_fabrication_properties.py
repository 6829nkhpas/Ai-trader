"""Property-based test that an Unavailable_Marker carries no fabricated states
(tools.py ``get_market_regime``, task 5.10).

Feature: regime-detection-gate

This Hypothesis property exercises the ``get_market_regime`` tool in ``tools.py``
with the network call MOCKED, driving every path on which the tool yields an
Unavailable_Marker, and asserts design Property 16: an Unavailable_Marker never
carries fabricated states. Concretely, whenever the tool returns an
Unavailable_Marker it MUST omit ``trend_state``, ``volatility_state`` and
``favorability`` rather than populate them with default / placeholder /
fabricated values (Requirements 4.3, 4.6).

The tool fetches candles via
``httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)`` and reads them with
``response.json()``. Here ``tools.httpx.post`` is patched (same pattern as
``test_regime_tool_success_properties.py`` — ``_mock_response`` / ``_raw``
helpers) to drive the three distinct unavailable paths with NO live Rust
Tool_Server:

  * ``retrieval failure``    — ``post`` raises, so the tool's fetch ``except``
                               degrades to an Unavailable_Marker (R4.1 / R4.5).
  * ``insufficient candles`` — ``post`` returns a too-short candle list, so the
                               classifier's sufficiency gate yields an
                               Unavailable_Marker (R4.2).
  * ``flat / all-null``      — ``post`` returns enough perfectly flat candles, so
                               every Regime_Measure has a zero denominator and is
                               ``None``; the classifier returns an
                               Unavailable_Marker ("no regime measure could be
                               computed").

A VALID symbol + timeframe is always used so the failure happens at/after
retrieval (never on argument validation, which is a different ``error`` path).
"""

import json
import os
import sys
from unittest import mock

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

# The default resolved config gates classification on
# ``max(min_candles=50, largest_lookback)`` where
# ``largest_lookback = vol_period + vol_pctl_window = 14 + 100 = 114``.
# "Insufficient" means fewer than that many valid candles; "flat" needs at least
# that many (so it clears the sufficiency gate and instead trips the all-null
# measure path).
_REQUIRED_CANDLES = 114


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``.

    ``.json()`` yields the candle list the tool reads; ``.raise_for_status()`` is
    a no-op so the mocked retrieval looks successful (the unavailable outcome
    then arises purely from the candle content, not a transport error).
    """
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


def _candle(price: float) -> dict:
    """A single valid, finite OHLCV candle at ``price`` (zero intrabar range)."""
    return {
        "timestamp_ms": 0,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1000.0,
    }


@st.composite
def _insufficient_candles(draw):
    """A valid candle list that is *too short* to classify (fewer than the gate).

    Content is otherwise well-formed (finite OHLCV); only the count matters here,
    so the classifier's sufficiency gate fires and yields an Unavailable_Marker.
    """
    n = draw(st.integers(min_value=0, max_value=_REQUIRED_CANDLES - 1))
    base = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    return [_candle(base + i) for i in range(n)]


@st.composite
def _flat_candles(draw):
    """Enough perfectly flat candles to clear the gate but yield all-null measures.

    Every candle shares one identical price, so each Regime_Measure has a zero
    denominator (zero true-range / zero price-range / zero dispersion) and is
    ``None``; ``classify_regime`` then returns an Unavailable_Marker.
    """
    n = draw(st.integers(min_value=_REQUIRED_CANDLES, max_value=_REQUIRED_CANDLES + 46))
    price = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    return [_candle(price) for _ in range(n)]


# Each scenario is ``(mode, payload)`` where ``mode`` selects how ``httpx.post``
# is configured and ``payload`` is either an exception message (raise mode) or a
# candle list (the response ``.json()`` content).
_scenarios = st.one_of(
    st.tuples(
        st.just("raise"),
        st.text(min_size=0, max_size=40),
    ),
    st.tuples(st.just("insufficient"), _insufficient_candles()),
    st.tuples(st.just("flat"), _flat_candles()),
)

# A VALID, non-empty/non-whitespace symbol and a supported timeframe, so the
# failure always happens at/after retrieval rather than on argument validation.
_valid_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"])
_valid_timeframes = st.sampled_from(sorted(SUPPORTED_TIMEFRAMES))

# The categorical state fields that an Unavailable_Marker must NEVER carry.
_FABRICATABLE_STATE_FIELDS = ("trend_state", "volatility_state", "favorability")


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: An Unavailable_Marker never carries fabricated states
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 16
@settings(max_examples=150, deadline=None)
@given(scenario=_scenarios, symbol=_valid_symbols, timeframe=_valid_timeframes)
def test_property_16_unavailable_marker_carries_no_fabricated_states(
    scenario, symbol, timeframe
):
    """Feature: regime-detection-gate, Property 16: An Unavailable_Marker never
    carries fabricated states — for every path on which ``get_market_regime``
    returns an Unavailable_Marker (retrieval failure, insufficient candles, or an
    all-null/flat window), the result flags ``unavailable`` with a reason and
    OMITS ``trend_state`` / ``volatility_state`` / ``favorability`` rather than
    populating them with default / placeholder / fabricated values. Never raises.

    Validates: Requirements 4.3, 4.6
    """
    mode, payload = scenario

    if mode == "raise":
        # Drive the retrieval-failure path: the mocked POST raises, so the tool's
        # fetch ``except`` degrades to an Unavailable_Marker (R4.1 / R4.5).
        patcher = mock.patch.object(
            tools.httpx, "post", side_effect=RuntimeError(payload or "boom")
        )
    else:
        # Drive the insufficient-data / all-null paths: the mocked POST returns
        # the generated candle list, and the unavailable outcome arises from the
        # classifier (sufficiency gate / all-null measures).
        patcher = mock.patch.object(
            tools.httpx, "post", return_value=_mock_response(payload)
        )

    with patcher:
        result = _raw(get_market_regime)(symbol=symbol, timeframe=timeframe)

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # Every scenario here is engineered to be unavailable.
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker for mode {mode!r}, got: {result!r}"
    )

    # An honest marker must cite a (non-empty) reason for being unavailable.
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker is missing a non-empty reason: {result!r}"
    )

    # The core assertion: the marker must OMIT the categorical state fields — no
    # default, placeholder, or otherwise fabricated trend/volatility/favorability
    # (Requirements 4.3, 4.6).
    for field in _FABRICATABLE_STATE_FIELDS:
        assert field not in result, (
            f"Unavailable_Marker fabricated '{field}'={result.get(field)!r} "
            f"(must be omitted): {result!r}"
        )
