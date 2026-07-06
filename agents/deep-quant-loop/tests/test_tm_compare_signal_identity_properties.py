"""Property-based test for comparison-mode signal-set identity (backtest.py, task 10.4).

Feature: trade-management

This module implements design **Property 24: Comparison-mode signal-set identity**:

    For any generated candle history, the managed and unmanaged runs of comparison
    mode score the IDENTICAL set of generated signal entries (same count and
    timestamps), differing only in the plan applied.

Validates: Requirements 12.1.

Implementation under test: ``backtest.compare_management`` and the
``backtest.generate_and_score`` it drives. ``compare_management`` runs
``generate_and_score`` twice over identical candle history — once unmanaged
(``manage_trades=False``, scored as ``Single_Target_Trades``) and once managed
(``manage_trades=True``, scored with the uniform default ``Management_Plan``) —
toggling ONLY ``manage_trades`` via ``dataclasses.replace``. The signal GENERATION
(``_signal_for_bar``) and the regime / relative-strength / forecast labelling are
all independent of ``manage_trades``; only the *scoring* of each generated signal
differs between the two runs.

The cleanest, cleanly-isolatable invariant (per the design note on this property)
is: the SEQUENCE OF GENERATED SIGNALS — each signal's ``entry`` / ``created_at`` /
``action`` — is identical across ``manage_trades`` False vs True. We assert that
invariant directly at the ``generate_and_score`` level, element-by-element, AND we
assert ``compare_management``'s two ``signals_scored`` counts are consistent
(equal) on the same history.

ISOLATION CHOICE (documented): expired-trade filtering (``record_unresolved=False``)
could in principle drop *different* signals between the two runs if a managed plan
resolves where a single-target trade expires (or vice-versa) — that would be a
real difference in the SCORED/RETAINED set, not in the GENERATED signal set. To
isolate "signal-set identity" cleanly we set ``record_unresolved=True`` for this
property, so every generated signal is retained in both runs and the generated
sequences can be compared element-by-element. With the regime gate, the
relative-strength filter, and the forecast filter all disabled (the defaults),
the only per-run difference is the management plan and its resulting outcome.

The test stays fully OFFLINE: a deterministic synthetic candle series is passed
directly to ``compare_management(..., candles=...)`` and ``generate_and_score``,
and an explicit empty ``benchmark_candles=[]`` is supplied so no Rust tool server /
QuestDB is ever touched (a ``None`` benchmark series would trigger a network
fetch). With no benchmark series, relative strength degrades to an honest
Unavailable_Marker for every signal in BOTH runs identically — which does not
affect signal generation.

The sys.path / import pattern mirrors ``tests/test_backtest_compare_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``backtest``
is importable when pytest is run from anywhere.
"""

import os
import sys
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402

# A reduced-lookback config keeps each run fast while still satisfying the signal
# rules (which need >= max(ema_slow, ols_window) + 1 == 22 closes) and the
# generate_and_score guard (n >= lookback + 2). ``record_unresolved=True`` isolates
# signal-set identity from expired-trade filtering (see module docstring); the
# regime gate / RS filter / forecast filter stay disabled (defaults) so the only
# per-run difference is the management plan.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12,
                      record_unresolved=True)


@st.composite
def _candle_series(draw):
    """A synthetic OHLCV random-walk candle series long enough to emit signals.

    Prices drift via small per-bar steps so EMA-fast/slow crossings, value-area
    edges, and over-extensions all occur, letting the deterministic rule set emit
    a realistic mix of signals (some resolving as wins, some as losses, some
    expiring). OHLC ordering is well-formed and timestamps are strictly
    increasing and unique so each signal bar is identifiable. Mirrors the
    generator in ``test_backtest_compare_properties.py``.
    """
    n = draw(st.integers(min_value=40, max_value=140))
    price = draw(st.floats(min_value=50.0, max_value=500.0,
                           allow_nan=False, allow_infinity=False))
    base_ts = 1_700_000_000_000
    candles = []
    for i in range(n):
        step = draw(st.floats(min_value=-4.0, max_value=4.0,
                              allow_nan=False, allow_infinity=False))
        new_price = max(1.0, price + step)
        o, c = price, new_price
        up = draw(st.floats(min_value=0.0, max_value=2.5, allow_nan=False, allow_infinity=False))
        dn = draw(st.floats(min_value=0.0, max_value=2.5, allow_nan=False, allow_infinity=False))
        hi = max(o, c) + up
        lo = max(0.5, min(o, c) - dn)
        candles.append({
            "open": o, "high": hi, "low": lo, "close": c,
            "volume": draw(st.floats(min_value=0.0, max_value=1e6,
                                     allow_nan=False, allow_infinity=False)),
            "timestamp_ms": base_ts + i * 60_000,
        })
        price = new_price
    return candles


