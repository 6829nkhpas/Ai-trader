"""Property-based test for unchanged re-arm detection (opportunity.py, task 4.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 10: Unchanged re-arm after an
invalidation is detected**:

    For any prior (just-invalidated) thesis, a proposed re-arm that is the same
    thesis within the volatility tolerance is detected by ``is_rearm_unchanged``
    (and thus blocked), while a proposed re-arm that changes structure, timeframe,
    direction, or level beyond the tolerance is not flagged.

Validates: Requirements 4.2.

``is_rearm_unchanged(prior, proposed, atr, cfg)`` returns ``True`` only when the
proposed re-arm shares the prior thesis's symbol / timeframe / direction AND its
target level sits within ``REARM_LEVEL_ATR_TOLERANCE_MULT * atr`` of the prior
level. Any change of symbol / timeframe / direction, a level moved beyond the
tolerance, missing / malformed fingerprint fields, or a missing / non-positive /
non-finite ``atr`` FAILS OPEN to ``False`` (never blocks).

The strategy generates a well-formed prior thesis (as raw ``watch_args`` OR as an
already-computed fingerprint, since ``thesis_fingerprint`` is idempotent) and a
positive ``atr``, then derives the proposed re-arm as one of the documented
variants:

  * identical                         -> unchanged  (True)
  * small jitter within 0.5*atr       -> unchanged  (True)
  * level moved beyond 0.5*atr        -> changed     (False)
  * changed symbol                    -> changed     (False)
  * changed timeframe                 -> changed     (False)
  * changed direction                 -> changed     (False)

Separate properties cover the fail-open behaviour on malformed fields and on a
missing / non-positive / non-finite ``atr``. Constants (the tolerance multiple)
are imported from the module rather than hard-coded. The sys.path / import pattern
mirrors the sibling deep-quant-loop opportunity property tests.
"""

import os
import sys

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from opportunity import (  # noqa: E402
    REARM_LEVEL_ATR_TOLERANCE_MULT,
    OpportunityConfig,
    is_rearm_unchanged,
    thesis_fingerprint,
)

# ── Direction spellings, grouped by the canonical side they normalize to. ─────
# above/up/long/buy -> "above"; below/down/short/sell -> "below".
_ABOVE_SPELLINGS = ["above", "up", "long", "buy"]
_BELOW_SPELLINGS = ["below", "down", "short", "sell"]
_DIRECTION_SIDES = {
    "above": _ABOVE_SPELLINGS,
    "below": _BELOW_SPELLINGS,
}

_SYMBOLS = ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK"]
_TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"]


# A representative, non-degenerate configuration. ``cfg`` is accepted only for
# interface symmetry; the tolerance is a module constant, so its exact fields do
# not affect the sameness decision.
def _cfg():
    return OpportunityConfig(
        watch_cap=3,
        session_max_turns=40,
        session_max_wall_secs=1800.0,
        size_factor_a_plus=1.0,
        size_factor_b_continuation=0.6,
        size_factor_scalp=0.3,
        lower_tiers_enabled=True,
        heartbeat_enabled=False,
        heartbeat_cadence_secs=300.0,
        heartbeat_max=6,
        prune_keep_recent_turns=8,
        prune_max_messages=40,
    )


@st.composite
def _prior_thesis(draw):
    """A well-formed prior thesis with a canonical side and a finite level.

    Returns ``(watch_args_or_fingerprint, symbol, timeframe, side, level)`` where
    ``side`` is the canonical 'above'/'below' the chosen spelling normalizes to.
    Emitted as raw ``watch_args`` (``price_level``) OR as an already-computed
    fingerprint (``level``) — ``is_rearm_unchanged`` fingerprints both internally.
    """
    symbol = draw(st.sampled_from(_SYMBOLS))
    timeframe = draw(st.sampled_from(_TIMEFRAMES))
    side = draw(st.sampled_from(["above", "below"]))
    spelling = draw(st.sampled_from(_DIRECTION_SIDES[side]))
    # Levels quantize to 4 dp; keep them comfortably above the tolerance band.
    level = draw(st.floats(min_value=10.0, max_value=100_000.0).map(lambda v: round(v, 4)))

    as_fingerprint = draw(st.booleans())
    if as_fingerprint:
        thesis = {"symbol": symbol, "timeframe": timeframe, "direction": spelling, "level": level}
    else:
        thesis = {"symbol": symbol, "timeframe": timeframe, "direction": spelling, "price_level": level}
    return thesis, symbol, timeframe, side, level


