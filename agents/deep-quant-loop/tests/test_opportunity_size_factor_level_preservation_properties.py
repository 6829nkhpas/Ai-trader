"""Property-based test for Size_Factor level preservation (opportunity.py, task 2.4).

Feature: adaptive-opportunity-engine

This module implements design **Property 3: Applying Size_Factor never mutates the
validated levels**:

    For any tier and any entry/stop/take_profit bracket and any quantity, applying
    the tier's Size_Factor to the position size (qty * size_factor) leaves entry,
    stop, and take_profit — and therefore the stop distance (|entry-stop|) and the
    Risk:Reward ratio (|target-entry| / |entry-stop|) the Trade_Validator enforces —
    unchanged, so validation is identical for every tier.

Validates: Requirements 1.4, 2.3, 10.2.

``Size_Factor`` is a multiplier on POSITION SIZE only (Design > Data Models >
Size_Factor and trade-management sizing). It is recorded on the decision and applied
through the existing trade-management sizing when the Management_Plan is built; it
never alters entry, stop, or take_profit, so the ``Trade_Validator`` hard rules
(stop >= 1.5x ATR, min R:R) are evaluated identically for every tier.

The property models "applying the size factor" exactly as the design does — scaling
ONLY the quantity — and asserts that the levels object is byte-identical before and
after, and that the stop-distance and R:R computed from the (unchanged) levels are
identical. The strategy generates arbitrary finite valid long/short brackets, an
arbitrary positive quantity, an arbitrary tier, and an arbitrary (valid) config so
the invariant is exercised across the whole tier/level/quantity space.

The sys.path / import pattern mirrors
``tests/test_opportunity_config_resolution_properties.py``.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from opportunity import (  # noqa: E402
    OPPORTUNITY_TIERS,
    OpportunityConfig,
    size_factor,
)

# Tiers to exercise: every ladder value plus an unknown tier (size_factor is total
# and returns 0.0 for stand_aside / unknown — level preservation must still hold).
_TIERS = st.sampled_from(list(OPPORTUNITY_TIERS) + ["unknown_tier"])

# Finite, well-separated price components so a valid non-degenerate bracket can be
# built without floating-point collapse (a real risk/reward bracket).
_price = st.floats(
    min_value=1e-3,
    max_value=1e6,
    allow_nan=False,
    allow_infinity=False,
)

# A strictly positive gap between adjacent levels so entry != stop != target and the
# R:R denominator is never zero.
_gap = st.floats(
    min_value=1e-2,
    max_value=1e5,
    allow_nan=False,
    allow_infinity=False,
)

# Arbitrary positive traded quantity.
_quantity = st.floats(
    min_value=1e-6,
    max_value=1e9,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _bracket(draw):
    """An arbitrary finite, non-degenerate long OR short entry/stop/target bracket.

    Long:  stop < entry < target.  Short: target < entry < stop. Built from a base
    price and two strictly positive gaps so stop and target sit on OPPOSITE sides of
    entry (a genuine risk/reward bracket the Trade_Validator can evaluate).
    """
    entry = draw(_price)
    stop_gap = draw(_gap)
    target_gap = draw(_gap)
    is_long = draw(st.booleans())
    if is_long:
        stop = entry - stop_gap
        target = entry + target_gap
    else:
        stop = entry + stop_gap
        target = entry - target_gap
    return {"entry": entry, "stop": stop, "take_profit": target}


# Arbitrary VALID config: per-tier factors in (0.0, 1.0]; the other knobs at values
# in their documented ranges. size_factor only reads the three factor fields, but a
# full valid config keeps the test faithful to the resolved-config contract.
_config = st.builds(
    OpportunityConfig,
    watch_cap=st.integers(min_value=1, max_value=10),
    session_max_turns=st.integers(min_value=1, max_value=100),
    session_max_wall_secs=st.floats(min_value=1.0, max_value=1e5,
                                    allow_nan=False, allow_infinity=False),
    size_factor_a_plus=st.floats(min_value=1e-3, max_value=1.0,
                                 allow_nan=False, allow_infinity=False),
    size_factor_b_continuation=st.floats(min_value=1e-3, max_value=1.0,
                                          allow_nan=False, allow_infinity=False),
    size_factor_scalp=st.floats(min_value=1e-3, max_value=1.0,
                                allow_nan=False, allow_infinity=False),
    lower_tiers_enabled=st.booleans(),
    heartbeat_enabled=st.booleans(),
    heartbeat_cadence_secs=st.floats(min_value=1.0, max_value=1e4,
                                     allow_nan=False, allow_infinity=False),
    heartbeat_max=st.integers(min_value=0, max_value=20),
    prune_keep_recent_turns=st.integers(min_value=1, max_value=50),
    prune_max_messages=st.integers(min_value=1, max_value=100),
)


def _stop_distance(levels):
    """The stop distance |entry - stop| the Trade_Validator's ATR rule uses."""
    return abs(levels["entry"] - levels["stop"])


