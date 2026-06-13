"""Unit test: Python ``compute_tick_ofi`` mirrors the Rust ``compute_order_flow_imbalance``.

Feature: order-flow-context (task 3.5, anchors design decision AD-8)

The authoritative Tick_OFI math lives in the Rust function
``compute_order_flow_imbalance`` in
``frontend/src-tauri/src/commands/deep_quant.rs``. The Python
``order_flow.compute_tick_ofi`` is required to reproduce that arithmetic exactly
so the live tool path and the Rust path agree (Requirements 2.1, 2.2).

Because invoking the Rust binary from the Python test environment is not
feasible (and we must not block on the Rust toolchain), this test pins the
agreement two ways:

  1. A *shared representative tick fixture* — a fixed, oldest-first sequence of
     ticks that exercises every branch of the Rust algorithm: upticks,
     downticks, zero-ticks (sign inheritance), the Lee-Ready quote-location
     refinement (trade above mid -> +1, below mid -> -1, at mid -> tick sign),
     ticks with an absent quote (best_bid == best_ask == 0 -> refinement
     skipped), a cumulative-volume *session reset* (negative delta -> skipped),
     and a zero-volume delta (-> skipped). It also includes two *divergence*
     ticks where the quote location overrides the price-direction tick sign.

  2. A hand-derived expected OFI obtained by tracing the exact Rust arithmetic
     over that fixture (the full trace is documented below), plus an independent
     line-by-line Python re-implementation of the Rust function
     (``_rust_reference_ofi``) run over the same fixture. ``compute_tick_ofi``
     must match BOTH within a tight floating tolerance (abs_tol = 1e-9).

Validates: Requirements 2.1, 2.2.

The sys.path / import pattern mirrors the other ``tests/test_of_*`` modules.
"""

import math
import os
import sys

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import (  # noqa: E402
    OrderFlowConfig,
    compute_tick_ofi,
    resolve_order_flow_config,
)

# Floating tolerance for the cross-implementation agreement.
ABS_TOL = 1e-9

# ─────────────────────────────────────────────────────────────────────────────
# Shared representative tick fixture (oldest-first, the order _read_live_ticks
# yields after reversing its DESC query — matching the Rust ``.rev()`` step).
#
# Each tick carries last_price / volume (cumulative) / best_bid / best_ask, the
# exact shape ``tools._read_live_ticks`` produces and ``order_flow._parse_tick``
# consumes.
# ─────────────────────────────────────────────────────────────────────────────
#
# Every price / quote is a multiple of 0.5 so that each ``(bid + ask) / 2`` mid
# and every ``last_price`` is EXACTLY representable in IEEE-754 double. That
# keeps the ``last_price == mid`` (Lee-Ready "at the mid -> use the tick sign")
# branch deterministic rather than tripping on a floating-point tie that would
# otherwise land just above or below the mid.
FIXTURE_TICKS = [
    # idx 0 — base tick (no delta computed for the first tick)
    {"last_price": 100.0, "volume": 1000, "best_bid": 99.5, "best_ask": 100.5},
    # idx 1 — uptick, trade exactly at mid (100.5) -> tick sign +1
    {"last_price": 100.5, "volume": 1010, "best_bid": 100.0, "best_ask": 101.0},
    # idx 2 — zero-tick (inherits prev sign +1), trade at mid (100.5) -> +1
    {"last_price": 100.5, "volume": 1025, "best_bid": 100.0, "best_ask": 101.0},
    # idx 3 — DIVERGENCE: uptick (tick +1) but trade BELOW mid (102.0) -> -1
    {"last_price": 101.0, "volume": 1040, "best_bid": 101.5, "best_ask": 102.5},
    # idx 4 — session reset: cumulative volume drops (dv = -10) -> skipped
    {"last_price": 101.0, "volume": 1030, "best_bid": 101.0, "best_ask": 101.5},
    # idx 5 — downtick, quote ABSENT (0/0) -> refinement skipped, tick sign -1
    {"last_price": 100.0, "volume": 1045, "best_bid": 0.0, "best_ask": 0.0},
    # idx 6 — zero volume delta (dv = 0) -> skipped
    {"last_price": 100.0, "volume": 1045, "best_bid": 99.5, "best_ask": 100.5},
    # idx 7 — DIVERGENCE: downtick (tick -1) but trade ABOVE mid (98.75) -> +1
    {"last_price": 99.5, "volume": 1065, "best_bid": 98.5, "best_ask": 99.0},
    # idx 8 — zero-tick (inherits prev TICK sign -1), trade at mid (99.5) -> -1
    {"last_price": 99.5, "volume": 1085, "best_bid": 99.0, "best_ask": 100.0},
    # idx 9 — uptick, trade exactly at mid (100.5) -> tick sign +1
    {"last_price": 100.5, "volume": 1100, "best_bid": 100.0, "best_ask": 101.0},
    # idx 10 — zero-tick (inherits prev sign +1), quote ABSENT -> +1
    {"last_price": 100.5, "volume": 1115, "best_bid": 0.0, "best_ask": 0.0},
    # idx 11 — downtick, trade exactly at mid (100.0) -> tick sign -1
    {"last_price": 100.0, "volume": 1130, "best_bid": 99.5, "best_ask": 100.5},
    # idx 12 — uptick, trade exactly at mid (100.5) -> tick sign +1
    {"last_price": 100.5, "volume": 1145, "best_bid": 100.0, "best_ask": 101.0},
]