def _other(pool, current):
    return [x for x in pool if x != current]


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 4.2): Unchanged re-arm after an invalidation is detected
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 10: For any prior just-invalidated thesis and any positive ATR, a proposed re-arm sharing symbol/timeframe/direction with its level within REARM_LEVEL_ATR_TOLERANCE_MULT*atr is detected as unchanged (True), whereas a re-arm that changes symbol, timeframe, or direction, or moves the level beyond the tolerance, is not flagged (False).
@settings(max_examples=300, deadline=None)
@given(
    data=_prior_thesis(),
    atr=st.floats(min_value=0.5, max_value=5000.0),
    variant=st.sampled_from(
        [
            "identical",
            "within_tolerance",
            "beyond_tolerance",
            "changed_symbol",
            "changed_timeframe",
            "changed_direction",
        ]
    ),
    frac=st.floats(min_value=0.0, max_value=1.0),
    cross_side=st.booleans(),
)
def test_property_10_unchanged_rearm_detected(data, atr, variant, frac, cross_side):
    """Feature: adaptive-opportunity-engine, Property 10: Unchanged re-arm after
    an invalidation is detected — a same-thesis re-arm within the volatility
    tolerance is flagged True, and any structural / timeframe / direction change
    or a level moved beyond the tolerance is not flagged.

    Validates: Requirements 4.2
    """
    prior, symbol, timeframe, side, level = data
    cfg = _cfg()
    tolerance = REARM_LEVEL_ATR_TOLERANCE_MULT * atr

    if variant == "identical":
        proposed = dict(prior)
        assert is_rearm_unchanged(prior, proposed, atr, cfg) is True

    elif variant == "within_tolerance":
        # Move the level by a fraction of (strictly inside) the tolerance band.
        # Use a margin below 1.0 so quantization to 4 dp cannot push it over.
        delta = frac * tolerance * 0.9
        if cross_side:
            delta = -delta
        new_level = round(level + delta, 4)
        assume(abs(new_level - level) <= tolerance)
        proposed = {"symbol": symbol, "timeframe": timeframe, "direction": side, "level": new_level}
        assert is_rearm_unchanged(prior, proposed, atr, cfg) is True

    elif variant == "beyond_tolerance":
        # Move the level strictly beyond the tolerance band.
        delta = tolerance + 1.0 + frac * tolerance
        if cross_side:
            delta = -delta
        new_level = round(level + delta, 4)
        assume(abs(new_level - level) > tolerance)
        proposed = {"symbol": symbol, "timeframe": timeframe, "direction": side, "level": new_level}
        assert is_rearm_unchanged(prior, proposed, atr, cfg) is False

    elif variant == "changed_symbol":
        new_symbol = _other(_SYMBOLS, symbol)[int(frac * (len(_SYMBOLS) - 1)) % (len(_SYMBOLS) - 1)]
        proposed = {"symbol": new_symbol, "timeframe": timeframe, "direction": side, "level": level}
        assert is_rearm_unchanged(prior, proposed, atr, cfg) is False

    elif variant == "changed_timeframe":
        new_tf = _other(_TIMEFRAMES, timeframe)[int(frac * (len(_TIMEFRAMES) - 1)) % (len(_TIMEFRAMES) - 1)]
        proposed = {"symbol": symbol, "timeframe": new_tf, "direction": side, "level": level}
        assert is_rearm_unchanged(prior, proposed, atr, cfg) is False

    else:  # changed_direction
        new_side = "below" if side == "above" else "above"
        proposed = {"symbol": symbol, "timeframe": timeframe, "direction": new_side, "level": level}
        assert is_rearm_unchanged(prior, proposed, atr, cfg) is False


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (fail-open): malformed fingerprint fields are never flagged
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 10 (fail-open): a proposed or prior thesis missing any of symbol / timeframe / direction / level (or a non-dict input) can never be confidently judged unchanged, so is_rearm_unchanged fails open to False.
@settings(max_examples=200, deadline=None)
@given(
    data=_prior_thesis(),
    atr=st.floats(min_value=0.5, max_value=5000.0),
    drop_field=st.sampled_from(["symbol", "timeframe", "direction", "level", "price_level", "__nondict__"]),
    drop_from_prior=st.booleans(),
)
def test_property_10_malformed_fields_fail_open(data, atr, drop_field, drop_from_prior):
    """Feature: adaptive-opportunity-engine, Property 10 (fail-open): a
    missing/malformed identifying field on either the prior or the proposed
    thesis fails open to False (never blocks).

    Validates: Requirements 4.2
    """
    prior, symbol, timeframe, side, level = data
    cfg = _cfg()

    # An identical, well-formed proposed re-arm (would be True if both intact).
    good = {"symbol": symbol, "timeframe": timeframe, "direction": side, "level": level}

    if drop_field == "__nondict__":
        broken = None
    else:
        broken = dict(prior)
        # Malform whichever level key is present, plus the requested field.
        broken.pop("symbol", None) if drop_field == "symbol" else None
        broken.pop("timeframe", None) if drop_field == "timeframe" else None
        if drop_field == "direction":
            broken["direction"] = "sideways"  # unrecognized spelling -> None
        if drop_field in ("level", "price_level"):
            broken.pop("level", None)
            broken.pop("price_level", None)

    if drop_from_prior:
        assert is_rearm_unchanged(broken, good, atr, cfg) is False
    else:
        assert is_rearm_unchanged(good, broken, atr, cfg) is False


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (fail-open): missing / non-positive / non-finite ATR is not flagged
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 10 (fail-open): without a usable ATR (None, non-positive, or non-finite) the volatility tolerance cannot be derived, so an otherwise-identical re-arm fails open to False.
@settings(max_examples=200, deadline=None)
@given(
    data=_prior_thesis(),
    bad_atr=st.one_of(
        st.none(),
        st.just(0.0),
        st.floats(min_value=-5000.0, max_value=0.0),
        st.sampled_from([float("nan"), float("inf"), float("-inf"), "1.0", True]),
    ),
)
def test_property_10_bad_atr_fails_open(data, bad_atr):
    """Feature: adaptive-opportunity-engine, Property 10 (fail-open): an
    otherwise-identical re-arm is NOT flagged unchanged when the ATR is missing,
    zero, negative, or non-finite (the tolerance cannot be derived).

    Validates: Requirements 4.2
    """
    prior, symbol, timeframe, side, level = data
    cfg = _cfg()
    proposed = {"symbol": symbol, "timeframe": timeframe, "direction": side, "level": level}

    # Sanity: with a usable ATR this identical re-arm IS flagged (guards the test).
    assert is_rearm_unchanged(prior, proposed, 10.0, cfg) is True
    # With an unusable ATR it fails open.
    assert is_rearm_unchanged(prior, proposed, bad_atr, cfg) is False
