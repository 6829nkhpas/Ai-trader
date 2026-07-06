"""Property-based tests for the backtest regime-gate behaviour (backtest.py, task 13.5).

Feature: regime-detection-gate

This module implements design **Property 26: The enabled gate excludes
unfavorable signals and retains unavailable ones**:

    With the regime gate enabled, signals whose regime favorability is the
    AVAILABLE ``unfavorable`` label are excluded from the with-gate seeded trade
    set; signals whose regime is an Unavailable_Marker (``available`` False) are
    RETAINED — never excluded on the basis of regime — as are ``favorable`` and
    ``neutral`` signals. The with-gate seeded set is exactly the without-gate set
    minus the available-``unfavorable`` signals.

Validates: Requirements 10.2, 10.6.

Two complementary properties are implemented:

  * **26a — the drop predicate** ``backtest._signal_is_unfavorable`` returns True
    for EXACTLY the available-``unfavorable`` regime-entry shape, and False for
    every other shape a real ``classify_regime`` result can produce (favorable /
    neutral labels, Unavailable_Markers, partial labels, non-dict / missing
    regimes). The entries are built by feeding controlled ``classify_regime``
    outputs through the SAME ``backtest._regime_defensibility_entry`` the seeder
    uses, so the predicate is exercised on real entry shapes.

  * **26b — end-to-end gate over generate_and_score** with a forced
    ``classify_regime`` (the regime label/marker per signal is controlled) and a
    deterministic signal emitter, run with the gate enabled and disabled over the
    IDENTICAL synthetic candle history. The with-gate result set is asserted to
    equal the without-gate set minus exactly the available-``unfavorable``
    signals, with Unavailable_Marker signals retained in BOTH runs.

The sys.path / import pattern mirrors the other regime property tests in this
directory: the service directory (one level up) is prepended to ``sys.path`` so
``backtest`` / ``regime`` are importable when pytest is run from anywhere.
"""

import copy
import os
import sys
from dataclasses import replace
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / regime.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import (  # noqa: E402
    BacktestConfig,
    generate_and_score,
    _signal_is_unfavorable,
    _regime_defensibility_entry,
    _build_decision,
)

# The classifier's enums.
_TREND_STATES = ["trending", "ranging", "transitional"]
_VOLATILITY_STATES = ["low", "normal", "high"]
_FAVORABILITY = ["favorable", "unfavorable", "neutral"]

# Favorability "kinds" the forced classify_regime can return per signal: the
# three available favorabilities plus the Unavailable_Marker path.
_KINDS = ["favorable", "unfavorable", "neutral", "unavailable"]

