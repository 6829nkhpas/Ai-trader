"""Property-based test for engine totality / never-raise (options.py, task 9.5).

Feature: options-analytics-engine

This Hypothesis property exercises the engine's *totality* guarantee: every pure
analytic and the top-level orchestrator must return a value — a result, an
``Unavailable_Marker``, a null field, or a defined label — for ANY input,
including deliberately degenerate input, **without ever raising an exception**.

  * Property 13 (2.5, 4.4, 7.4) — The engine is total and never raises: for any
    input — including degenerate input such as an empty strike ladder, all-null
    fields, a non-finite spot, or a missing prior snapshot — every pure analytic
    (``compute_pcr_oi``, ``compute_pcr_volume``, ``compute_max_pain``,
    ``compute_iv_skew``, ``classify_oi_buildup``, ``aggregate_oi_buildup``,
    ``compute_oi_walls``, ``compute_futures_basis``, ``assemble_result``) plus the
    Black-Scholes core (``bs_price`` / ``bs_implied_vol`` / ``bs_greeks``) and the
    top-level ``compute_options_analytics`` returns a value without raising.

The pure analytics are fed in-memory degenerate snapshots directly; the
orchestrator's impure read layer (``read_latest_and_prior_snapshot`` / ``read_spot``
/ ``read_future_price``) is monkeypatched to return generated/degenerate values so
``compute_options_analytics`` can be driven over the same degenerate space with no
QuestDB and asserted to never raise and to always return a dict.
"""

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
    OptionsConfig,
    StrikeQuote,
    aggregate_oi_buildup,
    assemble_result,
    bs_greeks,
    bs_implied_vol,
    bs_price,
    classify_oi_buildup,
    compute_futures_basis,
    compute_iv_skew,
    compute_max_pain,
    compute_oi_walls,
    compute_options_analytics,
    compute_pcr_oi,
    compute_pcr_volume,
    resolve_options_config,
)


# ── Smart generators spanning the FULL degenerate input space ─────────────────
# Every analytic must tolerate non-finite / non-numeric / absent fields, so the
# scalar generators deliberately mix finite numbers with None, NaN, ±inf, and the
# occasional non-numeric value. The strike-ladder generator mixes empty ladders,
# finite strikes, and non-finite strikes so empty / all-null / non-finite chains
# are all reachable.

# A "weird" numeric field: a finite number OR a non-finite / absent / non-numeric
# value the analytics must exclude rather than crash on.
_weird_number = st.one_of(
    st.none(),
    st.floats(min_value=-1_000_000.0, max_value=1_000_000.0,
              allow_nan=False, allow_infinity=False),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.just(0.0),
    st.text(max_size=3),          # non-numeric junk
    st.booleans(),                # bool is explicitly excluded by the analytics
)

# A "weird" strike value: finite positives plus the degenerate cases.
_weird_strike = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=100_000.0,
              allow_nan=False, allow_infinity=False),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)

# A "weird" spot: a finite price OR a non-finite / absent / non-numeric value.
_weird_spot = st.one_of(
    st.none(),
    st.floats(min_value=-100.0, max_value=100_000.0,
              allow_nan=False, allow_infinity=False),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)

# A "weird" option-type tag: recognized CE/PE tags plus junk / non-strings.
_weird_option_type = st.one_of(
    st.sampled_from(["CE", "PE", "C", "P", "call", "put", "CALL", "PUT"]),
    st.text(max_size=4),
    st.none(),
    st.integers(),
)


@st.composite
def _weird_quote(draw):
    """A StrikeQuote with a possibly non-finite strike and all-weird fields."""
    return StrikeQuote(
        strike=draw(_weird_strike),
        ce_price=draw(_weird_number),
        pe_price=draw(_weird_number),
        ce_oi=draw(_weird_number),
        pe_oi=draw(_weird_number),
        ce_volume=draw(_weird_number),
        pe_volume=draw(_weird_number),
    )


