"""Property-based test that an Unavailable_Marker carries no fabricated states
(order_flow.py ``classify_order_flow``, task 4.11).

Feature: order-flow-context

This module implements design **Property 15: An Unavailable_Marker never carries
fabricated states**:

    For every path on which ``classify_order_flow`` returns an
    Unavailable_Marker (too few valid candles, an all-invalid candle sequence, or
    an empty sequence — with no usable tick layer), the result flags
    ``unavailable`` with a non-empty ``reason`` and OMITS the categorical
    ``order_flow_state`` / ``alignment`` outputs rather than populating them with
    default / placeholder / otherwise fabricated values, and never reports a
    fabricated neutral ``tick_ofi`` (``0.0``) — the marker carries no Tick_OFI at
    all. It leaves its inputs unmodified and never raises.

Validates: Requirements 4.6, 6.3, 14.6.

``classify_order_flow`` builds an Unavailable_Marker via ``_order_flow_unavailable``
which carries only ``symbol`` / ``timeframe`` / ``unavailable`` / ``reason`` — it
never defaults or fabricates the categorical states (R14.6), and because the
tick layer is honestly unavailable it never substitutes a neutral ``0.0`` for the
Tick_OFI (R6.3, R14.6).

The generator drives ``classify_order_flow`` down the unavailable paths the task
calls out, with NO mocking — the calculator is pure:

  * ``insufficient`` — fewer *valid* (finite-OHLCV) candles than the sufficiency
                       gate (``config.largest_lookback``) requires (R4.1), padded
                       with dirty candles the parser excludes.
  * ``all-invalid``  — every candle carries a non-finite / non-numeric OHLCV
                       field, so the valid count is zero (R4.2 -> R4.1).
  * ``empty``        — an empty candle sequence -> zero valid candles.

The tick layer is supplied as unavailable in every case (``None``, an empty list,
or fewer than ``min_ticks`` usable ticks) so no Tick_OFI is produced — the
calculator must still never fabricate a neutral value (R6.3, R14.6).

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

# The categorical state fields an Unavailable_Marker must NEVER carry
# (Requirements 4.6, 14.6): they are omitted, never defaulted or fabricated.
_FABRICATABLE_STATE_FIELDS = ("order_flow_state", "alignment")

# Resolve config once (identical on the tool and backtest paths). The candle
# sufficiency gate the property exercises is ``config.largest_lookback``.
_CONFIG = resolve_order_flow_config()
_REQUIRED = _CONFIG.largest_lookback

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

_finite_price = st.floats(
    min_value=0.5, max_value=10_000.0, allow_nan=False, allow_infinity=False
)
_finite_volume = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# the parser rejects, so the carrying candle never adds to the valid count.
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
    excludes it (it never contributes to the valid count, R4.2)."""
    candle = draw(_clean_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close", "volume"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _insufficient_candles(draw):
    """A candle sequence whose *valid* count is strictly below the gate (R4.1).

    ``n_valid`` clean candles (``0 <= n_valid < required``) padded with an
    arbitrary number of dirty candles (excluded), then shuffled so order carries
    no information.
    """
    n_valid = draw(st.integers(min_value=0, max_value=_REQUIRED - 1))
    candles = [draw(_clean_candle()) for _ in range(n_valid)]
    n_dirty = draw(st.integers(min_value=0, max_value=10))
    candles += [draw(_dirty_candle()) for _ in range(n_dirty)]
    return list(draw(st.permutations(candles)))


@st.composite
def _all_invalid_candles(draw):
    """A non-empty candle sequence in which every candle is dirty (R4.2 -> R4.1)."""
    n = draw(st.integers(min_value=1, max_value=15))
    return [draw(_dirty_candle()) for _ in range(n)]


# An empty candle sequence -> zero valid candles.
_empty_candles = st.just([])

_unavailable_candles = st.one_of(
    _insufficient_candles(),
    _all_invalid_candles(),
    _empty_candles,
)


@st.composite
def _too_few_ticks(draw):
    """A tick sequence with fewer than ``min_ticks`` usable ticks (tick layer
    unavailable) — exercises that even a present-but-degenerate tick stream never
    fabricates a neutral Tick_OFI (R6.3, R14.6)."""
    n = draw(st.integers(min_value=0, max_value=max(0, _CONFIG.min_ticks - 1)))
    ticks = []
    vol = draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False,
                         allow_infinity=False))
    for _ in range(n):
        price = draw(_finite_price)
        vol += draw(st.floats(min_value=0.0, max_value=1e4, allow_nan=False,
                              allow_infinity=False))
        ticks.append({"last_price": price, "volume": vol,
                      "best_bid": price - 0.5, "best_ask": price + 0.5})
    return ticks


