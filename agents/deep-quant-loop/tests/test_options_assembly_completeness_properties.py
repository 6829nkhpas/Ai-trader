"""Property-based tests for assembly completeness and honest degradation (options.py, task 9.4).

Feature: options-analytics-engine

This Hypothesis property exercises the engine's *result contract*: the assembled
``Options_Analytics_Result`` is structurally complete, and the engine degrades
honestly when its inputs are missing. It targets the pure assembler
(:func:`options.assemble_result`) directly for the completeness aspect, and the
top-level orchestrator (:func:`options.compute_options_analytics`, with a
monkeypatched read layer) for the degradation aspect:

  * Property 12 (6.1, 7.1, 7.2, 7.3) — Assembly is complete and degradation is
        honest. For any computable chain the assembled result contains all of
        PCR (OI + volume), max pain, aggregate and per-strike OI buildup,
        per-strike IV and Greeks, IV skew, OI-wall support and resistance,
        futures basis, and the underlying/expiry/spot/snapshot timestamp; when no
        snapshot is available or spot is unavailable the engine instead returns
        an ``Unavailable_Marker`` whose reason identifies the missing-data
        condition; and when a single analytic cannot be computed only that field
        is null while the remaining analytics are still returned.
"""

import contextlib
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
from options import (  # noqa: E402
    ChainSnapshot,
    StrikeQuote,
    assemble_result,
    compute_options_analytics,
    resolve_options_config,
)


# ── Expected result contract (the design's Options_Analytics_Result shape) ────
# Every top-level field the assembled success result MUST contain (R6.1). These
# mirror the design's `Options_Analytics_Result` (success shape) verbatim.
_EXPECTED_TOP_LEVEL_KEYS = frozenset({
    "underlying", "expiry", "spot", "snapshot_ts",
    "pcr_oi", "pcr_volume", "max_pain",
    "oi_buildup", "iv_skew", "oi_walls", "futures_basis",
    "per_strike",
})

# Each per-strike leg (ce / pe) MUST carry exactly these keys.
_EXPECTED_LEG_KEYS = frozenset({
    "iv", "delta", "gamma", "theta", "vega", "oi_buildup",
})

# The five buildup labels the classifier may emit.
_BUILDUP_LABELS = frozenset({
    "long_buildup", "short_buildup", "long_unwinding", "short_covering", "neutral",
})


# ── Smart generators constrained to a realistic, computable chain ─────────────
# Strikes are distinct finite positives drawn from a realistic ladder around a
# spot near 24000 so per-strike Black-Scholes is well-posed (positive S, K, T).
_strike_value = st.floats(
    min_value=20_000.0, max_value=28_000.0,
    allow_nan=False, allow_infinity=False,
)

# An option leg price: a finite positive (the normal case) or absent (None) so
# the assembler must tolerate a null-IV leg while still emitting the leg shape.
_price_value = st.one_of(
    st.none(),
    st.floats(min_value=0.5, max_value=2_000.0, allow_nan=False, allow_infinity=False),
)

# Open interest / volume: finite non-negative or absent.
_oi_value = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _chain_snapshots(draw):
    """A ChainSnapshot with a non-empty ladder of distinct finite strikes.

    Each strike carries finite-or-absent CE/PE prices, OI, and volume — the
    realistic input space over which the chain is "computable" (a well-defined
    ladder), while still exercising the null-leg degradation paths.
    """
    strikes = draw(st.lists(_strike_value, min_size=1, max_size=10, unique=True))
    quotes = []
    for k in sorted(strikes):
        quotes.append(
            StrikeQuote(
                strike=k,
                ce_price=draw(_price_value),
                pe_price=draw(_price_value),
                ce_oi=draw(_oi_value),
                pe_oi=draw(_oi_value),
                ce_volume=draw(_oi_value),
                pe_volume=draw(_oi_value),
            )
        )
    # A realistic, fixed-format expiry + epoch-ms snapshot timestamp.
    return ChainSnapshot(
        underlying=draw(st.sampled_from(["NIFTY 50", "BANKNIFTY", "TEST"])),
        expiry=draw(st.sampled_from(["2025-12-25", "2026-01-29", "2025-11-27"])),
        snapshot_ts=draw(st.integers(min_value=1_600_000_000_000,
                                     max_value=1_900_000_000_000)),
        strikes=tuple(quotes),
    )