def _signal_signature(results):
    """Reduce a generate_and_score result list to its generated-signal sequence.

    Each element is the (action, entry, created_at) triple of the underlying
    generated signal — the part that signal GENERATION produces and that must be
    identical across the managed / unmanaged runs (management changes only the
    scoring/outcome, never these).
    """
    return [
        (r["decision"]["action"], r["decision"]["entry"], r["decision"]["created_at"])
        for r in results
    ]


# Feature: trade-management, Property 24: Comparison-mode signal-set identity
@settings(max_examples=50, deadline=None)
@given(candles=_candle_series())
def test_property_24_comparison_signal_set_identity(candles):
    """Validates: Requirements 12.1

    The unmanaged and managed runs of comparison mode score the SAME generated
    signal set over identical candle history: the same number of generated
    signals and, per signal, the same entry / created_at / action — differing only
    in how each signal is managed/scored.
    """
    # ── (1) generate_and_score level: toggle ONLY manage_trades ──────────────
    # benchmark_candles=[] keeps relative strength offline + identical for both
    # runs (an empty series => Unavailable_Marker for every signal, both runs).
    unmanaged_cfg = replace(_CFG, manage_trades=False)
    managed_cfg = replace(_CFG, manage_trades=True)

    unmanaged = generate_and_score(
        candles, "TESTSYM", "1d", unmanaged_cfg, benchmark_candles=[],
    )
    managed = generate_and_score(
        candles, "TESTSYM", "1d", managed_cfg, benchmark_candles=[],
    )

    # Same number of generated/scored signals (record_unresolved=True retains all).
    assert len(unmanaged) == len(managed), (
        f"signal count differs: unmanaged={len(unmanaged)} managed={len(managed)}"
    )

    # Per-signal generated identity: entry / created_at / action match exactly,
    # element-by-element and in the same order (only the outcome may differ).
    sig_unmanaged = _signal_signature(unmanaged)
    sig_managed = _signal_signature(managed)
    assert sig_unmanaged == sig_managed, (
        "generated signal sequence differs between the unmanaged and managed runs "
        "(it must be identical; only scoring/management may differ)"
    )

    # ── (2) compare_management level: the two signals_scored counts agree ────
    # Pass candles directly and benchmark_candles=[] so no network is hit; the
    # summary's unmanaged/managed signals_scored must be equal AND equal to the
    # direct generate_and_score counts above.
    summary = backtest.compare_management(
        "TESTSYM", "1d", candles=candles, benchmark_candles=[], cfg=_CFG,
    )
    assert summary["unmanaged"]["signals_scored"] == summary["managed"]["signals_scored"], (
        "compare_management reported inconsistent signals_scored across runs: "
        f"unmanaged={summary['unmanaged']['signals_scored']} "
        f"managed={summary['managed']['signals_scored']}"
    )
    assert summary["unmanaged"]["signals_scored"] == len(unmanaged), (
        "compare_management unmanaged signals_scored does not match the direct "
        f"generate_and_score count: {summary['unmanaged']['signals_scored']} != {len(unmanaged)}"
    )
    assert summary["managed"]["signals_scored"] == len(managed), (
        "compare_management managed signals_scored does not match the direct "
        f"generate_and_score count: {summary['managed']['signals_scored']} != {len(managed)}"
    )
