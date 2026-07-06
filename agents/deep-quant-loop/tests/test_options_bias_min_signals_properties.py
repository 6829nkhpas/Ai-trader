"""Property-based test for the too-few-signals neutral rule (options_bias.py, task 2.6).

Feature: options-agent-integration

This module implements design **Property 5: Too few signals yields a neutral
bias**:

    When FEWER than ``MIN_SIGNALS_FOR_BIAS`` (= 2) signals cast a directional
    vote, ``classify_options_bias`` must NOT fabricate a directional bias — its
    ``options_bias_state`` is ALWAYS ``neutral``. This covers both the
    zero-contributing case (no signal votes) and the one-contributing case
    (exactly one signal votes, all others ``null`` / structurally absent).

Validates: Requirements 1.5.

The classifier nets six per-signal votes — ``pcr_oi``, the aggregate
``oi_buildup`` (call/put), ``max_pain`` vs ``spot``, the nearest ``oi_walls`` vs
``spot``, ``iv_skew.put_minus_call``, and ``futures_basis``. To exercise the
too-few-signals rule we generate analytics in which AT MOST ONE of these
channels carries a value that can cast a directional vote, while every other
channel is ``null`` or absent (so it contributes no vote). The result must be
``neutral`` regardless of how strongly that single channel leans.

The sys.path / import pattern mirrors the sibling
``test_options_bias_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options_bias.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options_bias import (  # noqa: E402
    MIN_SIGNALS_FOR_BIAS,
    classify_options_bias,
    resolve_options_bias_config,
)

# Resolve config once (deterministic, env-independent for the default path).
_CONFIG = resolve_options_bias_config()

# Sanity guard: the property only makes sense while the threshold is 2 — if it is
# ever raised, the "at most one contributing signal" construction below still
# holds (one < any threshold >= 2), but we document the assumption explicitly.
assert MIN_SIGNALS_FOR_BIAS >= 2


# ── Per-channel generators producing a GUARANTEED directional vote ────────────
# Each generator yields a value that, placed in its analytics channel with every
# other channel null/absent, makes exactly that one channel cast a +1/-1 vote.

# PCR(OI): >= bullish cutoff => bullish; <= bearish cutoff => bearish. The two
# disjoint ranges below straddle the (bearish, bullish) dead band, so every draw
# casts a directional vote.
_pcr_directional = st.one_of(
    st.floats(min_value=_CONFIG.pcr_bullish_cutoff, max_value=_CONFIG.pcr_bullish_cutoff + 10.0,
              allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=_CONFIG.pcr_bearish_cutoff,
              allow_nan=False, allow_infinity=False),
)

# Aggregate OI buildup: each (side, label) below belongs to EXACTLY ONE of the
# bullish / bearish membership sets, with the opposite side held ``neutral`` (a
# non-member that contributes nothing), so the netted aggregate vote is ±1.
_oi_buildup_directional = st.sampled_from([
    {"put": "long_buildup", "call": "neutral"},      # +1 (put longs accumulating)
    {"put": "short_covering", "call": "neutral"},    # +1 (put shorts covering)
    {"call": "long_unwinding", "put": "neutral"},    # +1 (call longs exiting overhead)
    {"call": "long_buildup", "put": "neutral"},      # -1 (call longs / resistance)
    {"put": "long_unwinding", "call": "neutral"},    # -1 (put longs exiting, support erodes)
    {"put": "short_buildup", "call": "neutral"},     # -1 (fresh put writing below)
])

# IV skew on put_minus_call vs the threshold (default 0.0): > threshold => bearish,
# < -threshold => bullish. The two disjoint ranges below avoid the dead band.
_iv_skew_directional = st.builds(
    lambda v: {"put_minus_call": v},
    st.one_of(
        st.floats(min_value=_CONFIG.iv_skew_threshold + 0.001,
                  max_value=_CONFIG.iv_skew_threshold + 100.0,
                  allow_nan=False, allow_infinity=False),
        st.floats(min_value=-(_CONFIG.iv_skew_threshold + 100.0),
                  max_value=-(_CONFIG.iv_skew_threshold + 0.001),
                  allow_nan=False, allow_infinity=False),
    ),
)

# Futures basis vs the threshold (default 0.0): > threshold => bullish,
# < -threshold => bearish. Two disjoint ranges straddling the dead band.
_futures_basis_directional = st.one_of(
    st.floats(min_value=_CONFIG.futures_basis_threshold + 0.001,
              max_value=_CONFIG.futures_basis_threshold + 100.0,
              allow_nan=False, allow_infinity=False),
    st.floats(min_value=-(_CONFIG.futures_basis_threshold + 100.0),
              max_value=-(_CONFIG.futures_basis_threshold + 0.001),
              allow_nan=False, allow_infinity=False),
)

# A strictly-positive spot used by the max_pain / oi_walls channels.
_spot = st.floats(min_value=100.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)

# A proposed direction spanning recognized / unrecognized / absent / non-string —
# the alignment must not influence the (neutral) bias state.
_weird_direction = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", " hold ", "FLAT", ""]),
    st.text(max_size=5),
    st.integers(),
)


def _all_null_base():
    """An analytics skeleton in which every signal channel casts no vote."""
    return {
        "pcr_oi": None,
        "oi_buildup": None,
        "max_pain": None,
        "spot": None,
        "oi_walls": None,
        "iv_skew": None,
        "futures_basis": None,
    }


@st.composite
def _maybe_drop_null_keys(draw, analytics):
    """Randomly drop a subset of the keys whose value is ``None``.

    Exercises the "structurally absent" path (a missing key) alongside the
    "present but null" path — both must contribute no vote. Keys carrying a real
    value (the single active channel, plus any spot it depends on) are never
    dropped.
    """
    droppable = [k for k, v in analytics.items() if v is None]
    to_drop = draw(st.sets(st.sampled_from(droppable), max_size=len(droppable))) if droppable else set()
    return {k: v for k, v in analytics.items() if k not in to_drop}


@st.composite
def _at_most_one_contributing(draw):
    """Build analytics in which AT MOST ONE channel casts a directional vote.

    Picks a single "active" channel (or ``none`` for the zero-contributing case),
    populates it with a guaranteed-directional value, leaves every other channel
    null/absent, and randomly drops some of the null keys to also cover the
    structurally-absent path.
    """
    channel = draw(st.sampled_from([
        "none", "pcr", "oi_buildup", "max_pain", "oi_walls", "iv_skew", "futures_basis",
    ]))
    a = _all_null_base()

    if channel == "pcr":
        a["pcr_oi"] = draw(_pcr_directional)
    elif channel == "oi_buildup":
        a["oi_buildup"] = draw(_oi_buildup_directional)
    elif channel == "max_pain":
        spot = draw(_spot)
        a["spot"] = spot
        # Place max pain well outside the proximity band so it casts a vote.
        factor = draw(st.sampled_from([1.05, 0.95]))
        a["max_pain"] = spot * factor
    elif channel == "oi_walls":
        spot = draw(_spot)
        a["spot"] = spot
        # Exactly one wall near spot (the other absent) => a directional vote.
        if draw(st.booleans()):
            a["oi_walls"] = {"support": spot, "resistance": None}   # +1
        else:
            a["oi_walls"] = {"support": None, "resistance": spot}   # -1
    elif channel == "iv_skew":
        a["iv_skew"] = draw(_iv_skew_directional)
    elif channel == "futures_basis":
        a["futures_basis"] = draw(_futures_basis_directional)
    # channel == "none": leave everything null (zero contributing signals)

    return draw(_maybe_drop_null_keys(a))


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (task 2.6): Too few signals yields a neutral bias
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 5: Too few signals yields a neutral bias
@settings(max_examples=200, deadline=None)
@given(analytics=_at_most_one_contributing(), proposed_direction=_weird_direction)
def test_property_5_too_few_signals_yields_neutral(analytics, proposed_direction):
    """Feature: options-agent-integration, Property 5: Too few signals yields a
    neutral bias.

    When at most one signal can cast a directional vote (fewer than
    ``MIN_SIGNALS_FOR_BIAS``), ``classify_options_bias`` must return a
    ``neutral`` ``options_bias_state`` — never a fabricated ``bullish`` /
    ``bearish`` bias — covering both the zero-contributing and one-contributing
    cases.

    Validates: Requirements 1.5
    """
    label = classify_options_bias(analytics, _CONFIG, proposed_direction=proposed_direction)

    assert isinstance(label, dict), f"expected a label dict, got {label!r}"
    state = label.get("options_bias_state")
    assert state == "neutral", (
        f"too-few-signals analytics {analytics!r} should yield a neutral bias, "
        f"got {state!r}"
    )


# Feature: options-agent-integration, Property 5: Too few signals yields a neutral bias
@settings(max_examples=100, deadline=None)
@given(proposed_direction=_weird_direction)
def test_property_5_zero_contributing_signals_is_neutral(proposed_direction):
    """Feature: options-agent-integration, Property 5: Too few signals yields a
    neutral bias — the explicit zero-contributing edge.

    An analytics result in which NO signal casts a directional vote (every
    channel null / absent) must classify as ``neutral``.

    Validates: Requirements 1.5
    """
    label = classify_options_bias(_all_null_base(), _CONFIG, proposed_direction=proposed_direction)
    assert label.get("options_bias_state") == "neutral", (
        f"zero-contributing analytics must be neutral, got {label.get('options_bias_state')!r}"
    )