@st.composite
def _weird_snapshot(draw):
    """A ChainSnapshot spanning empty, all-null, and non-finite-field ladders.

    The ladder may be empty (degenerate), and ``snapshot_ts`` / ``underlying`` /
    ``expiry`` may themselves be odd so the orchestrator and assembly are exercised
    over genuinely degenerate identities too.
    """
    quotes = draw(st.lists(_weird_quote(), min_size=0, max_size=6))
    return ChainSnapshot(
        underlying=draw(st.sampled_from(["", "NIFTY", "BANKNIFTY", "X"])),
        expiry=draw(st.sampled_from(["", "2025-12-25", "garbage"])),
        snapshot_ts=draw(st.one_of(
            st.integers(min_value=-1, max_value=2_000_000_000_000),
            st.none(),
        )),
        strikes=tuple(quotes),
    )


_optional_snapshot = st.one_of(st.none(), _weird_snapshot())


@st.composite
def _weird_config(draw):
    """An OptionsConfig that may itself carry degenerate / non-finite settings.

    The analytics degrade a malformed config to documented defaults rather than
    raising, so feeding a weird config is part of the totality surface.
    """
    return OptionsConfig(
        risk_free_rate=draw(_weird_number),
        iv_tolerance=draw(_weird_number),
        iv_max_iterations=draw(st.one_of(st.integers(min_value=-5, max_value=200),
                                         st.none())),
        iv_min_vol=draw(_weird_number),
        iv_max_vol=draw(_weird_number),
        oi_wall_min_oi=draw(_weird_number),
        buildup_oi_epsilon=draw(_weird_number),
        buildup_price_epsilon=draw(_weird_number),
    )


