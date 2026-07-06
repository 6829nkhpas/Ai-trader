"""Property-based test for insufficient-candle unavailability (order_flow.py, task 4.10).

Feature: order-flow-context

This module implements design **Property 14: Insufficient candles yield an
Unavailable_Marker with counts**:

    For any candle sequence whose count of *valid* (finite-OHLCV) candles is
    strictly fewer than ``config.largest_lookback`` (and with no usable tick
    layer), ``classify_order_flow`` returns an Unavailable_Marker whose reason
    identifies the insufficient-data condition and includes BOTH the count of
    valid candles received and the configured minimum required, omits the
    Order_Flow_State / Alignment rather than fabricating them, leaves its inputs
    unmodified, and never raises.

Validates: Requirements 4.1.

``classify_order_flow`` gates on ``required = config.largest_lookback`` (the max
of the proxy ``lookback`` and the ``min_candles`` floor), counting only candles
whose OHLCV fields are finite numbers (non-finite / non-numeric candles are
excluded — Requirement 4.2). The strategy below resolves the real configuration
via ``resolve_order_flow_config()``, then builds a candle sequence whose *valid*
count is strictly below that gate by:

  * placing ``n_valid`` clean candles (``0 <= n_valid < required``), and
  * padding with dirty (non-finite / non-numeric) candles that the parser
    excludes, so they never add to the valid count.

The tick layer is supplied as ``None`` or an empty list so it is unavailable
too; the insufficient-candle gate is the first gate in ``classify_order_flow``
and fires regardless, so the result is always the insufficient-data marker.

The sys.path / import pattern mirrors the sibling ``test_of_*_properties.py``
modules: the service directory (one level up) is prepended to ``sys.path`` so
``order_flow`` is importable when pytest runs from anywhere.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import classify_order_flow, resolve_order_flow_config  # noqa: E402

# Resolve config once (identical on the tool and backtest paths). The gate the
# property exercises is ``config.largest_lookback``.
_CONFIG = resolve_order_flow_config()
_REQUIRED = _CONFIG.largest_lookback

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite price values in a sane band so clean candles are accepted by the parser
# (each OHLCV field a finite number) and therefore count as valid candles.
_finite_price = st.floats(
    min_value=0.5, max_value=10_000.0, allow_nan=False, allow_infinity=False
)
_finite_volume = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that the parser rejects, so the carrying candle never adds to the valid count.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True, [], {}]
)


@st.composite
def _clean_candle(draw):
    """A well-formed dict-like OHLCV candle with finite fields and ``high >= low``."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    low = min(a, b)
    high = max(a, b)
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(_finite_volume),
    }


@st.composite
def _dirty_candle(draw):
    """A candle dict carrying one non-finite/non-numeric OHLCV field so the parser
    excludes it (it never contributes to the valid count)."""
    candle = draw(_clean_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close", "volume"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _insufficient_candles(draw):
    """Draw a candle sequence whose *valid* count is strictly below the gate.

    Returns ``(candles, n_valid)`` where ``n_valid`` is drawn in
    ``[0, required - 1]`` clean candles, padded with an arbitrary number of dirty
    candles (which are excluded), then shuffled so order carries no information.
    """
    n_valid = draw(st.integers(min_value=0, max_value=_REQUIRED - 1))
    candles = [draw(_clean_candle()) for _ in range(n_valid)]
    n_dirty = draw(st.integers(min_value=0, max_value=10))
    candles += [draw(_dirty_candle()) for _ in range(n_dirty)]
    candles = draw(st.permutations(candles))
    return list(candles), n_valid


# The tick layer is supplied as unavailable (``None`` or an empty list).
_no_ticks = st.sampled_from([None, []])

# A proposed trade direction (or its absence): the insufficient-data marker must
# arise regardless of the proposed direction.
_proposed_direction = st.sampled_from(["BUY", "SELL", "HOLD", "", None])


# ─────────────────────────────────────────────────────────────────────────────
# Property 14: Insufficient candles yield an Unavailable_Marker with counts
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 14: Insufficient candles yield an Unavailable_Marker with counts
@settings(max_examples=200, deadline=None)
@given(
    data=_insufficient_candles(),
    ticks=_no_ticks,
    proposed_direction=_proposed_direction,
)
def test_property_14_insufficient_candles_unavailable(data, ticks, proposed_direction):
    """Validates: Requirements 4.1

    For any candle sequence whose count of valid candles is strictly fewer than
    ``config.largest_lookback`` (with no usable tick layer),
    ``classify_order_flow`` returns an Unavailable_Marker whose reason identifies
    the insufficient-data condition and includes BOTH the received valid-candle
    count and the configured minimum required, omits the Order_Flow_State /
    Alignment, leaves its inputs unmodified, and never raises.
    """
    candles, n_valid = data

    # Snapshot the inputs to confirm purity (no mutation while classifying).
    candles_snapshot = copy.deepcopy(candles)
    ticks_snapshot = copy.deepcopy(ticks)

    # Must never raise (Requirement 4.1): the call itself is part of the assertion.
    result = classify_order_flow(
        candles, ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )

    # The result is an honest Unavailable_Marker.
    assert isinstance(result, dict)
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker for {n_valid} valid candles "
        f"(< required {_REQUIRED}); got {result!r}"
    )

    # The reason identifies the insufficient-data condition and includes BOTH the
    # count of valid candles received and the configured minimum required (R4.1).
    reason = result.get("reason", "")
    assert isinstance(reason, str)
    assert "insufficient" in reason.lower(), (
        f"reason should identify the insufficient-data condition; got {reason!r}"
    )
    assert str(n_valid) in reason, (
        f"reason should include the received valid-candle count {n_valid}; "
        f"got {reason!r}"
    )
    assert str(_REQUIRED) in reason, (
        f"reason should include the required count {_REQUIRED}; got {reason!r}"
    )

    # An Unavailable_Marker omits the classification fields rather than
    # fabricating them (Requirement 4.1 / Unavailable_Marker schema).
    assert "order_flow_state" not in result
    assert "alignment" not in result

    # The inputs are left unmodified (purity underpins the honest marker).
    assert candles == candles_snapshot, (
        "classify_order_flow mutated its candle input"
    )
    assert ticks == ticks_snapshot, (
        "classify_order_flow mutated its tick input"
    )
