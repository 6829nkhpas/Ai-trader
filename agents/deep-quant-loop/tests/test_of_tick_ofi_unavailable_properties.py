"""Property-based test for insufficient/degenerate ticks yielding an unavailable
Tick_OFI, never a fabricated neutral (order_flow.py, task 3.4).

Feature: order-flow-context

This module implements design **Property 8: Insufficient or degenerate ticks
yield an unavailable Tick_OFI, never a fabricated neutral**:

    ``compute_tick_ofi(ticks, config)`` returns ``None`` (unavailable) — and
    NEVER a fabricated neutral ``0.0`` — whenever the tick sequence cannot
    produce a trustworthy imbalance, namely when it:

      * is empty (R2.3),
      * has fewer than ``config.min_ticks`` usable ticks (R2.3),
      * yields zero total signed volume because the day's cumulative volume is
        flat or only declining — no positive cumulative-volume delta exists
        (R2.3), or
      * is full of non-finite / non-numeric required fields so that every tick
        is excluded, leaving zero usable ticks (R4.2, R2.3).

    In every such degenerate case the result is the honest ``None`` marker, not
    a fabricated ``0.0`` (R14.6).

Validates: Requirements 2.3, 14.6.

Ticks are generated as dict-like records with ``last_price`` / ``volume`` /
``best_bid`` / ``best_ask`` keys, exactly as ``tools._read_live_ticks`` produces
them and as ``order_flow.compute_tick_ofi`` reads them via ``tick.get(...)``.
The config comes from ``resolve_order_flow_config()`` per the task. The
sys.path / import pattern mirrors the sibling ``test_of_*_properties.py``
modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import compute_tick_ofi, resolve_order_flow_config  # noqa: E402

# Resolve once (env is read once, deterministically) so the generators can be
# bounded by the configured minimum trustworthy tick count. The test itself also
# calls resolve_order_flow_config() to pass config into compute_tick_ofi.
_CONFIG = resolve_order_flow_config()
_MIN_TICKS = _CONFIG.min_ticks

_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)

# Non-finite / non-numeric junk values for the required tick fields (R4.2). Any
# of these in ``last_price`` or ``volume`` excludes the whole tick.
_BAD_FIELD = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", "", None, True, False, [], {}]
)


def _valid_tick(draw, vol):
    """One well-formed dict-like tick with a given cumulative volume."""
    return {
        "last_price": draw(_PRICE),
        "volume": vol,
        "best_bid": draw(_PRICE),
        "best_ask": draw(_PRICE),
    }


# Category 1: an empty tick sequence (R2.3).
_empty_ticks = st.just([])


@st.composite
def _too_few_ticks(draw):
    """Category 2: fewer than ``min_ticks`` usable (well-formed) ticks (R2.3).

    Volumes are strictly increasing so the ticks would each be usable — the only
    reason the imbalance is unavailable is that there are too few of them.
    """
    n = draw(st.integers(min_value=0, max_value=max(0, _MIN_TICKS - 1)))
    ticks = []
    vol = draw(_VOLUME)
    for _ in range(n):
        ticks.append(_valid_tick(draw, vol))
        vol += draw(st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    return ticks


@st.composite
def _no_positive_delta_ticks(draw):
    """Category 3: >= min_ticks ticks whose cumulative volume never increases.

    With a flat or monotonically declining cumulative volume, every consecutive
    delta is <= 0 and is skipped, so the total signed volume stays zero and the
    imbalance is unavailable (R2.3) — never a fabricated 0.0.
    """
    n = draw(st.integers(min_value=_MIN_TICKS, max_value=_MIN_TICKS + 40))
    ticks = []
    vol = draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False))
    for _ in range(n):
        ticks.append(_valid_tick(draw, vol))
        # Non-increasing: subtract a non-negative amount (flat when 0).
        vol -= draw(st.floats(min_value=0.0, max_value=vol if vol > 0 else 0.0,
                              allow_nan=False, allow_infinity=False))
    return ticks


@st.composite
def _nonfinite_field_ticks(draw):
    """Category 4: ticks whose required fields are non-finite / non-numeric.

    Every tick carries junk in ``last_price`` and/or ``volume`` so each is
    excluded entirely, leaving zero usable ticks (R4.2) and an unavailable
    Tick_OFI (R2.3).
    """
    n = draw(st.integers(min_value=1, max_value=_MIN_TICKS + 20))
    ticks = []
    for _ in range(n):
        ticks.append(
            {
                "last_price": draw(_BAD_FIELD),
                "volume": draw(_BAD_FIELD),
                "best_bid": draw(_PRICE),
                "best_ask": draw(_PRICE),
            }
        )
    return ticks


_DEGENERATE_TICKS = st.one_of(
    _empty_ticks,
    _too_few_ticks(),
    _no_positive_delta_ticks(),
    _nonfinite_field_ticks(),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (task 3.4): Insufficient or degenerate ticks yield an unavailable
# Tick_OFI, never a fabricated neutral
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 8: Insufficient or degenerate ticks yield an unavailable Tick_OFI, never a fabricated neutral
@settings(max_examples=200, deadline=None)
@given(ticks=_DEGENERATE_TICKS)
def test_property_8_degenerate_ticks_yield_unavailable_not_fabricated_neutral(ticks):
    """Feature: order-flow-context, Property 8: Insufficient or degenerate ticks
    yield an unavailable Tick_OFI, never a fabricated neutral.

    For empty, too-few, no-positive-delta, and all-non-finite tick sequences,
    ``compute_tick_ofi`` returns ``None`` (unavailable) and never a fabricated
    ``0.0``.

    Validates: Requirements 2.3, 14.6
    """
    config = resolve_order_flow_config()
    result = compute_tick_ofi(ticks, config)

    # Honest unavailable marker, never a fabricated neutral value (R2.3, R14.6).
    assert result is None, f"expected None (unavailable), got {result!r}"
    # Belt-and-suspenders: explicitly assert it is not a fabricated 0.0.
    assert result != 0.0
