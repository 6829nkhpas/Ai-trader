"""Property-based tests for regime threshold resolution (regime.py, tasks 1.2–1.4).

Feature: regime-detection-gate

These Hypothesis properties exercise the deterministic threshold resolver
(:func:`regime.resolve_regime_config`) across the env-var input space. They
complement example-based unit tests by asserting universal invariants:

  * Property 29 (1.3) — the strict low < high volatility-percentile ordering is
                        enforced: when ``vol_low_pctl >= vol_high_pctl`` BOTH
                        cutoffs revert to their documented defaults together;
                        valid orderings (low < high) are preserved.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import regime  # noqa: E402
from regime import (  # noqa: E402
    DEFAULT_VOL_HIGH_PCTL,
    DEFAULT_VOL_LOW_PCTL,
    ENV_VOL_HIGH_PCTL,
    ENV_VOL_LOW_PCTL,
    resolve_regime_config,
)

# Every REGIME_* env var the resolver reads. We clear all of them inside the
# isolation context so only the two cutoffs under test influence the result and
# the remaining thresholds fall back to their documented defaults deterministically.
_ALL_REGIME_ENV_VARS = (
    regime.ENV_ADX_TREND_CUTOFF,
    regime.ENV_CHOP_RANGING_CUTOFF,
    regime.ENV_VOL_LOW_PCTL,
    regime.ENV_VOL_HIGH_PCTL,
    regime.ENV_MIN_CANDLES,
    regime.ENV_ADX_PERIOD,
    regime.ENV_CHOP_PERIOD,
    regime.ENV_VOL_PERIOD,
    regime.ENV_VOL_PCTL_WINDOW,
    regime.ENV_BB_PERIOD,
)


@contextmanager
def _regime_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every REGIME_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_REGIME_ENV_VARS}
    try:
        for name in _ALL_REGIME_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


# In-range percentile values (0.0–100.0); str() of a Python float round-trips
# exactly through float(), so the resolved cutoff equals the generated value when
# the ordering is valid.
_pctl = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)


# ─────────────────────────────────────────────────────────────────────────────
# Property 29 (1.3): Volatility-percentile ordering is enforced
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=300)
@given(low=_pctl, high=_pctl)
def test_property_29_vol_pctl_ordering_enforced(low, high):
    """Feature: regime-detection-gate, Property 29: Volatility-percentile
    ordering is enforced — when the resolved ``vol_low_pctl >= vol_high_pctl``,
    BOTH cutoffs revert to their documented defaults together; a strictly valid
    ordering (low < high) is preserved verbatim.

    Validates: Requirements 11.5
    """
    with _regime_env(
        {ENV_VOL_LOW_PCTL: repr(low), ENV_VOL_HIGH_PCTL: repr(high)}
    ):
        config = resolve_regime_config()

    if low < high:
        # Strictly valid ordering: both cutoffs preserved exactly as provided.
        assert config.vol_low_pctl == low
        assert config.vol_high_pctl == high
        # The preserved ordering still satisfies the strict invariant.
        assert config.vol_low_pctl < config.vol_high_pctl
    else:
        # low >= high (including equality): BOTH revert to their defaults,
        # never just one of them.
        assert config.vol_low_pctl == DEFAULT_VOL_LOW_PCTL
        assert config.vol_high_pctl == DEFAULT_VOL_HIGH_PCTL

    # Whatever branch was taken, the resolved cutoffs always satisfy the strict
    # ordering the resolver guarantees, and the resolver never raised.
    assert config.vol_low_pctl < config.vol_high_pctl


# ─────────────────────────────────────────────────────────────────────────────
# Property 30 (1.4): Threshold resolution is deterministic and path-independent
# ─────────────────────────────────────────────────────────────────────────────

# A single env-var value: ``None`` means "leave the variable unset"; any string
# (valid numeric, out-of-range, empty/whitespace, or unparseable garbage) is set
# verbatim. This spans the realistic input space the resolver must tolerate.
_env_value_30 = st.one_of(
    st.none(),                                                 # unset
    st.just(""),                                               # empty
    st.just("   "),                                            # whitespace-only
    st.floats(allow_nan=True, allow_infinity=True).map(repr),  # numeric (+ nan/inf)
    st.integers(min_value=-500, max_value=500).map(str),       # integer-like
    st.text(max_size=8),                                       # arbitrary garbage
)

# A complete assignment over every regime env var at once.
_env_assignments_30 = st.fixed_dictionaries(
    {name: _env_value_30 for name in _ALL_REGIME_ENV_VARS}
)


@settings(max_examples=200)
@given(assignment=_env_assignments_30)
def test_property_30_resolution_is_deterministic_and_path_independent(assignment):
    # Feature: regime-detection-gate, Property 30
    """Feature: regime-detection-gate, Property 30: Threshold resolution is
    deterministic and path-independent — for identical environment-variable
    values, ``resolve_regime_config`` returns equal ``RegimeConfig`` instances
    across repeated and independent calls (e.g. the live tool path vs the
    backtest path), regardless of call order.

    Validates: Requirements 11.6
    """
    # Only set the vars the assignment marks as present; the rest stay unset.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _regime_env(overrides):
        # "Tool path" and "backtest path" are independent call sites of the same
        # resolver. Call them repeatedly and interleaved to expose any hidden
        # ordering- or call-count-dependence.
        tool_path_first = resolve_regime_config()
        backtest_path_first = resolve_regime_config()
        tool_path_second = resolve_regime_config()
        backtest_path_second = resolve_regime_config()

        # Every resolved configuration is a RegimeConfig (the resolver never raised).
        for config in (
            tool_path_first,
            backtest_path_first,
            tool_path_second,
            backtest_path_second,
        ):
            assert isinstance(config, regime.RegimeConfig)

        # Identical env => identical config across all calls and both paths.
        assert tool_path_first == backtest_path_first
        assert tool_path_first == tool_path_second
        assert tool_path_first == backtest_path_second
        assert backtest_path_first == backtest_path_second

        # Order-independence: resolving again in a different call order yields the
        # same (identical) result every time.
        reordered = [resolve_regime_config() for _ in range(5)]
        assert all(config == tool_path_first for config in reordered)