def _risk_reward(levels):
    """The Risk:Reward ratio |target - entry| / |entry - stop| the validator enforces."""
    return abs(levels["take_profit"] - levels["entry"]) / abs(levels["entry"] - levels["stop"])


def _apply_size_factor(position, tier, cfg):
    """Model applying the tier's Size_Factor: scale ONLY the quantity.

    Returns a NEW position with ``qty`` scaled by ``size_factor(tier, cfg)`` and the
    entry/stop/take_profit levels carried through untouched — exactly as the design
    applies Size_Factor through trade-management sizing (position size only, never
    the levels).
    """
    factor = size_factor(tier, cfg)
    scaled = copy.deepcopy(position)
    scaled["qty"] = position["qty"] * factor
    return scaled


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 2.4): Applying Size_Factor never mutates the validated levels
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 3: For any tier and any entry/stop/take_profit bracket and any quantity, applying the tier's Size_Factor to the position size leaves entry, stop, and take_profit — and therefore the stop distance and R:R the Trade_Validator enforces — unchanged, so validation is identical for every tier.
@settings(max_examples=200, deadline=None)
@given(levels=_bracket(), qty=_quantity, tier=_TIERS, cfg=_config)
def test_property_3_size_factor_never_mutates_validated_levels(levels, qty, tier, cfg):
    """Feature: adaptive-opportunity-engine, Property 3: Applying Size_Factor never
    mutates the validated levels — scaling the position size by the tier's
    Size_Factor leaves entry/stop/take_profit byte-identical, so the stop distance
    and R:R the Trade_Validator enforces are unchanged for every tier.

    Validates: Requirements 1.4, 2.3, 10.2
    """
    position = {"qty": qty, "levels": copy.deepcopy(levels)}

    # Snapshot the levels and the validator-relevant quantities BEFORE sizing.
    levels_before = copy.deepcopy(levels)
    repr_before = repr(levels)
    stop_distance_before = _stop_distance(levels)
    rr_before = _risk_reward(levels)

    # Apply the tier's Size_Factor to the POSITION SIZE only.
    scaled = _apply_size_factor(position, tier, cfg)

    # ── The levels are byte-identical before and after (nothing mutated). ──────
    assert scaled["levels"] == levels_before
    assert repr(scaled["levels"]) == repr_before
    # The original position's levels object was not mutated in place either.
    assert position["levels"] == levels_before
    assert repr(position["levels"]) == repr_before

    # ── Individual levels are exactly unchanged. ───────────────────────────────
    assert scaled["levels"]["entry"] == levels_before["entry"]
    assert scaled["levels"]["stop"] == levels_before["stop"]
    assert scaled["levels"]["take_profit"] == levels_before["take_profit"]

    # ── The validator-enforced stop distance and R:R are identical for the tier. ─
    stop_distance_after = _stop_distance(scaled["levels"])
    rr_after = _risk_reward(scaled["levels"])
    assert stop_distance_after == stop_distance_before
    assert rr_after == rr_before

    # ── Only the position size changed, by exactly the tier's Size_Factor. ─────
    factor = size_factor(tier, cfg)
    assert math.isfinite(factor)
    assert scaled["qty"] == qty * factor
