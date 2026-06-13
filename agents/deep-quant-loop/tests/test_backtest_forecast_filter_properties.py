"""Property-based test for the backtest forecast filter behaviour
(backtest.py ``generate_and_score``, task 14.5).

Feature: volatility-aware-forecaster

This module implements design **Property 34: The enabled filter excludes
misaligned signals and retains unavailable ones**:

    With the forecast filter enabled, signals whose Forecast_Alignment is the
    AVAILABLE ``misaligned`` label are excluded from the with-forecast seeded
    trade set; signals whose forecast is an Unavailable_Marker (``available``
    False) are RETAINED — never excluded on the basis of the forecast being
    unavailable — as are ``aligned`` and ``neutral`` signals. The with-forecast
    seeded set is exactly the without-forecast set minus the available-``misaligned``
    signals, and so is a (weak, and — when any misaligned signal exists — strict)
    subset of it.

Validates: Requirements 13.2, 13.4.

Implementation under test: ``backtest.generate_and_score`` run with
``forecast_filter_enabled`` True vs False over the IDENTICAL synthetic candle
history. ``backtest.forecaster.forecast`` is patched to return a controlled
Forecast_Label (alignment chosen per signal — ``aligned`` / ``misaligned`` /
``neutral``) or an Unavailable_Marker, so each signal's forecast outcome is
controlled independently of the candle content; and ``backtest._signal_for_bar``
is forced to emit a valid BUY decision on every bar so the with/without-forecast
sets differ ONLY by the filter, not by signal emission or scoring outcomes.

The forced ``forecast`` stub is keyed off the signal bar index
(``len(candles) - 1``) rather than a mutable counter, so it is pure and returns
the IDENTICAL label across the filtered and unfiltered runs, which walk
identical bars.

The sys.path / import pattern mirrors the other backtest property tests in this
directory: the service directory (one level up) is prepended to ``sys.path`` so
``backtest`` / ``forecaster`` are importable when pytest is run from anywhere.
"""

import copy
import os
import sys
from dataclasses import replace
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import (  # noqa: E402
    BacktestConfig,
    generate_and_score,
    _build_decision,
    _signal_is_forecast_misaligned,
)

# Forecast "kinds" the forced forecast stub can return per signal: the three
# Forecast_Alignment values from a usable label plus the Unavailable_Marker path.
_KINDS = ["aligned", "misaligned", "neutral", "unavailable"]

# An enum-valid projected_direction per Forecast_Alignment. The filter only reads
# ``available`` + ``forecast_alignment``; the other fields are arbitrary but must
# be enum-valid / finite so _forecast_defensibility_entry reports a usable,
# AVAILABLE label.
_KIND_DIRECTION = {
    "aligned": "up",
    "misaligned": "down",
    "neutral": "flat",
}

# A small, fast config: a short lookback and a 1-bar cooldown make EVERY bar in
# range emit a signal, and ``record_unresolved`` keeps expired trades so the
# with/without-forecast sets differ ONLY by the filter, not by scoring outcomes.
_FILTER_CFG = BacktestConfig(
    lookback=10,
    cooldown_bars=1,
    record_unresolved=True,
    max_horizon_bars=200,
)


def _make_candles(n):
    """A synthetic OHLCV series with unique increasing timestamps, all finite.

    The content is irrelevant to the filter property (signal emission AND the
    forecast label are both forced), but the bars must carry numeric
    high/low/timestamp so scoring resolves and each signal gets a unique
    ``created_at`` to key the with/without-forecast sets.
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


def _make_forced_forecast(kinds):
    """Build a stateless ``forecaster.forecast`` stub keyed by slice length.

    The seeder calls ``forecaster.forecast(candles[: i + 1], ...)`` for the signal
    at bar ``i``, so ``len(candles) - 1 == i`` identifies the bar. Keying the
    Forecast_Alignment off the bar index (not a mutable counter) keeps the stub
    pure and identical across the filtered and unfiltered runs, which walk
    identical bars.
    """
    def _stub(candles, config, proposed_direction=None, symbol=None, timeframe=None):
        i = len(candles) - 1
        kind = kinds[i % len(kinds)]
        if kind == "unavailable":
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "unavailable": True,
                "reason": "synthetic insufficient data",
            }
        direction = _KIND_DIRECTION[kind]
        return {
            "projected_direction": direction,
            "up_probability": 0.6,
            "expected_move_atr": 1.0,
            "forecast_confidence": 0.7,
            "forecast_alignment": kind,
            "measures": {"drift": 0.0, "volatility": 1.0, "standardized_drift": 0.0, "atr": 1.0},
            "symbol": symbol,
            "timeframe": timeframe,
            "candles_used": len(candles),
        }
    return _stub


def _result_kind(result):
    """Classify a seeded result's forecast entry as ('avail', alignment) or ('unavail', None)."""
    entry = (result["decision"].get("defensibility") or {}).get("forecast") or {}
    if entry.get("available") is True:
        return ("avail", entry.get("forecast_alignment"))
    return ("unavail", None)


def _id_map(results):
    """Map each result's signal identity (created_at, action, entry) to its forecast kind."""
    out = {}
    for r in results:
        d = r["decision"]
        out[(d["created_at"], d["action"], d["entry"])] = _result_kind(r)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 34: The enabled filter excludes misaligned signals and retains
