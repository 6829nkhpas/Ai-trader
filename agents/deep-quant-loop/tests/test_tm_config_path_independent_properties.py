"""Property-based test for path-independent resolution (trade_manager.py, task 1.3).

Feature: trade-management

This module implements design **Property 27: Configuration resolution is
path-independent**:

    For identical environment-variable values, ``resolve_trade_manager_config``
    returns identical resolved ``TradeManagerConfig`` values no matter how many
    times it is called — in particular the journal-scoring path and the
    backtest path (both of which call the same function) resolve to the same
    configuration and the same documented defaults.

Validates: Requirements 13.5.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_path_independent_properties.py`` and
``tests/test_fc_config_path_independent_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import trade_manager  # noqa: E402
from trade_manager import TradeManagerConfig, resolve_trade_manager_config  # noqa: E402

# Every TM_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_TM_ENV_VARS = (
    trade_manager.ENV_DEFAULT_FIRST_TARGET_R,
    trade_manager.ENV_DEFAULT_FIRST_TARGET_FRACTION,
    trade_manager.ENV_DEFAULT_BREAKEVEN_TRIGGER_R,
    trade_manager.ENV_DEFAULT_TRAIL_ATR_MULTIPLE,
    trade_manager.ENV_MIN_BLENDED_REWARD_TO_RISK,
)


@contextmanager
def _tm_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every TM_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_TM_ENV_VARS}
    try:
        for name in _ALL_TM_ENV_VARS:
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


# ── Value strategies ──────────────────────────────────────────────────────────
# All five TM_* parameters are floats. A deliberately broad mix of values per
# env var exercises every resolution category: valid in-range values,
# out-of-range values (below the lower bound, above the upper bound, and exactly
# at an exclusive boundary), unparseable garbage, empty/whitespace, non-finite,
# and "unset" (``None``). The resolution must be identical no matter which
# category a value falls into, so we exercise all of them.
_float_value = st.one_of(
    st.none(),                                                  # unset
    st.just(""),                                                # empty
    st.just("   "),                                             # whitespace-only
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),    # unparseable garbage
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),        # non-finite
    st.sampled_from(["0", "0.0", "-0.0"]),                      # exclusive-lower boundary
    st.floats(min_value=-10.0, max_value=150.0, allow_nan=False,
              allow_infinity=False).map(repr),                  # spans valid + out-of-range
)

_env_assignment = st.fixed_dictionaries(
    {
        trade_manager.ENV_DEFAULT_FIRST_TARGET_R: _float_value,
        trade_manager.ENV_DEFAULT_FIRST_TARGET_FRACTION: _float_value,
        trade_manager.ENV_DEFAULT_BREAKEVEN_TRIGGER_R: _float_value,
        trade_manager.ENV_DEFAULT_TRAIL_ATR_MULTIPLE: _float_value,
        trade_manager.ENV_MIN_BLENDED_REWARD_TO_RISK: _float_value,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 27 (task 1.3): Configuration resolution is path-independent
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 27: Configuration resolution is path-independent
@settings(max_examples=200, deadline=None)
@given(assignment=_env_assignment)
def test_property_27_resolution_is_path_independent(assignment):
    """Feature: trade-management, Property 27: Configuration resolution is
    path-independent — for identical environment-variable values,
    ``resolve_trade_manager_config`` returns identical resolved
    ``TradeManagerConfig`` values across repeated/independent calls (modeling
    the journal-scoring path and the backtest path), with identical documented
    defaults.

    Validates: Requirements 13.5
    """
    # ``None`` means "leave the var unset" so the unset-fallback path is exercised.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _tm_env(overrides):
        # The journal-scoring path resolves the config.
        journal_path_config = resolve_trade_manager_config()
        # The backtest path resolves the config from the SAME environment.
        backtest_path_config = resolve_trade_manager_config()
        # A third call guards general determinism / idempotency.
        third_config = resolve_trade_manager_config()

    # The resolver never raised and produced fully-formed configs.
    assert isinstance(journal_path_config, TradeManagerConfig)
    assert isinstance(backtest_path_config, TradeManagerConfig)
    assert isinstance(third_config, TradeManagerConfig)

    # Path-independence: the journal path and the backtest path resolve identically.
    assert journal_path_config == backtest_path_config
    # Determinism: every call returns the same value.
    assert journal_path_config == third_config

    # Field-level equality (covers every resolved parameter explicitly, so a
    # failure pinpoints the divergent field rather than the whole dataclass).
    assert journal_path_config.default_first_target_r == backtest_path_config.default_first_target_r
    assert (
        journal_path_config.default_first_target_fraction
        == backtest_path_config.default_first_target_fraction
    )
    assert (
        journal_path_config.default_breakeven_trigger_r
        == backtest_path_config.default_breakeven_trigger_r
    )
    assert (
        journal_path_config.default_trail_atr_multiple
        == backtest_path_config.default_trail_atr_multiple
    )
    assert (
        journal_path_config.min_blended_reward_to_risk
        == backtest_path_config.min_blended_reward_to_risk
    )