_spot_value = st.floats(
    min_value=20_000.0, max_value=28_000.0,
    allow_nan=False, allow_infinity=False,
)

# Best-effort future price: a finite positive (basis computable) or absent
# (basis null — the common case per the design's F1 selection note).
_future_value = st.one_of(
    st.none(),
    st.floats(min_value=20_000.0, max_value=28_000.0, allow_nan=False, allow_infinity=False),
)


@contextlib.contextmanager
def _patched_read_layer(snapshot_pair, spot_fn, future_fn):
    """Temporarily swap the impure read-layer functions on the options module.

    Restores the originals on exit so the patch is reset for every generated
    Hypothesis input (the function-scoped ``monkeypatch`` fixture is not reset
    between ``@given`` examples, so we manage save/restore explicitly).
    """
    originals = {
        "read_latest_and_prior_snapshot": options.read_latest_and_prior_snapshot,
        "read_spot": options.read_spot,
        "read_future_price": options.read_future_price,
    }
    options.read_latest_and_prior_snapshot = lambda u, e: snapshot_pair
    options.read_spot = spot_fn
    options.read_future_price = future_fn
    try:
        yield
    finally:
        for name, fn in originals.items():
            setattr(options, name, fn)


def _boom(*_args, **_kwargs):
    """A read-layer stub that must never be called on the current degradation path."""
    raise AssertionError("read layer consulted on a path that should short-circuit")


def _assert_complete_result(result, spot, future_price):
    """Assert the assembled result is the structurally-complete success shape."""
    # Not a degraded marker.
    assert "unavailable" not in result

    # All top-level fields present (R6.1).
    assert set(result.keys()) == _EXPECTED_TOP_LEVEL_KEYS

    # Chain identity / spot / timestamp present.
    assert isinstance(result["underlying"], str)
    assert isinstance(result["expiry"], str)
    assert result["snapshot_ts"] is None or isinstance(result["snapshot_ts"], int)

    # Aggregate OI buildup: both sides present and a valid label.
    oi_buildup = result["oi_buildup"]
    assert set(oi_buildup.keys()) == {"call", "put"}
    assert oi_buildup["call"] in _BUILDUP_LABELS
    assert oi_buildup["put"] in _BUILDUP_LABELS

    # OI walls: support + resistance present (each finite or null).
    oi_walls = result["oi_walls"]
    assert set(oi_walls.keys()) == {"support", "resistance"}

    # IV skew: null or the three-field dict.
    iv_skew = result["iv_skew"]
    if iv_skew is not None:
        assert set(iv_skew.keys()) == {"put_minus_call", "slope", "atm_iv"}

    # Per-strike: one entry per ladder strike, each with the full ce/pe leg shape.
    per_strike = result["per_strike"]
    assert isinstance(per_strike, list)
    for entry in per_strike:
        assert set(entry.keys()) == {"strike", "ce", "pe"}
        for leg_name in ("ce", "pe"):
            leg = entry[leg_name]
            assert set(leg.keys()) == _EXPECTED_LEG_KEYS
            assert leg["oi_buildup"] in _BUILDUP_LABELS
            # Each numeric leaf is a finite number or null — never NaN/±inf.
            for field in ("iv", "delta", "gamma", "theta", "vega"):
                v = leg[field]
                assert v is None or (isinstance(v, (int, float)) and math.isfinite(v))


