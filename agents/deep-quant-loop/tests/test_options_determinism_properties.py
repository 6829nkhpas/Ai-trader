"""Property-based tests for engine determinism and purity (options.py, task 9.6).

Feature: options-analytics-engine

This Hypothesis property exercises the whole-engine purity contract that every
pure analytic in :mod:`options` is built on: the orchestration entry point
(:func:`options.assemble_result`) and the configuration resolver
(:func:`options.resolve_options_config`) are **deterministic** (identical inputs
yield identical output) and **pure** (no invocation observably mutates its input
snapshots or configuration).

  * Property 14 (1.6, 8.3, 9.1, 9.2) — The engine is deterministic and pure: for
                       any fixed snapshots, spot, and configuration, repeated
                       invocations of ``assemble_result`` return identical
                       output, and no invocation observably mutates its input
                       snapshots or configuration; and ``resolve_options_config``
                       under a fixed environment returns identical configs.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import (  # noqa: E402
    ChainSnapshot,
    OptionsConfig,
    StrikeQuote,
    assemble_result,
    resolve_options_config,
    DEFAULT_RISK_FREE_RATE,
    DEFAULT_IV_TOLERANCE,
    DEFAULT_IV_MAX_ITERATIONS,
    DEFAULT_IV_MIN_VOL,
    DEFAULT_IV_MAX_VOL,
    DEFAULT_OI_WALL_MIN_OI,
    DEFAULT_BUILDUP_OI_EPSILON,
    DEFAULT_BUILDUP_PRICE_EPSILON,
    ENV_RISK_FREE_RATE,
    ENV_IV_TOLERANCE,
    ENV_IV_MAX_ITERATIONS,
    ENV_IV_MIN_VOL,
    ENV_IV_MAX_VOL,
    ENV_OI_WALL_MIN_OI,
    ENV_BUILDUP_OI_EPSILON,
    ENV_BUILDUP_PRICE_EPSILON,
)


# ── Smart generators spanning the realistic input space ───────────────────────
# Numeric chain fields are Optional and may be non-finite/absent (None, NaN,
# ±inf) which the engine must tolerate by excluding them — exercising the same
# input space the pure functions guarantee against.
_finite_nonneg = st.floats(
    min_value=0.0, max_value=1_000_000.0,
    allow_nan=False, allow_infinity=False,
)
_optional_field = st.one_of(
    st.none(),
    _finite_nonneg,
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)
# Per-leg prices: finite positives are the normal case; absent/non-finite legs
# must degrade to a null IV/Greek rather than raise.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0,
    allow_nan=False, allow_infinity=False,
)
_optional_price = st.one_of(st.none(), _finite_price, st.just(float("nan")))

_strike_value = st.floats(
    min_value=1.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)


@st.composite
def _strike_quotes_for(draw, strikes):
    """Build ascending ``StrikeQuote`` rows for a sorted list of strikes."""
    quotes = []
    for k in sorted(strikes):
        quotes.append(
            StrikeQuote(
                strike=k,
                ce_price=draw(_optional_price),
                pe_price=draw(_optional_price),
                ce_oi=draw(_optional_field),
                pe_oi=draw(_optional_field),
                ce_volume=draw(_optional_field),
                pe_volume=draw(_optional_field),
            )
        )
    return tuple(quotes)


@st.composite
def _chain_snapshots(draw):
    """A ChainSnapshot with a possibly-empty ascending ladder of distinct strikes."""
    strikes = draw(st.lists(_strike_value, min_size=0, max_size=10, unique=True))
    return ChainSnapshot(
        underlying=draw(st.sampled_from(["NIFTY", "BANKNIFTY", "TEST"])),
        expiry=draw(st.sampled_from(["2025-12-25", "2026-01-29", ""])),
        snapshot_ts=draw(st.integers(min_value=0, max_value=2_000_000_000_000)),
        strikes=draw(_strike_quotes_for(strikes)),
    )


@st.composite
def _options_configs(draw):
    """A valid resolved ``OptionsConfig`` (in-range fields, iv_min_vol < iv_max_vol)."""
    iv_min_vol = draw(st.floats(min_value=0.0, max_value=1.0,
                                allow_nan=False, allow_infinity=False))
    iv_max_vol = draw(st.floats(min_value=iv_min_vol + 0.01, max_value=5.0,
                                allow_nan=False, allow_infinity=False))
    return OptionsConfig(
        risk_free_rate=draw(st.floats(min_value=0.0, max_value=1.0,
                                      allow_nan=False, allow_infinity=False)),
        iv_tolerance=draw(st.floats(min_value=1e-8, max_value=1e-2,
                                    allow_nan=False, allow_infinity=False)),
        iv_max_iterations=draw(st.integers(min_value=1, max_value=200)),
        iv_min_vol=iv_min_vol,
        iv_max_vol=iv_max_vol,
        oi_wall_min_oi=draw(_finite_nonneg),
        buildup_oi_epsilon=draw(_finite_nonneg),
        buildup_price_epsilon=draw(_finite_nonneg),
    )


_spot_value = st.floats(
    min_value=1.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)
_optional_future = st.one_of(st.none(), _spot_value, st.just(float("nan")))


def _structurally_equal(a, b):
    """NaN-safe deep equality for the engine's finite-or-None result trees.

    The engine sanitizes every numeric leaf to a finite number or ``None``, so a
    plain ``==`` already suffices; this helper additionally treats two ``NaN``
    leaves as equal so the determinism assertion never spuriously fails on a
    leaked non-finite value (it would instead surface via the sanitization
    property test).
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_structurally_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_structurally_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 14 (1.6, 8.3, 9.1, 9.2): The engine is deterministic and pure
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 14: The engine is deterministic and pure
@settings(max_examples=100)
@given(
    latest=_chain_snapshots(),
    prior=st.one_of(st.none(), _chain_snapshots()),
    spot=_spot_value,
    future_price=_optional_future,
    config=_options_configs(),
)
def test_property_14_assemble_result_is_deterministic_and_pure(
    latest, prior, spot, future_price, config
):
    """Feature: options-analytics-engine, Property 14: The engine is deterministic
    and pure — for any fixed snapshots, spot, and configuration, repeated
    invocations of ``assemble_result`` return identical output, and no invocation
    observably mutates its input snapshots or configuration.

    Validates: Requirements 1.6, 8.3, 9.1, 9.2
    """
    # Deep snapshots of every input taken BEFORE any invocation, to detect any
    # observable mutation after the calls return.
    latest_before = copy.deepcopy(latest)
    prior_before = copy.deepcopy(prior)
    config_before = copy.deepcopy(config)

    # Determinism: two invocations on identical inputs yield identical output.
    result_a = assemble_result(latest, prior, spot, future_price, config)
    result_b = assemble_result(latest, prior, spot, future_price, config)

    assert _structurally_equal(result_a, result_b)
    # repr equality is a strong, NaN-insensitive cross-check of identical output.
    assert repr(result_a) == repr(result_b)

    # Purity: no invocation observably mutated its input snapshots or config
    # (frozen dataclasses — verify equality of the deep snapshot taken before).
    assert latest == latest_before
    assert prior == prior_before
    assert config == config_before


