"""Property-based tests for regime threshold resolution (regime.py, task 1.x).

Feature: regime-detection-gate

These Hypothesis properties exercise ``regime.resolve_regime_config`` — the single,
deterministic threshold resolver shared by the live tool path and the backtest
path. Task 1.2 covers Property 28 (per-threshold default fallback): every regime
threshold falls back to its documented default when its environment variable is
unset, empty, unparseable, or out of range.

Environment isolation is handled with an explicit save/restore context manager
(``_regime_env``) rather than the ``monkeypatch`` fixture, because Hypothesis runs
many examples per test function and a function-scoped fixture would not reset
between examples. ``_regime_env`` clears every ``REGIME_*`` variable, applies the
single override under test, and fully restores the prior environment afterward,
so each generated example is isolated and the suite is deterministic.
"""

import contextlib
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import regime  # noqa: E402
from regime import (  # noqa: E402
    DEFAULT_ADX_PERIOD,
    DEFAULT_ADX_TREND_CUTOFF,
    DEFAULT_BB_PERIOD,
    DEFAULT_CHOP_PERIOD,
    DEFAULT_CHOP_RANGING_CUTOFF,
    DEFAULT_MIN_CANDLES,
    DEFAULT_VOL_HIGH_PCTL,
    DEFAULT_VOL_LOW_PCTL,
    DEFAULT_VOL_PCTL_WINDOW,
    DEFAULT_VOL_PERIOD,
    ENV_ADX_PERIOD,
    ENV_ADX_TREND_CUTOFF,
    ENV_BB_PERIOD,
    ENV_CHOP_PERIOD,
    ENV_CHOP_RANGING_CUTOFF,
    ENV_MIN_CANDLES,
    ENV_VOL_HIGH_PCTL,
    ENV_VOL_LOW_PCTL,
    ENV_VOL_PCTL_WINDOW,
    ENV_VOL_PERIOD,
    resolve_regime_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# Threshold catalogue: (env var name, RegimeConfig attribute, documented default)
# ─────────────────────────────────────────────────────────────────────────────

# Float thresholds, all bounded to the [0.0, 100.0] valid range (R11.1).
FLOAT_SPECS = [
    (ENV_ADX_TREND_CUTOFF, "adx_trend_cutoff", DEFAULT_ADX_TREND_CUTOFF),
    (ENV_CHOP_RANGING_CUTOFF, "chop_ranging_cutoff", DEFAULT_CHOP_RANGING_CUTOFF),
    (ENV_VOL_LOW_PCTL, "vol_low_pctl", DEFAULT_VOL_LOW_PCTL),
    (ENV_VOL_HIGH_PCTL, "vol_high_pctl", DEFAULT_VOL_HIGH_PCTL),
]

# Integer thresholds, all with a minimum valid value of 1 and no upper bound (R11.1).
INT_SPECS = [
    (ENV_MIN_CANDLES, "min_candles", DEFAULT_MIN_CANDLES),
    (ENV_ADX_PERIOD, "adx_period", DEFAULT_ADX_PERIOD),
    (ENV_CHOP_PERIOD, "chop_period", DEFAULT_CHOP_PERIOD),
    (ENV_VOL_PERIOD, "vol_period", DEFAULT_VOL_PERIOD),
    (ENV_VOL_PCTL_WINDOW, "vol_pctl_window", DEFAULT_VOL_PCTL_WINDOW),
    (ENV_BB_PERIOD, "bb_period", DEFAULT_BB_PERIOD),
]

# Every regime env var name; cleared before each example for isolation.
ALL_ENV_NAMES = [spec[0] for spec in FLOAT_SPECS] + [spec[0] for spec in INT_SPECS]


@contextlib.contextmanager
def _regime_env(overrides):
    """Run with a clean REGIME_* environment plus ``overrides``, then restore.

    Clears every regime env var, applies the given overrides, yields, and on exit
    restores the exact prior values (including re-deleting vars that were unset).
    Keeps each Hypothesis example isolated and the suite deterministic.
    """
    saved = {name: os.environ.get(name) for name in ALL_ENV_NAMES}
    try:
        for name in ALL_ENV_NAMES:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name in ALL_ENV_NAMES:
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


# ─────────────────────────────────────────────────────────────────────────────
# Strategies: invalid raw env values that MUST trigger the documented default
# ─────────────────────────────────────────────────────────────────────────────

_EMPTY = st.sampled_from(["", " ", "   ", "\t", "\n", "  \t  ", "\r\n"])


@st.composite
def _invalid_float_raw(draw):
    """A raw string that must NOT be accepted as a valid float threshold."""
    kind = draw(st.sampled_from(["empty", "unparseable", "nonfinite", "out_of_range"]))
    if kind == "empty":
        return draw(_EMPTY)
    if kind == "unparseable":
        return draw(
            st.sampled_from(
                ["abc", "x", "12.3.4", "1,5", "true", "none", "$5", "1e", "--3",
                 "0x10", "1 2", "3..", "+", "."]
            )
        )
    if kind == "nonfinite":
        # Parse as float() but are non-finite -> resolver rejects them.
        return draw(st.sampled_from(["nan", "NaN", "inf", "-inf", "Infinity", "+inf"]))
    # out_of_range: a finite float strictly outside [0.0, 100.0].
    value = draw(
        st.one_of(
            st.floats(min_value=100.0001, max_value=1e9,
                      allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1e9, max_value=-0.0001,
                      allow_nan=False, allow_infinity=False),
        )
    )
    return repr(value)


@st.composite
def _invalid_int_raw(draw):
    """A raw string that must NOT be accepted as a valid integer threshold."""
    kind = draw(st.sampled_from(["empty", "unparseable", "out_of_range"]))
    if kind == "empty":
        return draw(_EMPTY)
    if kind == "unparseable":
        # Includes float-like strings: int("1.5") raises -> falls back to default.
        return draw(
            st.sampled_from(
                ["abc", "1.5", "1e3", "--", "0x10", "3.0", "1,000", "ten", "+-1",
                 "x", "12.0", " 5 5 "]
            )
        )
    # out_of_range: an integer below the minimum valid value of 1.
    value = draw(st.integers(min_value=-(10 ** 9), max_value=0))
    return str(value)


@st.composite
def _threshold_fallback_case(draw):
    """Pick one threshold and either leave it unset or give it an invalid value.

    Returns ``(env_name, attr, default, override)`` where ``override`` is ``None``
    (the var is left unset) or a raw string that must be rejected. Covers the
    unset (R11.2), empty (R11.2), unparseable (R11.3), and out-of-range (R11.4)
    fallback paths in a single property.
    """
    if draw(st.booleans()):
        env_name, attr, default = draw(st.sampled_from(FLOAT_SPECS))
        raw_strategy = _invalid_float_raw()
    else:
        env_name, attr, default = draw(st.sampled_from(INT_SPECS))
        raw_strategy = _invalid_int_raw()

    if draw(st.booleans()):
        override = None  # unset
    else:
        override = draw(raw_strategy)

    return env_name, attr, default, override


# ─────────────────────────────────────────────────────────────────────────────
# Property 28: Each threshold falls back to its documented default
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 28
@settings(max_examples=200, deadline=None)
@given(case=_threshold_fallback_case())
def test_property_28_each_threshold_falls_back_to_default(case):
    """Validates: Requirements 11.1, 11.2, 11.3, 11.4

    For any single regime threshold whose environment variable is unset, empty,
    unparseable, or out of its documented valid range, ``resolve_regime_config``
    resolves that threshold to its documented default value, without raising.
    """
    env_name, attr, default, override = case

    overrides = {} if override is None else {env_name: override}
    with _regime_env(overrides):
        config = resolve_regime_config()  # must never raise (R11.3, R11.4)

    resolved = getattr(config, attr)
    assert resolved == default, (
        f"{env_name}={override!r} should fall back to default {default!r} "
        f"for attribute {attr!r}, got {resolved!r}"
    )
    # The default must itself be the correct numeric type for the threshold.
    assert type(resolved) is type(default)
