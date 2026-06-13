"""Property-based test for a well-formed successful tool result (tools.py, task 6.6).

Feature: order-flow-context

This Hypothesis property exercises the ``get_order_flow`` tool in ``tools.py``
with BOTH of its I/O paths MOCKED. It covers design **Property 17: A successful
tool result is well-formed**: for any symbol candle data sufficient to classify
(the Rust candle retrieval mocked) and any live-tick dataset (the QuestDB
``live_ticks`` query mocked — a usable tick set or empty), a successful
(non-unavailable, non-error) ``get_order_flow`` result is well-formed — it
carries ``order_flow_state`` in its enum, ``alignment`` in its enum, the five
named Order_Flow_Proxy_Measures each present as a finite number or ``null``
(None) under a ``measures`` dict, ``tick_ofi`` as a finite number or ``null``,
and a boolean ``live_tick_contributed`` flag (Requirements 5.5, 3.5).

The tool's two I/O calls are mocked at the ``httpx`` level, exactly where
``_fetch_candles_for_rs`` and ``_read_live_ticks`` reach the network:

  * ``tools.httpx.post`` -> the Rust ``/tools/get_candles`` POST. Patched to
    return a generated valid OHLCV candle list (>= the classifier's
    largest-lookback gate), read by the tool via ``response.json()``.
  * ``tools.httpx.get``  -> the QuestDB ``/exec`` GET against ``live_ticks``.
    Patched to return a QuestDB-shaped ``{"dataset": [...]}`` body (rows of
    ``[last_traded_price, volume, best_bid, best_ask]``) — sometimes a usable
    tick set (so ``live_tick_contributed`` can be true) and sometimes empty (so
    only the candle-derived proxy layer is used and the flag is false).

The full tool path runs (arg validation -> config resolution -> candle fetch ->
live-tick read -> classify -> contract re-validation) with NO live Rust
Tool_Server and NO live QuestDB. Some generated inputs may still degenerate and
yield an Unavailable_Marker (e.g. an all-null proxy window with no usable
ticks); those are skipped via ``assume`` because this property asserts only over
produced Order_Flow_Labels.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_rs_tool_wellformed_properties.py`` and
``tests/test_regime_tool_success_properties.py``.
"""

import json
import math
import os
import sys
from unittest import mock

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / order_flow.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    ORDER_FLOW_STATES,
    _OF_MEASURE_FIELDS,
    get_order_flow,
)

# The default resolved config gates on ``largest_lookback = max(lookback=20,
# min_candles=20) = 20`` valid candles. Generate comfortably more than that so
# the classifier reliably produces an Order_Flow_Label (not Unavailable).
_MIN_CANDLES = 40
_MAX_CANDLES = 70

