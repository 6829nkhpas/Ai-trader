"""Property-based test for the normalized signed-volume imbalance (order_flow.py, task 3.2).

Feature: order-flow-context

This module implements design **Property 6: Tick_OFI is the normalized
signed-volume imbalance within bounds**:

    For any generated tick sequence, when ``compute_tick_ofi(ticks, config)``
    returns a non-``None`` value that value is a finite number lying within
    ``[-1.0, 1.0]`` and equals the normalized net-signed-volume divided by the
    total-signed-volume computed independently per the tick rule:

      * per-tick traded size is the POSITIVE delta of the day's cumulative
        ``volume`` between consecutive usable ticks (non-positive deltas —
        session/counter resets — are skipped),
      * each delta is signed by the tick rule (uptick => +1 buy, downtick => -1
        sell, zero-tick inherits the previous sign, seeded at +1),
      * the sign is refined by quote location (Lee-Ready style) when a usable
        best bid/ask is present (``bid > 0`` and ``ask > 0`` and ``ask >= bid``):
        a trade above the bid/ask mid => +1, below => -1, at the mid => the tick
        sign,
      * OFI = net signed volume / total signed volume, clamped to [-1.0, 1.0].

Validates: Requirements 2.1, 2.4.

Ticks are generated as dict-like records with ``last_price`` / ``volume``
(cumulative) / ``best_bid`` / ``best_ask`` keys, exactly as
``tools._read_live_ticks`` produces them and as ``order_flow.py`` reads them via
``tick.get(...)``. Sequences carry at least ``config.min_ticks`` usable ticks so
that a value can be produced. The sys.path / import pattern mirrors the sibling
``test_of_*_properties.py`` modules.
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

from order_flow import (  # noqa: E402
    compute_tick_ofi,
    resolve_order_flow_config,
)

# Resolve config once (same on tool and backtest paths). Drives the minimum
# usable-tick count so generated sequences are large enough to produce a value.
_CONFIG = resolve_order_flow_config()
_MIN_TICKS = _CONFIG.min_ticks

_OFI_TOTAL_VOLUME_EPSILON = 1e-6

# Finite price magnitudes spanning ordinary values.
_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)
# Non-negative cumulative-volume increments; 0 produces a skipped (non-positive
# delta) tick so the session-reset/skip path is exercised too.
_VOLUME_STEP = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
# A bid/ask spread; a small non-negative number keeps ask >= bid.
_SPREAD = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)


@st.composite
def _tick(draw):
    """One dict-like tick with ``last_price`` / ``volume`` / ``best_bid`` / ``best_ask``.

    ``volume`` is filled in by the sequence generator (it must be cumulative).
    The quote is either present (``bid > 0`` and ``ask >= bid``) so the Lee-Ready
    refinement engages, or absent (``0.0``) so the refinement is skipped — both
    paths are covered.
    """
    last_price = draw(_PRICE)
    if draw(st.booleans()):
        bid = draw(st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False))
        ask = bid + draw(_SPREAD)
    else:
        bid = 0.0
        ask = 0.0
    return {"last_price": last_price, "best_bid": bid, "best_ask": ask, "volume": None}


@st.composite
def _tick_sequence(draw):
    """A chronological (oldest-first) sequence of >= ``_MIN_TICKS`` usable ticks.

    The cumulative ``volume`` is built as a running, non-decreasing sum of
    non-negative increments so the sequence is a realistic day's cumulative
    volume; zero increments yield skipped ticks (non-positive delta).
    """
    n = draw(st.integers(min_value=_MIN_TICKS, max_value=_MIN_TICKS + 40))
    ticks = draw(st.lists(_tick(), min_size=n, max_size=n))
    cumulative = draw(_PRICE)  # arbitrary positive starting cumulative volume
    for tick in ticks:
        cumulative += draw(_VOLUME_STEP)
        tick["volume"] = cumulative
    return ticks


def _clamp(value, low, high):
    return low if value < low else high if value > high else value


def _expected_ofi(ticks, config):
    """Independent recomputation of the Tick_OFI per the tick rule (R2.1, R2.4).

    Mirrors the algorithm in ``order_flow.compute_tick_ofi`` for clean dict
    ticks: positive cumulative-volume deltas signed by the tick rule, refined by
    quote location, normalized by total signed volume and clamped to [-1, 1].
    Returns ``None`` when fewer than ``min_ticks`` usable ticks or zero total
    signed volume.
    """
    if len(ticks) < config.min_ticks:
        return None
    signed_vol = 0.0
    total_vol = 0.0
    last_sign = 1.0
    for i in range(1, len(ticks)):
        prev = ticks[i - 1]
        cur = ticks[i]
        dv = cur["volume"] - prev["volume"]
        if dv <= 0.0:
            continue
        dp = cur["last_price"] - prev["last_price"]
        if dp > 0.0:
            tick_sign = 1.0
        elif dp < 0.0:
            tick_sign = -1.0
        else:
            tick_sign = last_sign
        last_sign = tick_sign

        bid = cur["best_bid"]
        ask = cur["best_ask"]
        if bid > 0.0 and ask > 0.0 and ask >= bid:
            mid = (bid + ask) / 2.0
            ltp = cur["last_price"]
            if ltp > mid:
                refined_sign = 1.0
            elif ltp < mid:
                refined_sign = -1.0
            else:
                refined_sign = tick_sign
        else:
            refined_sign = tick_sign

        signed_vol += refined_sign * dv
        total_vol += dv

    if total_vol < _OFI_TOTAL_VOLUME_EPSILON:
        return None
    return _clamp(signed_vol / total_vol, -1.0, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (task 3.2): Tick_OFI is the normalized signed-volume imbalance within bounds
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 6: Tick_OFI is the normalized signed-volume imbalance within bounds
@settings(max_examples=200, deadline=None)
@given(ticks=_tick_sequence())
def test_property_6_tick_ofi_normalized_imbalance_within_bounds(ticks):
    """Feature: order-flow-context, Property 6: Tick_OFI is the normalized
    signed-volume imbalance within bounds.

    When ``compute_tick_ofi`` returns a non-``None`` value it is finite, within
    ``[-1.0, 1.0]``, and equals the normalized net-signed-volume /
    total-signed-volume computed independently per the tick rule.

    Validates: Requirements 2.1, 2.4
    """
    ofi = compute_tick_ofi(ticks, _CONFIG)

    if ofi is None:
        # Unavailable is permitted (e.g. zero total signed volume); the bounds /
        # equality claims only constrain the non-None case.
        return

    # Finite and within bounds (R2.4).
    assert isinstance(ofi, float), f"Tick_OFI is not a float: {ofi!r}"
    assert math.isfinite(ofi), f"Tick_OFI is not finite: {ofi!r}"
    assert -1.0 <= ofi <= 1.0, f"Tick_OFI {ofi!r} outside [-1.0, 1.0]"

    # Equals the independently recomputed normalized imbalance (R2.1).
    expected = _expected_ofi(ticks, _CONFIG)
    assert expected is not None, "independent recompute returned None while compute_tick_ofi did not"
    assert math.isclose(ofi, expected, rel_tol=1e-9, abs_tol=1e-12), (
        f"Tick_OFI {ofi!r} != independently recomputed {expected!r}"
    )
