"""Property-based test for non-finite candle exclusion (trade_manager.py, task 3.11).

Feature: trade-management

This module implements design **Property 10: Non-finite candles are excluded**:

    For any candle sequence, inserting candles with non-finite (NaN / inf) or
    non-numeric (None / str / bool) OHLCV fields ANYWHERE in the sequence yields
    the SAME ``SimulationResult`` as simulating the sequence with those candles
    removed, and never raises.

Validates: Requirements 3.2.

Strategy: draw a ``ManagementPlan`` and a base list of well-formed ("clean")
candles, then draw a list of "junk" candles — each carrying at least one
non-finite / non-numeric OHLCV field (``nan`` / ``inf`` / ``-inf`` / ``None`` /
a string / a bool), or a non-numeric timestamp — that the simulator must
exclude. We then build a "polluted" sequence by interleaving the junk candles
anywhere among the clean ones and assert that

    simulate_plan(plan, clean)  ==  simulate_plan(plan, polluted)

(full ``SimulationResult`` equality — status, realized_r, the ``fills`` tuple,
residual_fraction, breakeven_moved_at, trailed), and that neither call raises.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_order_invariance_properties.py`` and
``tests/test_tm_sim_open_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import (  # noqa: E402
    BreakevenTrigger,
    ManagementPlan,
    ScaleOutLeg,
    TrailingStop,
    resolve_trade_manager_config,
    simulate_plan,
)

# A single resolved configuration, built from the documented defaults via the
# canonical resolver (the simulator is a pure function of plan + candles + config;
# the config value is held fixed so the property isolates candle CONTENT).
_CONFIG = resolve_trade_manager_config()


# ── Plan strategies ───────────────────────────────────────────────────────────
# Finite, well-behaved floats. Prices overlap the candle band below so that
# targets / stops are actually reached on many examples (exercising resolved,
# open, and invalid outcomes), not just trivially open ones.
_price = st.floats(
    min_value=0.0,
    max_value=200.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

_fraction = st.floats(
    min_value=1e-3,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

_leg = st.builds(ScaleOutLeg, target=_price, fraction=_fraction)
_legs = st.lists(_leg, min_size=1, max_size=4).map(tuple)

_breakeven = st.one_of(
    st.none(),
    st.builds(BreakevenTrigger, price=_price, r_multiple=st.none()),
    st.builds(
        BreakevenTrigger,
        price=st.none(),
        r_multiple=st.floats(
            min_value=1e-3, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

_trailing = st.one_of(
    st.none(),
    st.builds(
        TrailingStop,
        atr_multiple=st.floats(
            min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
        r_increment=st.none(),
    ),
    st.builds(
        TrailingStop,
        atr_multiple=st.none(),
        r_increment=st.floats(
            min_value=1e-3, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

_atr_14 = st.one_of(
    st.none(),
    st.floats(min_value=1e-3, max_value=50.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _plans(draw):
    """Build a finite ``ManagementPlan`` exercising every optional field."""
    return ManagementPlan(
        action=draw(st.sampled_from(["BUY", "SELL"])),
        entry=draw(_price),
        initial_stop=draw(_price),
        legs=draw(_legs),
        breakeven=draw(_breakeven),
        trailing=draw(_trailing),
        atr_14=draw(_atr_14),
    )


# ── Clean candle strategy ─────────────────────────────────────────────────────
# Well-formed OHLCV dicts with finite values and numeric timestamps:
# low <= open/close <= high. These are the candles that MUST survive in both the
# clean and the polluted run.
@st.composite
def _clean_candle(draw, timestamp_ms):
    low = draw(_price)
    high = draw(st.floats(min_value=low, max_value=200.0, allow_nan=False, allow_infinity=False))
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    volume = draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    return {
        "timestamp_ms": timestamp_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ── Junk values and junk candle strategy ──────────────────────────────────────
# The non-finite / non-numeric values the simulator must treat as exclusion
# triggers on any OHLCV field (Requirement 3.2 names open/high/low/close and
# volume when present).
_junk_value = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "abc", "", True, False]
)


@st.composite
def _junk_candle(draw, timestamp_ms):
    """Build a candle that the simulator MUST exclude.

    Starts from a well-formed candle, then corrupts AT LEAST ONE of the required
    OHLC fields with a non-finite / non-numeric junk value (guaranteeing
    exclusion regardless of volume / timestamp handling), and optionally corrupts
    further fields (high/low/close/volume) and the timestamp too.
    """
    base = draw(_clean_candle(timestamp_ms))

    required = ["open", "high", "low", "close"]
    # Always corrupt at least one required OHLC field -> guaranteed exclusion.
    forced = draw(st.sampled_from(required))
    base[forced] = draw(_junk_value)

    # Optionally corrupt additional fields (including volume and the timestamp).
    for field in ["open", "high", "low", "close", "volume", "timestamp_ms"]:
        if field == forced:
            continue
        if draw(st.booleans()):
            base[field] = draw(_junk_value)

    return base


@st.composite
def _plan_clean_and_polluted(draw):
    """Draw (plan, clean_candles, polluted_candles).

    ``clean_candles`` are well-formed OHLCV dicts. ``polluted_candles`` is the
    clean set with junk candles interleaved at arbitrary positions (a permutation
    of clean + junk). Because every junk candle is excluded, the simulator should
    produce an identical ``SimulationResult`` for both inputs.
    """
    plan = draw(_plans())

    # Distinct timestamps for the clean candles so their chronological ordering is
    # unambiguous (the only thing that determines the simulated result).
    clean_ts = draw(
        st.lists(st.integers(min_value=1, max_value=10_000_000), min_size=0, max_size=20, unique=True)
    )
    clean = [draw(_clean_candle(ts)) for ts in clean_ts]

    # Junk candles carry arbitrary (possibly numeric) timestamps; it does not
    # matter because they are excluded on their corrupted OHLCV field anyway.
    n_junk = draw(st.integers(min_value=1, max_value=8))
    junk = [draw(_junk_candle(draw(st.integers(min_value=1, max_value=10_000_000)))) for _ in range(n_junk)]

    # Interleave: a permutation of the combined list places the junk candles
    # ANYWHERE among the clean ones.
    polluted = draw(st.permutations(clean + junk))

    return plan, clean, list(polluted)


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 3.11): Non-finite candles are excluded
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 10: Non-finite candles are excluded
@settings(max_examples=200, deadline=None)
@given(case=_plan_clean_and_polluted())
def test_property_10_nonfinite_candles_excluded(case):
    """Feature: trade-management, Property 10: Non-finite candles are excluded —
    inserting candles with non-finite (NaN / inf) or non-numeric (None / str /
    bool) OHLCV fields anywhere in the sequence yields the same
    ``SimulationResult`` as simulating with those candles removed, and never
    raises.

    Validates: Requirements 3.2
    """
    plan, clean, polluted = case

    # Neither call raises.
    result_clean = simulate_plan(plan, clean, _CONFIG)
    result_polluted = simulate_plan(plan, polluted, _CONFIG)

    # Excluding the junk candles leaves the simulation outcome unchanged: the full
    # SimulationResult (status, realized_r, fills, residual_fraction,
    # breakeven_moved_at, trailed) is identical.
    assert result_polluted == result_clean