_SYMBOL = "RELIANCE"
_TIMEFRAME = "15m"


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``.

    ``.json()`` yields the payload the tool reads; ``.raise_for_status()`` is a
    no-op so the mocked retrieval looks successful.
    """
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


@st.composite
def _candle_walk(draw, n):
    """A sequence of ``n`` valid OHLCV candle dicts following a random price walk.

    Every candle's OHLCV fields are finite numbers, consecutive closes move, and
    the high-low range is positive, so the path has real movement (keeping the
    proxy denominators generally non-zero). Volumes are positive. Shape matches
    what the Rust ``/tools/get_candles`` endpoint returns and what the
    Order_Flow_Calculator reads via ``candle.get(...)``.
    """
    price = draw(
        st.floats(min_value=10.0, max_value=10_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    candles = []
    for i in range(n):
        step = draw(
            st.floats(min_value=-50.0, max_value=50.0,
                      allow_nan=False, allow_infinity=False)
        )
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.1, max_value=10.0,
                      allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(
                st.floats(min_value=0.1, max_value=10.0,
                          allow_nan=False, allow_infinity=False)
            ),
            0.5,
        )
        candles.append(
            {
                "timestamp_ms": i * 1000,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0 + i,
            }
        )
        price = new_price
    return candles


@st.composite
def _tick_dataset(draw):
    """A QuestDB-shaped ``{"dataset": [...]}`` body for the live_ticks query.

    Each row is ``[last_traded_price, volume, best_bid, best_ask]`` where
    ``volume`` is the day's CUMULATIVE traded volume (so consecutive forward
    deltas are positive — the shape the Tick_OFI consumes). ``_read_live_ticks``
    reverses the dataset to oldest-first, so we build the chronological sequence
    first and store it reversed.

    With some probability we return an EMPTY dataset (no rows) so the Tick_OFI is
    unavailable and only the candle-derived proxy layer is used — exercising
    ``live_tick_contributed == False``.
    """
    if draw(st.booleans()):
        return {"dataset": []}

    n = draw(st.integers(min_value=12, max_value=40))
    price = draw(
        st.floats(min_value=10.0, max_value=10_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    cumulative = draw(
        st.floats(min_value=0.0, max_value=1000.0,
                  allow_nan=False, allow_infinity=False)
    )
    chronological = []
    for _ in range(n):
        step = draw(
            st.floats(min_value=-5.0, max_value=5.0,
                      allow_nan=False, allow_infinity=False)
        )
        price = max(price + step, 1.0)
        cumulative += draw(
            st.floats(min_value=1.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False)
        )
        spread = draw(
            st.floats(min_value=0.0, max_value=2.0,
                      allow_nan=False, allow_infinity=False)
        )
        best_bid = max(price - spread, 0.5)
        best_ask = price + spread
        chronological.append([price, cumulative, best_bid, best_ask])

    # ``_read_live_ticks`` reverses the dataset (DESC query -> oldest-first), so
    # store the reverse of the chronological sequence.
    return {"dataset": list(reversed(chronological))}


def _is_finite_or_null(value) -> bool:
    """True when ``value`` is None or a finite real number (bool excluded)."""
    if value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 17: A successful tool result is well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 17: A successful tool result is well-formed
@settings(max_examples=150, deadline=None)
@given(
    candle_count=st.integers(min_value=_MIN_CANDLES, max_value=_MAX_CANDLES),
    data=st.data(),
)
def test_property_17_successful_tool_result_is_well_formed(candle_count, data):
    """Feature: order-flow-context, Property 17: A successful tool result is
    well-formed — for any symbol candle data sufficient to classify (the Rust
    candle retrieval MOCKED) and any live-tick dataset (the QuestDB live_ticks
    query MOCKED, usable or empty), a non-unavailable, non-error
    ``get_order_flow`` result carries ``order_flow_state`` in its enum,
    ``alignment`` in its enum, the five named proxy measures each finite-or-null
    under ``measures``, ``tick_ofi`` finite-or-null, and a boolean
    ``live_tick_contributed`` flag.

    Validates: Requirements 5.5, 3.5
    """
    candles = data.draw(_candle_walk(candle_count))
    tick_body = data.draw(_tick_dataset())
    proposed_direction = data.draw(st.sampled_from(["", "BUY", "SELL"]))

    def _fake_post(url, json=None, timeout=None, **kwargs):
        # The proxy layer fetches the symbol candles from the Rust Tool_Server.
        assert "/tools/get_candles" in url, f"unexpected POST url: {url!r}"
        assert (json or {}).get("symbol") == _SYMBOL
        return _mock_response(candles)

    def _fake_get(url, params=None, timeout=None, **kwargs):
        # The Tick_OFI layer reads recent ticks from the QuestDB /exec API.
        assert "/exec" in url, f"unexpected GET url: {url!r}"
        return _mock_response(tick_body)

    # Mock BOTH I/O paths so the tool runs against generated data with no live
    # Rust Tool_Server and no live QuestDB.
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post), \
         mock.patch.object(tools.httpx, "get", side_effect=_fake_get):
        result = _raw(get_order_flow)(
            symbol=_SYMBOL,
            timeframe=_TIMEFRAME,
            proposed_direction=proposed_direction,
        )

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # A degenerate input can still yield an Unavailable_Marker (e.g. an all-null
    # proxy window with no usable ticks); this property asserts only over
    # produced Order_Flow_Labels.
    assume("unavailable" not in result)
    assume("error" not in result)

    # ── order_flow_state in its fixed enum (R5.5) ─────────────────────────────
    assert result.get("order_flow_state") in ORDER_FLOW_STATES, (
        f"order_flow_state {result.get('order_flow_state')!r} not in {ORDER_FLOW_STATES}"
    )

    # ── alignment in its fixed enum (R5.5) ────────────────────────────────────
    assert result.get("alignment") in ALIGNMENT_VALUES, (
        f"alignment {result.get('alignment')!r} not in {ALIGNMENT_VALUES}"
    )

    # ── the five named proxy measures, each finite-or-null, under 'measures' ──
    measures = result.get("measures")
    assert isinstance(measures, dict), f"'measures' is not a dict: {measures!r}"
    for field in _OF_MEASURE_FIELDS:
        assert field in measures, f"measure '{field}' missing from {measures!r}"
        value = measures[field]
        assert _is_finite_or_null(value), (
            f"measure '{field}' is neither a finite number nor null: {value!r}"
        )

    # ── tick_ofi present as a finite number or null (R5.5) ────────────────────
    assert "tick_ofi" in result, "result missing 'tick_ofi'"
    assert _is_finite_or_null(result["tick_ofi"]), (
        f"tick_ofi is neither a finite number nor null: {result['tick_ofi']!r}"
    )
    # When tick_ofi is a finite number it is bounded to [-1.0, 1.0].
    if result["tick_ofi"] is not None:
        assert -1.0 <= result["tick_ofi"] <= 1.0, (
            f"tick_ofi out of bounds: {result['tick_ofi']!r}"
        )

    # ── live_tick_contributed is a boolean flag (R3.5) ────────────────────────
    assert isinstance(result.get("live_tick_contributed"), bool), (
        f"live_tick_contributed is not a boolean: {result.get('live_tick_contributed')!r}"
    )
    # The flag is true exactly when a usable (finite) Tick_OFI was produced.
    assert result["live_tick_contributed"] == (result["tick_ofi"] is not None), (
        "live_tick_contributed must be true iff tick_ofi is a usable finite value"
    )