# Trend/volatility pairing per favorability (arbitrary but enum-valid; the gate
# only reads `available` + `favorability`).
_KIND_STATES = {
    "favorable": ("trending", "normal"),
    "unfavorable": ("ranging", "low"),
    "neutral": ("transitional", "normal"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Property 26a — the with-gate drop predicate
# ─────────────────────────────────────────────────────────────────────────────

@st.composite
def _regime_result_and_expectation(draw):
    """Draw a ``classify_regime``-shaped result and whether it is available-unfavorable.

    Returns ``(regime_result, expected_unfavorable)`` where ``expected_unfavorable``
    is True iff the result is a usable label whose favorability is ``unfavorable``.
    """
    shape = draw(st.sampled_from([
        "label",            # a full, usable Regime_Label
        "unavailable",      # an honest Unavailable_Marker
        "partial",          # a label missing one categorical state
        "non_dict",         # not a dict at all
    ]))

    if shape == "label":
        fav = draw(st.sampled_from(_FAVORABILITY))
        result = {
            "trend_state": draw(st.sampled_from(_TREND_STATES)),
            "volatility_state": draw(st.sampled_from(_VOLATILITY_STATES)),
            "favorability": fav,
            "measures": {
                "directional_strength": draw(st.floats(0.0, 100.0)),
                "choppiness": draw(st.floats(0.0, 100.0)),
                "efficiency_ratio": draw(st.floats(0.0, 1.0)),
                "atr_percentile": draw(st.floats(0.0, 100.0)),
                "bb_width": draw(st.floats(0.0, 1.0)),
            },
            "symbol": "SYM",
            "timeframe": "15m",
            "candles_used": draw(st.integers(50, 500)),
        }
        return result, (fav == "unfavorable")

    if shape == "unavailable":
        result = {
            "symbol": "SYM",
            "timeframe": "1m",
            "unavailable": True,
            "reason": "insufficient data: 18 valid candles received, 50 required",
        }
        return result, False

    if shape == "partial":
        # A label missing one of the three categorical states maps to an
        # unavailable entry (no fabrication) and so is NOT unfavorable.
        missing = draw(st.sampled_from(["trend_state", "volatility_state", "favorability"]))
        result = {
            "trend_state": "ranging",
            "volatility_state": "low",
            "favorability": "unfavorable",
            "measures": {},
        }
        result.pop(missing)
        return result, False

    # non_dict
    return draw(st.sampled_from([None, "unavailable", 42, ["x"]])), False


# Feature: regime-detection-gate, Property 26
@settings(max_examples=200, deadline=None)
@given(
    payload=_regime_result_and_expectation(),
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
    has_defensibility=st.booleans(),
    has_regime_key=st.booleans(),
)
def test_property_26a_drop_predicate_only_available_unfavorable(
    payload, action, has_defensibility, has_regime_key
):
    """Validates: Requirements 10.2, 10.6

    ``_signal_is_unfavorable`` returns True for EXACTLY the available-``unfavorable``
    regime entry and False for every other shape (favorable/neutral labels,
    Unavailable_Markers, partial labels, non-dict regimes, and a wholly absent
    regime/defensibility). Entries are built via the seeder's own
    ``_regime_defensibility_entry`` so the predicate is tested on real shapes.
    """
    regime_result, expected_unfavorable = payload

    # Build the defensibility regime entry exactly as the seeder does.
    entry = _regime_defensibility_entry(regime_result)

    # An entry produced by the seeder is always a dict that is safe for the
    # predicate to read; it is available-unfavorable iff the source label was a
    # usable ``unfavorable`` label.
    assert isinstance(entry, dict)
    if expected_unfavorable:
        assert entry.get("available") is True
        assert entry.get("favorability") == "unfavorable"
    else:
        assert not (entry.get("available") is True and entry.get("favorability") == "unfavorable")

    decision = {"action": action}
    if has_defensibility:
        deff = {}
        if has_regime_key:
            deff["regime"] = entry
        decision["defensibility"] = deff

    # The predicate flags the signal for dropping ONLY when the regime entry is
    # present, available, and unfavorable.
    expected = bool(has_defensibility and has_regime_key and expected_unfavorable)
    assert _signal_is_unfavorable(decision) is expected

    # When the regime is absent entirely (no defensibility or no regime key) the
    # predicate must never flag a drop — the signal is retained (R10.6).
    if not (has_defensibility and has_regime_key):
        assert _signal_is_unfavorable(decision) is False


# ─────────────────────────────────────────────────────────────────────────────
# Property 26b — end-to-end gate over generate_and_score (forced classify_regime)
# ─────────────────────────────────────────────────────────────────────────────

# A small, fast config: a short lookback and a 1-bar cooldown make EVERY bar in
# range emit a signal, and ``record_unresolved`` keeps expired trades so the
# with/without-gate sets differ ONLY by the gate, not by scoring outcomes.
_GATE_CFG = BacktestConfig(
    lookback=10,
    cooldown_bars=1,
    record_unresolved=True,
    max_horizon_bars=200,
)


def _make_candles(n):
    """A synthetic OHLCV series with unique increasing timestamps, all finite.

    The content is irrelevant to the gate property (signal emission is forced),
    but the bars must carry numeric high/low/timestamp so scoring resolves and
    each signal gets a unique ``created_at`` to key the with/without-gate sets.
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


def _make_forced_classify(kinds):
    """Build a stateless ``classify_regime`` stub keyed by candle-slice length.

    The seeder calls ``classify_regime(candles[: i + 1], ...)`` for the signal at
    bar ``i``, so ``len(candles_arg) - 1 == i`` identifies the bar. Keying the
    favorability off the bar index (not a mutable counter) keeps the stub pure
    and identical across the gated and ungated runs, which walk identical bars.
    """
    def _stub(candles_arg, config, symbol=None, timeframe=None):
        i = len(candles_arg) - 1
        kind = kinds[i % len(kinds)]
        if kind == "unavailable":
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "unavailable": True,
                "reason": "synthetic insufficient data",
            }
        trend_state, vol_state = _KIND_STATES[kind]
        return {
            "trend_state": trend_state,
            "volatility_state": vol_state,
            "favorability": kind,
            "measures": {
                "directional_strength": 30.0,
                "choppiness": 50.0,
                "efficiency_ratio": 0.5,
                "atr_percentile": 50.0,
                "bb_width": 0.04,
            },
            "symbol": symbol,
            "timeframe": timeframe,
            "candles_used": len(candles_arg),
        }
    return _stub


def _result_kind(result):
    """Classify a seeded result's regime entry as ('avail', fav) or ('unavail', None)."""
    entry = (result["decision"].get("defensibility") or {}).get("regime") or {}
    if entry.get("available") is True:
        return ("avail", entry.get("favorability"))
    return ("unavail", None)


def _ts_map(results):
    """Map each result's unique ``created_at`` to its regime kind."""
    return {r["decision"]["created_at"]: _result_kind(r) for r in results}


# Feature: regime-detection-gate, Property 26
@settings(max_examples=150, deadline=None)
@given(
    extra_bars=st.integers(min_value=12, max_value=60),
    kinds=st.lists(st.sampled_from(_KINDS), min_size=1, max_size=8),
)
def test_property_26b_gate_excludes_unfavorable_retains_unavailable(extra_bars, kinds):
    """Validates: Requirements 10.2, 10.6

    Running ``generate_and_score`` with the gate ENABLED and DISABLED over the
    identical candle history (signal emission forced, regime label/marker forced
    per signal): the with-gate set equals the without-gate set MINUS exactly the
    available-``unfavorable`` signals; Unavailable_Marker signals (and favorable/
    neutral signals) are retained in BOTH runs.
    """
    n = _GATE_CFG.lookback + extra_bars
    candles = _make_candles(n)
    candles_snapshot = copy.deepcopy(candles)

    forced_classify = _make_forced_classify(kinds)

    with mock.patch.object(backtest, "_signal_for_bar", _forced_signal), \
            mock.patch.object(backtest.regime, "classify_regime", forced_classify):
        ungated = generate_and_score(
            candles, "SYM", "15m", replace(_GATE_CFG, regime_gate_enabled=False)
        )
        gated = generate_and_score(
            candles, "SYM", "15m", replace(_GATE_CFG, regime_gate_enabled=True)
        )

    # Inputs are never mutated by the seeder (offline, pure given candles).
    assert candles == candles_snapshot

    ungated_map = _ts_map(ungated)
    gated_map = _ts_map(gated)

    # The runs walk identical bars (cooldown advances identically whether a
    # signal is taken or gated), so the ungated run must produce at least one
    # signal — the property would be vacuous otherwise.
    assert ungated, "expected the forced-signal ungated run to produce signals"

    # ── No gated result is an available-``unfavorable`` signal (R10.2) ────────
    for ts, kind in gated_map.items():
        assert kind != ("avail", "unfavorable"), (
            f"gated run retained an available-unfavorable signal at {ts}: {kind}"
        )

    # ── The gated set is EXACTLY the ungated set minus available-unfavorable ──
    expected_gated_ts = {
        ts for ts, kind in ungated_map.items() if kind != ("avail", "unfavorable")
    }
    assert set(gated_map.keys()) == expected_gated_ts

    # ── Unavailable signals are retained in BOTH runs (R10.6) ─────────────────
    for ts, kind in ungated_map.items():
        if kind == ("unavail", None):
            assert ts in gated_map, f"gate dropped an Unavailable_Marker signal at {ts}"

    # ── Favorable / neutral signals are retained in BOTH runs ─────────────────
    for ts, kind in ungated_map.items():
        if kind in (("avail", "favorable"), ("avail", "neutral")):
            assert ts in gated_map, f"gate dropped a {kind} signal at {ts}"

    # ── Retained signals carry the IDENTICAL regime entry across both runs ────
    for ts in gated_map:
        assert gated_map[ts] == ungated_map[ts]

    # ── The gate only ever removes signals; it never adds or alters them ──────
    assert set(gated_map).issubset(set(ungated_map))
    if any(kind == ("avail", "unfavorable") for kind in ungated_map.values()):
        assert len(gated_map) < len(ungated_map)
