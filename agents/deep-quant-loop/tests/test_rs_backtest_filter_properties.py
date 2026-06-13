"""Property-based test for the backtest relative-strength filter behaviour
(backtest.py ``generate_and_score``, task 13.5).

Feature: relative-strength-context

This module implements design **Property 28: The enabled filter excludes
misaligned signals and retains unavailable ones**:

    With the relative-strength filter enabled, signals whose relative-strength
    Alignment is the AVAILABLE ``misaligned`` label are excluded from the
    with-filter seeded trade set; signals whose relative strength is an
    Unavailable_Marker (``available`` False) are RETAINED — never excluded on the
    basis of relative strength being unavailable — as are ``aligned`` and
    ``neutral`` signals. The with-filter seeded set is exactly the without-filter
    set minus the available-``misaligned`` signals, and so is a strict subset of
    it.

Validates: Requirements 11.2, 11.6.

Implementation under test: ``backtest.generate_and_score`` run with
``rs_filter_enabled`` True vs False over the IDENTICAL synthetic candle history.
``backtest.rs.classify_relative_strength`` is patched to return a controlled
Relative_Strength_Label (alignment chosen per signal — ``aligned`` /
``misaligned`` / ``neutral``) or an Unavailable_Marker, so each signal's
relative-strength outcome is controlled independently of the candle content; and
``backtest._signal_for_bar`` is forced to emit a valid BUY decision on every bar
so the with/without-filter sets differ ONLY by the filter, not by signal
emission or scoring outcomes.

The forced ``classify_relative_strength`` is keyed off the signal bar index
(``len(symbol_window) - 1``) rather than a mutable counter, so it is pure and
returns the IDENTICAL label across the gated and ungated runs, which walk
identical bars.

The sys.path / import pattern mirrors the other backtest property tests in this
directory: the service directory (one level up) is prepended to ``sys.path`` so
``backtest`` / ``rs`` are importable when pytest is run from anywhere.
"""

import copy
import os
import sys
from dataclasses import replace
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import (  # noqa: E402
    BacktestConfig,
    generate_and_score,
    _build_decision,
)

# Relative-strength "kinds" the forced classify_relative_strength can return per
# signal: the three Alignment values from a usable label plus the
# Unavailable_Marker path.
_KINDS = ["aligned", "misaligned", "neutral", "unavailable"]

# An enum-valid (index_direction, relative_strength_state) pairing per Alignment.
# The filter only reads ``available`` + ``alignment``; the other categorical
# fields are arbitrary but must be enum-valid so the seeder's defensibility entry
# is reported as a usable, AVAILABLE label.
_KIND_STATES = {
    "aligned": ("up", "leader", "aligned"),
    "misaligned": ("down", "laggard", "misaligned"),
    "neutral": ("flat", "inline", "neutral"),
}

# A small, fast config: a short lookback and a 1-bar cooldown make EVERY bar in
# range emit a signal, and ``record_unresolved`` keeps expired trades so the
# with/without-filter sets differ ONLY by the filter, not by scoring outcomes.
_FILTER_CFG = BacktestConfig(
    lookback=10,
    cooldown_bars=1,
    record_unresolved=True,
    max_horizon_bars=200,
)


def _make_candles(n):
    """A synthetic OHLCV series with unique increasing timestamps, all finite.

    The content is irrelevant to the filter property (signal emission AND the
    relative-strength label are both forced), but the bars must carry numeric
    high/low/timestamp so scoring resolves and each signal gets a unique
    ``created_at`` to key the with/without-filter sets.
    """
    candles = []
    for i in range(n):
        price = 100.0 + (i % 11)
        candles.append({
            "timestamp_ms": float(1_000_000 + i * 60_000),  # unique, increasing
            "open": price,
            "high": price + 2.0,
            "low": price - 2.0,
            "close": price,
            "volume": 1000.0,
        })
    return candles


def _forced_signal(window, created_at, cfg):
    """Deterministically emit a valid, validator-shaped BUY decision per bar."""
    vp = {"poc": 100.0, "vah": 102.0, "val": 98.0, "price_vs_value_area": "inside_value_area"}
    return _build_decision("BUY", 100.0, 1.0, "Bullish", 1.0, vp, created_at, cfg)


def _make_forced_classify_rs(kinds):
    """Build a stateless ``classify_relative_strength`` stub keyed by slice length.

    The seeder calls ``classify_relative_strength(candles[: i + 1], ...)`` for the
    signal at bar ``i``, so ``len(symbol_candles) - 1 == i`` identifies the bar.
    Keying the Alignment off the bar index (not a mutable counter) keeps the stub
    pure and identical across the filtered and unfiltered runs, which walk
    identical bars.
    """
    def _stub(symbol_candles, benchmark_candles, config,
              proposed_direction=None, symbol=None, benchmark=None, timeframe=None):
        i = len(symbol_candles) - 1
        kind = kinds[i % len(kinds)]
        if kind == "unavailable":
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "benchmark": benchmark,
                "unavailable": True,
                "reason": "synthetic insufficient aligned data",
            }
        index_direction, rs_state, alignment = _KIND_STATES[kind]
        return {
            "index_direction": index_direction,
            "relative_strength_state": rs_state,
            "alignment": alignment,
            "measures": {
                "rs_ratio": 1.0,
                "rs_ratio_slope": 0.0,
                "relative_return": 0.0,
                "correlation": 0.5,
                "beta": 1.0,
            },
            "benchmark": benchmark,
            "symbol": symbol,
            "timeframe": timeframe,
            "aligned_candles": len(symbol_candles),
        }
    return _stub


