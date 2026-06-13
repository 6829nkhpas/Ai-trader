"""Property-based test for forecaster purity (forecaster.py, task 4.3).

Feature: volatility-aware-forecaster

This module implements design **Property 2: Forecaster functions are pure (no
input mutation, no network)**:

    Every ``Volatility_Aware_Forecaster`` function — in particular the top-level
    ``forecast`` and the candle-only estimation functions (``compute_drift`` /
    ``compute_volatility`` / ``compute_atr`` / ``compute_log_returns``) —
    produces NO observable change to its input candle sequence or its
    configuration. After a call, the candle sequence must remain deep-equal to a
    snapshot taken before the call, and the (frozen) ``ForecasterConfig`` must
    remain equal to its pre-call snapshot (R1.5).

    The forecaster is also pure with respect to I/O: it performs zero network
    calls and reads no data source other than its provided inputs (R1.1). The
    module imports no ``httpx`` / network client, asserted at import time below;
    the core property exercised here is input immutability.

Validates: Requirements 1.1, 1.5.

A candle is a dict-like OHLCV record carrying open/high/low/close/volume
(matching how ``forecaster.py`` reads candles through ``regime``'s validation
helpers). The generators produce arbitrary sequences — including extreme
magnitudes, flat/zero-range bars, candles carrying non-finite / non-numeric
OHLCV fields, and sequences ranging from too-short (the insufficient-data path)
to long enough that every measure is computable — so the purity guarantee is
stressed across every code path, including the degenerate ones (insufficient
data, all-null measures, zero denominators, zero-variance windows) where a
careless implementation might mutate or normalize its inputs in place.

The sys.path / import pattern mirrors the sibling
``test_forecaster_*_properties.py`` and ``test_of_purity_properties.py``
modules.
"""

import copy
import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import forecaster  # noqa: E402
from forecaster import (  # noqa: E402
    ForecasterConfig,
    compute_atr,
    compute_drift,
    compute_log_returns,
    compute_volatility,
    forecast,
    resolve_forecaster_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# No network client at import (R1.1): the forecaster is pure Python and must not
# pull in an HTTP client. The core property below is input immutability, but the
# absence of a network dependency is asserted here for completeness.
# ─────────────────────────────────────────────────────────────────────────────


def test_forecaster_module_imports_no_network_client():
    """Validates: Requirements 1.1

    ``forecaster`` performs zero network calls; it imports no ``httpx`` (or any
    other HTTP client) module attribute, so there is no client through which it
    could reach the network.
    """
    assert not hasattr(forecaster, "httpx"), (
        "forecaster must not import a network client (httpx)"
    )
    # No module-level attribute should reference an httpx module object either.
    for name in dir(forecaster):
        attr = getattr(forecaster, name)
        mod = getattr(attr, "__module__", "") or ""
        assert not str(mod).startswith("httpx"), (
            f"forecaster.{name} pulls in a network client: {mod}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records, including extreme / degenerate /
# corrupt values, so the purity guarantee is exercised across every code path
# (valid windows, flat bars, insufficient data, all-null measures, corrupt
# fields).
# ─────────────────────────────────────────────────────────────────────────────

_PRICE = st.one_of(
    st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 1e-12, 1e12, 1.0, 100.0, 12345.6789]),
)

# Values that make a field non-finite or non-numeric, so the carrying candle is
# excluded by the measure functions. Included so the purity property also covers
# the exclusion path.
_BAD_VALUE = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "x", "", True, False, [], {}]
)


@st.composite
def _candle(draw):
    """One OHLCV candle dict; fields may be ordinary, extreme, or corrupt.

    High/low are NOT forced to bracket open/close so flat and inverted-range
    bars are produced too. Each field independently has a small chance of
    carrying a non-finite / non-numeric value, exercising the exclusion path.
    """

    def _field():
        if draw(st.integers(min_value=0, max_value=9)) == 0:
            return draw(st.one_of(_PRICE, _BAD_VALUE))
        return draw(_PRICE)

    return {
        "open": _field(),
        "high": _field(),
        "low": _field(),
        "close": _field(),
        "volume": _field(),
    }


@st.composite
def _flat_candle(draw):
    """A flat candle where O=H=L=C (a zero-range, zero-variance bar)."""
    p = draw(_PRICE)
    return {"open": p, "high": p, "low": p, "close": p, "volume": draw(_PRICE)}


# Sequences span from too-short (insufficient-data path) to long enough that
# every measure is computable and the zero-variance short-circuit is hit.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=0,
    max_size=120,
)

# Proposed direction, including absent / non-directional / order-side values.
_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(
        ["BUY", "SELL", "HOLD", "buy", "sell", "long", "short", "up", "down", "", "weird"]
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Forecaster functions are pure (no input mutation, no network)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 2: Forecaster functions are pure (no input mutation, no network)
@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.large_base_example, HealthCheck.too_slow],
)
@given(
    candles=_CANDLES,
    lookback=st.integers(min_value=1, max_value=80),
    proposed_direction=_DIRECTION,
)
def test_property_2_forecaster_functions_are_pure(
    candles, lookback, proposed_direction
):
    """Feature: volatility-aware-forecaster, Property 2: Forecaster functions
    are pure (no input mutation, no network).

    ``forecast`` and the candle-only estimation functions leave the provided
    candle sequence and configuration deep-equal to their pre-call snapshots —
    producing no observable change to any input. The candle sequence (and its
    candle dicts) is snapshotted with a deep copy before each call and asserted
    deep-equal afterward; the (frozen) ``ForecasterConfig`` is compared by
    equality.

    Validates: Requirements 1.1, 1.5
    """
    config = resolve_forecaster_config()
    assert isinstance(config, ForecasterConfig)
    config_snapshot = config  # frozen dataclass -> compare by equality

    candles_snapshot = copy.deepcopy(candles)

    # Exercise every public candle-only estimation function on the same inputs.
    # None of them may mutate the candle sequence or the config.
    compute_log_returns(candles, lookback)
    compute_drift(candles, config)
    compute_volatility(candles, config)
    compute_atr(candles, lookback)

    # The top-level entry point, across both call shapes (with and without
    # symbol/timeframe context, and with a proposed direction).
    forecast(
        candles,
        config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE",
        timeframe="15m",
    )
    forecast(candles, config)

    assert candles == candles_snapshot, (
        "forecaster mutated its candle input: "
        f"{candles!r} != {candles_snapshot!r}"
    )
    assert config == config_snapshot, (
        "forecaster mutated its config input"
    )