# Feature: options-analytics-engine, Property 14: The engine is deterministic and pure
@settings(max_examples=100)
@given(
    risk_free_rate=st.one_of(st.none(), st.text(max_size=12)),
    iv_tolerance=st.one_of(st.none(), st.text(max_size=12)),
    iv_max_iterations=st.one_of(st.none(), st.text(max_size=12)),
    iv_min_vol=st.one_of(st.none(), st.text(max_size=12)),
    iv_max_vol=st.one_of(st.none(), st.text(max_size=12)),
    oi_wall_min_oi=st.one_of(st.none(), st.text(max_size=12)),
    buildup_oi_epsilon=st.one_of(st.none(), st.text(max_size=12)),
    buildup_price_epsilon=st.one_of(st.none(), st.text(max_size=12)),
)
def test_property_14_resolve_options_config_is_deterministic_under_fixed_env(
    risk_free_rate,
    iv_tolerance,
    iv_max_iterations,
    iv_min_vol,
    iv_max_vol,
    oi_wall_min_oi,
    buildup_oi_epsilon,
    buildup_price_epsilon,
):
    """Feature: options-analytics-engine, Property 14: The engine is deterministic
    and pure — under a fixed environment ``resolve_options_config`` returns
    identical configurations on repeated invocations (Requirement 8.3), spanning
    unset, empty, unparseable, and parseable env values.

    Validates: Requirements 1.6, 8.3, 9.1, 9.2
    """
    overrides = {
        ENV_RISK_FREE_RATE: risk_free_rate,
        ENV_IV_TOLERANCE: iv_tolerance,
        ENV_IV_MAX_ITERATIONS: iv_max_iterations,
        ENV_IV_MIN_VOL: iv_min_vol,
        ENV_IV_MAX_VOL: iv_max_vol,
        ENV_OI_WALL_MIN_OI: oi_wall_min_oi,
        ENV_BUILDUP_OI_EPSILON: buildup_oi_epsilon,
        ENV_BUILDUP_PRICE_EPSILON: buildup_price_epsilon,
    }
    # Save and apply a fixed environment for the duration of this example.
    saved = {name: os.environ.get(name) for name in overrides}
    try:
        for name, value in overrides.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

        config_a = resolve_options_config()
        config_b = resolve_options_config()

        # Determinism under a fixed environment: identical configs.
        assert config_a == config_b

        # The resolver does not mutate the environment it read.
        for name, value in overrides.items():
            assert os.environ.get(name) == (value if value is not None else None)
    finally:
        # Restore the original environment.
        for name, original in saved.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