# ─────────────────────────────────────────────────────────────────────────────
# Hand-derived expected OFI — tracing the EXACT Rust arithmetic over the fixture.
#
# last_sign seeds at +1.0 (Rust ``let mut last_sign = 1.0``). For each i in
# 1..len: dv = vol[i] - vol[i-1]; skip if dv <= 0. tick_sign from price
# direction (uptick +1 / downtick -1 / zero -> last_sign). last_sign := tick_sign
# (price-based, NOT the refined sign). refined_sign from quote location when
# bid>0 and ask>0 and ask>=bid, else tick_sign. signed_vol += refined_sign*dv;
# total_vol += dv.
#
#   i=1 : dv=10  uptick +1   ltp100.5==mid100.5 -> +1   signed+=+10 -> +10  total=10
#   i=2 : dv=15  zero   +1   ltp100.5==mid100.5 -> +1   signed+=+15 -> +25  total=25
#   i=3 : dv=15  uptick +1   ltp101.0<mid102.0  -> -1   signed+=-15 -> +10  total=40
#   i=4 : dv=-10 reset -> skip
#   i=5 : dv=15  downtick -1  quote absent       -> -1  signed+=-15 ->  -5  total=55
#   i=6 : dv=0   -> skip
#   i=7 : dv=20  downtick -1  ltp99.5>mid98.75   -> +1  signed+=+20 -> +15  total=75
#   i=8 : dv=20  zero   -1    ltp99.5==mid99.5   -> -1  signed+=-20 ->  -5  total=95
#   i=9 : dv=15  uptick +1    ltp100.5==mid100.5 -> +1  signed+=+15 -> +10  total=110
#   i=10: dv=15  zero   +1    quote absent       -> +1  signed+=+15 -> +25  total=125
#   i=11: dv=15  downtick -1  ltp100.0==mid100.0 -> -1  signed+=-15 -> +10  total=140
#   i=12: dv=15  uptick +1    ltp100.5==mid100.5 -> +1  signed+=+15 -> +25  total=155
#
# signed_vol = 25.0 ; total_vol = 155.0 ; OFI = 25/155 = 5/31 ≈ 0.16129032258...
# (within [-1, 1], so no clamping needed.)
# ─────────────────────────────────────────────────────────────────────────────
EXPECTED_OFI = 25.0 / 155.0