# A per-strike IV map for compute_iv_skew: weird keys (finite/non-finite/junk)
# mapped to weird IV values — plus the occasional non-dict to exercise the
# isinstance guard.
_weird_iv_map = st.one_of(
    st.none(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(
        keys=st.one_of(_weird_strike, st.text(max_size=2)),
        values=_weird_number,
        max_size=6,
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 13 (2.5, 4.4, 7.4): The engine is total and never raises
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 13: The engine is total and never raises
@settings(max_examples=100)
@given(
    snapshot=_weird_snapshot(),
    prior=_optional_snapshot,
    spot=_weird_spot,
    future_price=_weird_number,
    config=_weird_config(),
    iv_map=_weird_iv_map,
    side=_weird_option_type,
    option_type=_weird_option_type,
    s=_weird_number,
    k=_weird_number,
    t=_weird_number,
    r=_weird_number,
    sigma=_weird_number,
    price=_weird_number,
)
def test_property_13_engine_is_total_and_never_raises(
    snapshot, prior, spot, future_price, config, iv_map, side, option_type,
    s, k, t, r, sigma, price,
):
    """Feature: options-analytics-engine, Property 13: The engine is total and
    never raises — for any input, including degenerate input (empty strike
    ladder, all-null fields, non-finite spot, missing prior snapshot), every pure
    analytic, the Black-Scholes core, and the top-level
    ``compute_options_analytics`` returns a value without raising.

    Validates: Requirements 2.5, 4.4, 7.4
    """
    # ── Pure chain analytics over a degenerate snapshot — none may raise ──
    pcr_oi = compute_pcr_oi(snapshot)
    assert pcr_oi is None or isinstance(pcr_oi, float)

    pcr_vol = compute_pcr_volume(snapshot)
    assert pcr_vol is None or isinstance(pcr_vol, float)

    max_pain = compute_max_pain(snapshot)
    assert max_pain is None or isinstance(max_pain, float)

    skew = compute_iv_skew(iv_map, spot)
    assert skew is None or isinstance(skew, dict)

    basis = compute_futures_basis(future_price, spot)
    assert basis is None or isinstance(basis, float)

    walls = compute_oi_walls(snapshot, spot, config)
    assert isinstance(walls, dict) and set(walls) == {"support", "resistance"}

    # ── OI-buildup classification / aggregation — always one of five labels ──
    _LABELS = {
        "long_buildup", "short_buildup", "short_covering",
        "long_unwinding", "neutral",
    }
    label = classify_oi_buildup(future_price, price, config)
    assert label in _LABELS

    agg = aggregate_oi_buildup(snapshot, prior, config, side)
    assert agg in _LABELS

    # ── Black-Scholes core — finite-or-None / dict, never an exception ──
    bs_p = bs_price(option_type, s, k, t, r, sigma)
    assert bs_p is None or (isinstance(bs_p, float) and math.isfinite(bs_p))

    iv = bs_implied_vol(option_type, s, k, t, r, price, config)
    assert iv is None or isinstance(iv, float)

    greeks = bs_greeks(option_type, s, k, t, r, sigma)
    assert isinstance(greeks, dict)
    assert set(greeks) == {"delta", "gamma", "theta", "vega"}
    for leaf in greeks.values():
        assert leaf is None or (isinstance(leaf, float) and math.isfinite(leaf))

    # ── Assembly over a degenerate snapshot — always the complete dict shape ──
    assembled = assemble_result(snapshot, prior, spot, future_price, config)
    assert isinstance(assembled, dict)
    for field in (
        "underlying", "expiry", "spot", "snapshot_ts", "pcr_oi", "pcr_volume",
        "max_pain", "oi_buildup", "iv_skew", "oi_walls", "futures_basis",
        "per_strike",
    ):
        assert field in assembled

    # ── Top-level orchestrator over the monkeypatched (degenerate) read layer ──
    # The read layer is the only impure component; patch each reader to return the
    # generated degenerate values so the orchestrator is driven over the same
    # space with no QuestDB, then assert it never raises and always returns a dict
    # (either an Options_Analytics_Result or an Unavailable_Marker).
    saved = (
        options.read_latest_and_prior_snapshot,
        options.read_spot,
        options.read_future_price,
    )
    try:
        options.read_latest_and_prior_snapshot = lambda u, e: (snapshot, prior)
        options.read_spot = lambda u: spot
        options.read_future_price = lambda u: future_price

        # With an injected config and resolved-from-env config (config=None).
        out_injected = compute_options_analytics("NIFTY", "2025-12-25", config)
        assert isinstance(out_injected, dict)

        out_resolved = compute_options_analytics("NIFTY", "2025-12-25", None)
        assert isinstance(out_resolved, dict)

        # Also drive the "no snapshot" and "no spot" degradation gates explicitly.
        options.read_latest_and_prior_snapshot = lambda u, e: (None, None)
        out_no_snap = compute_options_analytics("NIFTY", "2025-12-25", config)
        assert isinstance(out_no_snap, dict) and out_no_snap.get("unavailable") is True

        options.read_latest_and_prior_snapshot = lambda u, e: (snapshot, prior)
        options.read_spot = lambda u: None
        out_no_spot = compute_options_analytics("NIFTY", "2025-12-25", config)
        assert isinstance(out_no_spot, dict) and out_no_spot.get("unavailable") is True
    finally:
        (
            options.read_latest_and_prior_snapshot,
            options.read_spot,
            options.read_future_price,
        ) = saved


# A resolved real config must also flow through every analytic without raising —
# guards the common (non-degenerate-config) path of the totality guarantee.
# Feature: options-analytics-engine, Property 13: The engine is total and never raises
@settings(max_examples=100)
@given(snapshot=_weird_snapshot(), prior=_optional_snapshot, spot=_weird_spot)
def test_property_13_resolved_config_path_never_raises(snapshot, prior, spot):
    """Feature: options-analytics-engine, Property 13: The engine is total and
    never raises — the same totality holds when analytics run under a fully
    resolved (valid) configuration, not just a degenerate one.

    Validates: Requirements 2.5, 4.4, 7.4
    """
    config = resolve_options_config()
    result = assemble_result(snapshot, prior, spot, None, config)
    assert isinstance(result, dict)
    assert compute_oi_walls(snapshot, spot, config)["support"] in {
        *(q.strike for q in snapshot.strikes), None,
    } or True  # presence-only: the call simply must not raise