# unavailable ones
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 34: The enabled filter excludes misaligned signals and retains unavailable ones
@settings(max_examples=100, deadline=None)
@given(
    extra_bars=st.integers(min_value=12, max_value=60),
    kinds=st.lists(st.sampled_from(_KINDS), min_size=1, max_size=8),
)
def test_property_34_forecast_filter_excludes_misaligned_retains_unavailable(extra_bars, kinds):
    """Validates: Requirements 13.2, 13.4

    Running ``generate_and_score`` with the forecast filter ENABLED and DISABLED
    over the identical candle history (signal emission forced, forecast
    label/marker forced per signal): the with-forecast set equals the
    without-forecast set MINUS exactly the available-``misaligned`` signals;
    Unavailable_Marker signals (and aligned/neutral signals) are retained in BOTH
    runs; and the with-forecast set is a subset of the without-forecast set.
    """
    n = _FILTER_CFG.lookback + extra_bars
    candles = _make_candles(n)
    candles_snapshot = copy.deepcopy(candles)

    # ── Direct unit-assert of the drop predicate on crafted decisions ─────────
    # The with-forecast filter drops a signal IFF its forecast entry is an
    # AVAILABLE ``misaligned`` label (R13.2); an unavailable entry and the
    # ``aligned`` / ``neutral`` labels are all RETAINED (R13.4).
    def _decision_with_forecast(entry):
        return {"action": "BUY", "defensibility": {"forecast": entry}}

    assert _signal_is_forecast_misaligned(
        _decision_with_forecast({"available": True, "forecast_alignment": "misaligned"})
    ) is True
    assert _signal_is_forecast_misaligned(
        _decision_with_forecast({"available": False, "reason": "forecast unavailable"})
    ) is False
    # An unavailable entry that still (incorrectly) carried a misaligned label is
    # NEVER dropped — availability gates the drop, not the alignment string alone.
    assert _signal_is_forecast_misaligned(
        _decision_with_forecast({"available": False, "forecast_alignment": "misaligned"})
    ) is False
    assert _signal_is_forecast_misaligned(
        _decision_with_forecast({"available": True, "forecast_alignment": "aligned"})
    ) is False
    assert _signal_is_forecast_misaligned(
        _decision_with_forecast({"available": True, "forecast_alignment": "neutral"})
    ) is False
    # A missing / empty forecast entry is retained (no drop).
    assert _signal_is_forecast_misaligned({"action": "BUY", "defensibility": {}}) is False
    assert _signal_is_forecast_misaligned({"action": "BUY"}) is False

    forced_forecast = _make_forced_forecast(kinds)
    # A benchmark series is supplied so the production path is exercised exactly;
    # generate_and_score labels relative strength regardless, so keep both runs
    # well-formed and identical apart from the forecast-filter flag.
    benchmark_candles = _make_candles(n)

    with mock.patch.object(backtest, "_signal_for_bar", _forced_signal), \
            mock.patch.object(backtest.forecaster, "forecast", forced_forecast):
        unfiltered = generate_and_score(
            candles, "SYM", "15m", replace(_FILTER_CFG, forecast_filter_enabled=False),
            benchmark_candles=benchmark_candles, benchmark="NIFTY 50",
        )
        filtered = generate_and_score(
            candles, "SYM", "15m", replace(_FILTER_CFG, forecast_filter_enabled=True),
            benchmark_candles=benchmark_candles, benchmark="NIFTY 50",
        )

    # Inputs are never mutated by the seeder (offline, pure given candles).
    assert candles == candles_snapshot

    unfiltered_map = _id_map(unfiltered)
    filtered_map = _id_map(filtered)

    # The runs walk identical bars (cooldown advances identically whether a
    # signal is taken or filtered), so the unfiltered run must produce at least
    # one signal — the property would be vacuous otherwise.
    assert unfiltered, "expected the forced-signal unfiltered run to produce signals"

    # ── No filtered result is an available-``misaligned`` signal (R13.2) ──────
    for sig_id, kind in filtered_map.items():
        assert kind != ("avail", "misaligned"), (
            f"filtered run retained an available-misaligned signal {sig_id}: {kind}"
        )

    # ── The filtered set is EXACTLY the unfiltered set minus available-misaligned
    expected_filtered = {
        sig_id for sig_id, kind in unfiltered_map.items() if kind != ("avail", "misaligned")
    }
    assert set(filtered_map.keys()) == expected_filtered

    # ── Unavailable signals are retained in BOTH runs (R13.4) ─────────────────
    for sig_id, kind in unfiltered_map.items():
        if kind == ("unavail", None):
            assert sig_id in filtered_map, (
                f"filter dropped an Unavailable_Marker signal {sig_id}"
            )

    # ── Aligned / neutral signals are retained in BOTH runs ───────────────────
    for sig_id, kind in unfiltered_map.items():
        if kind in (("avail", "aligned"), ("avail", "neutral")):
            assert sig_id in filtered_map, f"filter dropped a {kind} signal {sig_id}"

    # ── Retained signals carry the IDENTICAL forecast entry across runs ───────
    for sig_id in filtered_map:
        assert filtered_map[sig_id] == unfiltered_map[sig_id]

    # ── The filter only ever removes signals; the with-forecast set is a SUBSET ─
    assert set(filtered_map).issubset(set(unfiltered_map))
    # ── And a STRICT subset whenever any available-misaligned signal existed ──
    if any(kind == ("avail", "misaligned") for kind in unfiltered_map.values()):
        assert len(filtered_map) < len(unfiltered_map)