def _rust_reference_ofi(ticks, min_ticks=10):
    """Independent, line-by-line Python re-implementation of the Rust
    ``compute_order_flow_imbalance`` body (post-fetch arithmetic), operating on
    oldest-first ``ticks``. Returns ``None`` where the Rust function returns
    ``f64::NAN`` (insufficient ticks or zero total volume). This is the
    "ground truth" the production ``compute_tick_ofi`` must reproduce."""
    rows = [
        (
            float(t["last_price"]),
            float(t["volume"]),
            float(t["best_bid"]),
            float(t["best_ask"]),
        )
        for t in ticks
    ]
    if len(rows) < min_ticks:  # Rust: rows.len() < 10
        return None

    signed_vol = 0.0
    total_vol = 0.0
    last_sign = 1.0
    for i in range(1, len(rows)):
        dv = rows[i][1] - rows[i - 1][1]
        if dv <= 0.0:
            continue
        dp = rows[i][0] - rows[i - 1][0]
        if dp > 0.0:
            tick_sign = 1.0
        elif dp < 0.0:
            tick_sign = -1.0
        else:
            tick_sign = last_sign
        last_sign = tick_sign

        bid = rows[i][2]
        ask = rows[i][3]
        if bid > 0.0 and ask > 0.0 and ask >= bid:
            mid = (bid + ask) / 2.0
            ltp = rows[i][0]
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

    if total_vol < 1e-6:
        return None
    return max(-1.0, min(1.0, signed_vol / total_vol))


def _fixture_config():
    """A config whose ``min_ticks`` is low enough for the fixture. We start from
    the resolved default (min_ticks default = 10, matching the Rust >= 10 guard)
    and only force min_ticks if the environment overrode it above the fixture
    size, so the test is robust regardless of the ambient OF_* env."""
    cfg = resolve_order_flow_config()
    if cfg.min_ticks > len(FIXTURE_TICKS):
        cfg = OrderFlowConfig(
            lookback=cfg.lookback,
            min_candles=cfg.min_candles,
            buy_pressure_threshold=cfg.buy_pressure_threshold,
            sell_pressure_threshold=cfg.sell_pressure_threshold,
            ofi_buy_threshold=cfg.ofi_buy_threshold,
            ofi_sell_threshold=cfg.ofi_sell_threshold,
            min_ticks=10,
        )
    return cfg


def test_compute_tick_ofi_matches_hand_derived_rust_trace():
    """``compute_tick_ofi`` over the shared fixture equals the hand-derived OFI
    obtained by tracing the exact Rust arithmetic, within abs_tol=1e-9."""
    cfg = _fixture_config()
    result = compute_tick_ofi(FIXTURE_TICKS, cfg)
    assert result is not None, "fixture has >= min_ticks usable ticks; OFI must be available"
    assert math.isfinite(result)
    assert math.isclose(result, EXPECTED_OFI, abs_tol=ABS_TOL), (
        f"compute_tick_ofi={result!r} != hand-derived Rust OFI={EXPECTED_OFI!r}"
    )


def test_compute_tick_ofi_matches_rust_reference_reimplementation():
    """``compute_tick_ofi`` agrees with an independent line-by-line Python mirror
    of the Rust ``compute_order_flow_imbalance`` body, within abs_tol=1e-9."""
    cfg = _fixture_config()
    result = compute_tick_ofi(FIXTURE_TICKS, cfg)
    reference = _rust_reference_ofi(FIXTURE_TICKS, min_ticks=cfg.min_ticks)
    assert reference is not None
    assert result is not None
    assert math.isclose(result, reference, abs_tol=ABS_TOL), (
        f"compute_tick_ofi={result!r} != Rust reference reimplementation={reference!r}"
    )


def test_rust_reference_matches_hand_derived_constant():
    """Sanity check that the independent reference reimplementation itself
    reproduces the documented hand-derived constant (guards the trace above)."""
    reference = _rust_reference_ofi(FIXTURE_TICKS, min_ticks=10)
    assert reference is not None
    assert math.isclose(reference, EXPECTED_OFI, abs_tol=ABS_TOL)