# ─────────────────────────────────────────────────────────────────────────────
# Property 12 — completeness aspect: assemble_result is structurally complete
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 12: Assembly is complete and degradation is honest
@settings(max_examples=100)
@given(snapshot=_chain_snapshots(), spot=_spot_value, future_price=_future_value)
def test_property_12_assembled_result_is_structurally_complete(snapshot, spot, future_price):
    """Feature: options-analytics-engine, Property 12: Assembly is complete and
    degradation is honest — for any computable chain, the assembled result
    contains all of PCR (OI + volume), max pain, aggregate and per-strike OI
    buildup, per-strike IV and Greeks, IV skew, OI-wall support and resistance,
    futures basis, and the underlying/expiry/spot/snapshot timestamp.

    Validates: Requirements 6.1, 7.3
    """
    config = resolve_options_config()
    result = assemble_result(snapshot, None, spot, future_price, config)

    _assert_complete_result(result, spot, future_price)

    # Single-analytic degradation is honest: when no future price is supplied the
    # futures basis is null while every other analytic is still returned (R7.3).
    if future_price is None:
        assert result["futures_basis"] is None
    # With no prior snapshot, aggregate + per-strike buildup are neutral while the
    # remaining analytics still populate the complete result (R3.3 / R7.3).
    assert result["oi_buildup"]["call"] == "neutral"
    assert result["oi_buildup"]["put"] == "neutral"
    for entry in result["per_strike"]:
        assert entry["ce"]["oi_buildup"] == "neutral"
        assert entry["pe"]["oi_buildup"] == "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# Property 12 — degradation aspect: honest Unavailable_Marker on missing inputs
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 12: Assembly is complete and degradation is honest
@settings(max_examples=100)
@given(
    underlying=st.sampled_from(["NIFTY 50", "BANKNIFTY", "TEST"]),
    expiry=st.sampled_from(["2025-12-25", "2026-01-29"]),
)
def test_property_12_no_snapshot_yields_honest_unavailable_marker(underlying, expiry):
    """Feature: options-analytics-engine, Property 12: Assembly is complete and
    degradation is honest — when no chain snapshot is available the engine
    returns an ``Unavailable_Marker`` whose reason identifies the missing-data
    condition rather than computing over fabricated data.

    Validates: Requirements 7.1
    """
    # No snapshot: the read layer returns the (None, None) sentinel. Spot/future
    # must NOT be consulted on the no-snapshot path (the guards raise if they are).
    with _patched_read_layer((None, None), _boom, _boom):
        result = compute_options_analytics(underlying, expiry)

    # Honest marker shape: identity + unavailable flag + a missing-data reason.
    assert result.get("unavailable") is True
    assert result["underlying"] == underlying
    assert result["expiry"] == expiry
    assert isinstance(result.get("reason"), str) and result["reason"]
    # The reason names the missing chain (the no-snapshot condition).
    assert "snapshot" in result["reason"].lower()
    # Analytic fields are omitted (never defaulted / fabricated).
    assert "pcr_oi" not in result
    assert "per_strike" not in result


# Feature: options-analytics-engine, Property 12: Assembly is complete and degradation is honest
@settings(max_examples=100)
@given(snapshot=_chain_snapshots())
def test_property_12_no_spot_yields_honest_unavailable_marker(snapshot):
    """Feature: options-analytics-engine, Property 12: Assembly is complete and
    degradation is honest — when the underlying spot is unavailable the engine
    returns an ``Unavailable_Marker`` whose reason identifies the missing-data
    condition rather than computing spot-relative analytics from a fabricated
    spot.

    Validates: Requirements 7.2
    """
    # A snapshot exists, but spot is unavailable (read_spot → None sentinel).
    with _patched_read_layer((snapshot, None), lambda u: None, lambda u: None):
        result = compute_options_analytics(snapshot.underlying, snapshot.expiry)

    # Honest marker shape with a spot-specific missing-data reason.
    assert result.get("unavailable") is True
    assert result["underlying"] == snapshot.underlying
    assert result["expiry"] == snapshot.expiry
    assert isinstance(result.get("reason"), str) and result["reason"]
    assert "spot" in result["reason"].lower()
    # Analytic fields are omitted.
    assert "per_strike" not in result


# Feature: options-analytics-engine, Property 12: Assembly is complete and degradation is honest
@settings(max_examples=100)
@given(snapshot=_chain_snapshots(), spot=_spot_value)
def test_property_12_computable_chain_yields_complete_result_via_orchestrator(snapshot, spot):
    """Feature: options-analytics-engine, Property 12: Assembly is complete and
    degradation is honest — when a snapshot and spot are both available, the
    top-level orchestrator returns the structurally-complete success result
    (no marker), with the futures basis null when no future price is read.

    Validates: Requirements 6.1, 7.3
    """
    # No future price available — the common case → futures basis null (R7.3).
    with _patched_read_layer((snapshot, None), lambda u: spot, lambda u: None):
        result = compute_options_analytics(snapshot.underlying, snapshot.expiry)

    _assert_complete_result(result, spot, None)
    # Single-analytic degradation: futures basis null, the rest still returned.
    assert result["futures_basis"] is None