def _result_kind(result):
    """Classify a seeded result's RS entry as ('avail', alignment) or ('unavail', None)."""
    entry = (result["decision"].get("defensibility") or {}).get("relative_strength") or {}
    if entry.get("available") is True:
        return ("avail", entry.get("alignment"))
    return ("unavail", None)


def _ts_map(results):
    """Map each result's unique ``created_at`` to its relative-strength kind."""
    return {r["decision"]["created_at"]: _result_kind(r) for r in results}


# ─────────────────────────────────────────────────────────────────────────────
# Property 28: The enabled filter excludes misaligned signals and retains
# unavailable ones
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 28: The enabled filter excludes misaligned signals and retains unavailable ones
@settings(max_examples=100, deadline=None)
@given(
    extra_bars=st.integers(min_value=12, max_value=60),
    kinds=st.lists(st.sampled_from(_KINDS), min_size=1, max_size=8),
)
def test_property_28_filter_excludes_misaligned_retains_unavailable(extra_bars, kinds):
    """Validates: Requirements 11.2, 11.6

    Running ``generate_and_score`` with the relative-strength filter ENABLED and
    DISABLED over the identical candle history (signal emission forced,
    relative-strength label/marker forced per signal): the with-filter set equals
    the without-filter set MINUS exactly the available-``misaligned`` signals;
    Unavailable_Marker signals (and aligned/neutral signals) are retained in BOTH
    runs; and the with-filter set is a strict subset of the without-filter set.
    """
    n = _FILTER_CFG.lookback + extra_bars
    candles = _make_candles(n)
    candles_snapshot = copy.deepcopy(candles)

    forced_classify = _make_forced_classify_rs(kinds)
    # A benchmark series is supplied so the production path is exercised exactly;
    # the patched classifier ignores its content, but the seeder still slices it.
    benchmark_candles = _make_candles(n)

    with mock.patch.object(backtest, "_signal_for_bar", _forced_signal), \
            mock.patch.object(backtest.rs, "classify_relative_strength", forced_classify):
        unfiltered = generate_and_score(
            candles, "SYM", "15m", replace(_FILTER_CFG, rs_filter_enabled=False),
            benchmark_candles=benchmark_candles, benchmark="NIFTY 50",
        )
        filtered = generate_and_score(
            candles, "SYM", "15m", replace(_FILTER_CFG, rs_filter_enabled=True),
            benchmark_candles=benchmark_candles, benchmark="NIFTY 50",
        )

    # Inputs are never mutated by the seeder (offline, pure given candles).
    assert candles == candles_snapshot

    unfiltered_map = _ts_map(unfiltered)
    filtered_map = _ts_map(filtered)

    # The runs walk identical bars (cooldown advances identically whether a
    # signal is taken or filtered), so the unfiltered run must produce at least
    # one signal — the property would be vacuous otherwise.
    assert unfiltered, "expected the forced-signal unfiltered run to produce signals"

    # ── No filtered result is an available-``misaligned`` signal (R11.2) ──────
    for ts, kind in filtered_map.items():
        assert kind != ("avail", "misaligned"), (
            f"filtered run retained an available-misaligned signal at {ts}: {kind}"
        )

    # ── The filtered set is EXACTLY the unfiltered set minus available-misaligned
    expected_filtered_ts = {
        ts for ts, kind in unfiltered_map.items() if kind != ("avail", "misaligned")
    }
    assert set(filtered_map.keys()) == expected_filtered_ts

    # ── Unavailable signals are retained in BOTH runs (R11.6) ─────────────────
    for ts, kind in unfiltered_map.items():
        if kind == ("unavail", None):
            assert ts in filtered_map, (
                f"filter dropped an Unavailable_Marker signal at {ts}"
            )

    # ── Aligned / neutral signals are retained in BOTH runs ───────────────────
    for ts, kind in unfiltered_map.items():
        if kind in (("avail", "aligned"), ("avail", "neutral")):
            assert ts in filtered_map, f"filter dropped a {kind} signal at {ts}"

    # ── Retained signals carry the IDENTICAL relative-strength entry across runs
    for ts in filtered_map:
        assert filtered_map[ts] == unfiltered_map[ts]

    # ── The filter only ever removes signals; the with-filter set is a SUBSET ──
    assert set(filtered_map).issubset(set(unfiltered_map))
    # ── And a STRICT subset whenever any available-misaligned signal existed ──
    if any(kind == ("avail", "misaligned") for kind in unfiltered_map.values()):
        assert len(filtered_map) < len(unfiltered_map)