# The tick layer is unavailable in every scenario: missing, empty, or too few.
_no_ticks = st.one_of(st.none(), st.just([]), _too_few_ticks())

# A proposed trade direction (or its absence): the marker must arise — with no
# fabricated state — regardless of the proposed direction.
_proposed_direction = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", "weird"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 15: An Unavailable_Marker never carries fabricated states
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 15: An Unavailable_Marker never carries fabricated states
@settings(max_examples=200, deadline=None)
@given(
    candles=_unavailable_candles,
    ticks=_no_ticks,
    proposed_direction=_proposed_direction,
)
def test_property_15_unavailable_marker_carries_no_fabricated_states(
    candles, ticks, proposed_direction
):
    """Validates: Requirements 4.6, 6.3, 14.6

    For every path that drives ``classify_order_flow`` to an Unavailable_Marker
    (too few valid candles, an all-invalid candle sequence, or an empty sequence,
    with no usable tick layer), the result flags ``unavailable`` with a non-empty
    ``reason``, OMITS ``order_flow_state`` / ``alignment`` rather than fabricating
    them, and never reports a fabricated neutral ``tick_ofi`` (``0.0``). It leaves
    its inputs unmodified and never raises.
    """
    # Snapshot the inputs to confirm purity (no mutation while classifying).
    candles_snapshot = copy.deepcopy(candles)
    ticks_snapshot = copy.deepcopy(ticks)

    # Must never raise (R4): the call itself is part of the assertion.
    result = classify_order_flow(
        candles, ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )

    # The calculator must always return a dict.
    assert isinstance(result, dict), f"calculator result is not a dict: {result!r}"

    # Every scenario here is engineered to be unavailable.
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker, got: {result!r}"
    )

    # An honest marker must cite a (non-empty) reason for being unavailable.
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker is missing a non-empty reason: {result!r}"
    )

    # Core assertion 1: the marker must OMIT the categorical state outputs — no
    # default, placeholder, or otherwise fabricated order_flow_state / alignment
    # (Requirements 4.6, 14.6).
    for field in _FABRICATABLE_STATE_FIELDS:
        assert field not in result, (
            f"Unavailable_Marker fabricated '{field}'={result.get(field)!r} "
            f"(must be omitted): {result!r}"
        )

    # Core assertion 2: no fabricated neutral Tick_OFI (Requirements 6.3, 14.6).
    # The marker carries no Tick_OFI at all; if a tick_ofi key were ever present
    # on a marker it must be the honest ``None``, never a fabricated ``0.0``.
    tick_ofi = result.get("tick_ofi")
    assert tick_ofi is None, (
        f"Unavailable_Marker carries a fabricated tick_ofi={tick_ofi!r} "
        f"(must be absent / None): {result!r}"
    )
    assert tick_ofi != 0.0, (
        f"Unavailable_Marker fabricated a neutral tick_ofi 0.0: {result!r}"
    )

    # The inputs are left unmodified (purity underpins the honest marker, R1.7/R2.5).
    assert candles == candles_snapshot, "classify_order_flow mutated its candle input"
    assert ticks == ticks_snapshot, "classify_order_flow mutated its tick input"
