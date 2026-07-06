"""Property-based test that an Unavailable_Marker carries no fabricated forecast
fields (forecaster.py ``forecast``, task 4.8).

Feature: volatility-aware-forecaster

This module implements design **Property 21: An Unavailable_Marker never carries
fabricated forecast fields**:

    For every path on which ``forecast`` returns an Unavailable_Marker
    (insufficient valid candles, an all-invalid candle sequence, an empty / non-
    candle input, or the internal defensive exception path), the result flags
    ``unavailable`` with a non-empty ``reason`` and OMITS every one of the five
    projection fields — ``projected_direction`` / ``up_probability`` /
    ``expected_move_atr`` / ``forecast_confidence`` / ``forecast_alignment`` —
    rather than populating them with default / placeholder / otherwise fabricated
    values. It leaves its inputs unmodified and never raises.

Validates: Requirements 6.3.

``forecast`` builds an Unavailable_Marker via ``_forecast_unavailable`` which
carries only ``symbol`` / ``timeframe`` / ``unavailable`` / ``reason`` — it never
defaults or fabricates the projection fields (AD-5, Requirement 6.3).

The generator drives ``forecast`` down the unavailable paths the task calls out,
with NO mocking on the primary paths — the forecaster is pure:

  * ``insufficient`` — fewer *valid* (finite-OHLCV) candles than the sufficiency
                       gate (``max(min_candles, largest_lookback)``) requires
                       (R4.1), padded with dirty candles the parser excludes.
  * ``all-invalid``  — every candle carries a non-finite / non-numeric OHLCV
                       field, so the valid count is zero (R4.2 -> R4.1).
  * ``empty``        — an empty candle sequence -> zero valid candles.
  * ``weird``        — a ``None`` / non-iterable / non-candle input the parser
                       degrades to zero valid candles.
  * ``exception``    — a *sufficient* candle sequence with an internal estimator
                       forced to raise, so the marker arises from ``forecast``'s
                       defensive exception handler (the internal exception path
                       the task asks to exercise "if feasible"). This is the one
                       branch that uses a stdlib patch, because the forecaster is
                       otherwise robust enough that no plain input reaches that
                       handler.

The sys.path / import pattern mirrors the sibling
``test_forecaster_determinism_properties.py`` module: the service directory (one
level up) is prepended to ``sys.path`` so ``forecaster`` is importable when
pytest runs from anywhere.
"""

import copy
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import forecaster  # noqa: E402
from forecaster import forecast, resolve_forecaster_config  # noqa: E402

# The five projection fields an Unavailable_Marker must NEVER carry
# (Requirement 6.3): they are omitted, never defaulted or fabricated.
_PROJECTION_FIELDS = (
    "projected_direction",
    "up_probability",
    "expected_move_atr",
    "forecast_confidence",
    "forecast_alignment",
)

# Resolve config once (identical on the tool and backtest paths). The candle
# sufficiency gate the property exercises is ``max(min_candles, largest_lookback)``.
_CONFIG = resolve_forecaster_config()
_REQUIRED = max(_CONFIG.min_candles, _CONFIG.largest_lookback)

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

_PRICE = st.floats(min_value=0.5, max_value=10_000.0, allow_nan=False, allow_infinity=False)
_SPAN = st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# the parser rejects, so the carrying candle never adds to the valid count.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True, [], {}]
)


@st.composite
def _clean_candle(draw):
    """A well-formed dict-like OHLCV candle with finite fields and ``high >= low``."""
    low = draw(_PRICE)
    high = low + draw(_SPAN)
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    return {"open": open_, "high": high, "low": low, "close": close, "volume": draw(_VOLUME)}


@st.composite
def _dirty_candle(draw):
    """A candle dict carrying one non-finite/non-numeric OHLC field so the parser
    excludes it (it never contributes to the valid count, R4.2).

    Only the OHLC fields are corrupted: the forecaster's candle-sufficiency gate
    (``regime._valid_ohlc_rows``) validates open/high/low/close and does NOT
    require a finite ``volume`` (forecasting reasons over closes and ranges), so
    corrupting ``volume`` alone would leave the candle VALID and the scenario
    would not actually be under-supplied."""
    candle = draw(_clean_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _insufficient_candles(draw):
    """A candle sequence whose *valid* count is strictly below the gate (R4.1).

    ``n_valid`` clean candles (``0 <= n_valid < required``) padded with an
    arbitrary number of dirty candles (excluded), then shuffled so order carries
    no information.
    """
    n_valid = draw(st.integers(min_value=0, max_value=_REQUIRED - 1))
    candles = [draw(_clean_candle()) for _ in range(n_valid)]
    n_dirty = draw(st.integers(min_value=0, max_value=8))
    candles += [draw(_dirty_candle()) for _ in range(n_dirty)]
    return list(draw(st.permutations(candles)))


@st.composite
def _all_invalid_candles(draw):
    """A non-empty candle sequence in which every candle is dirty (R4.2 -> R4.1)."""
    n = draw(st.integers(min_value=1, max_value=15))
    return [draw(_dirty_candle()) for _ in range(n)]


# Non-candle / non-iterable inputs the parser degrades to zero valid candles.
_weird_candles = st.sampled_from([None, 12345, 3.14, "not-a-candle", True])


@st.composite
def _sufficient_candles(draw):
    """A varied random price-walk sequence comfortably above the sufficiency gate.

    Used only by the ``exception`` branch, which forces an internal estimator to
    raise so the marker arises from ``forecast``'s defensive exception handler.
    """
    n = draw(st.integers(min_value=_REQUIRED + 5, max_value=_REQUIRED + 40))
    price = draw(st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False))
    candles = []
    for _ in range(n):
        step = draw(st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False))
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)),
            0.5,
        )
        candles.append({"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0})
        price = new_price
    return candles


@st.composite
def _scenario(draw):
    """A (candles, force_exception) pair driving ``forecast`` to an Unavailable_Marker.

    Every scenario is engineered to produce a marker: the candle-shortage paths
    (``insufficient`` / ``all-invalid`` / ``empty`` / ``weird``) hit the
    sufficiency gate, and the ``exception`` path forces the defensive handler.
    """
    kind = draw(
        st.sampled_from(["insufficient", "all_invalid", "empty", "weird", "exception"])
    )
    if kind == "insufficient":
        return draw(_insufficient_candles()), False
    if kind == "all_invalid":
        return draw(_all_invalid_candles()), False
    if kind == "empty":
        return [], False
    if kind == "weird":
        return draw(_weird_candles), False
    # exception: sufficient candles, but an internal estimator forced to raise.
    return draw(_sufficient_candles()), True


# A proposed trade direction (or its absence): the marker must arise — with no
# fabricated projection field — regardless of the proposed direction.
_proposed_direction = st.one_of(
    st.none(),
    st.sampled_from(["up", "down", "buy", "sell", "long", "short", "hold", "", "BUY", "Sell"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 21: An Unavailable_Marker never carries fabricated forecast fields
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 21: An Unavailable_Marker never carries fabricated forecast fields
@settings(max_examples=200, deadline=None)
@given(scenario=_scenario(), proposed_direction=_proposed_direction)
def test_property_21_unavailable_marker_carries_no_fabricated_fields(
    scenario, proposed_direction
):
    """Validates: Requirements 6.3

    For every path that drives ``forecast`` to an Unavailable_Marker (insufficient
    valid candles, an all-invalid sequence, an empty / non-candle input, or the
    internal defensive exception path), the result flags ``unavailable`` with a
    non-empty ``reason`` and OMITS all five projection fields rather than
    fabricating them. It leaves its inputs unmodified and never raises.
    """
    candles, force_exception = scenario

    # Snapshot the inputs to confirm purity (no mutation while forecasting).
    candles_snapshot = copy.deepcopy(candles)

    # Must never raise (R4): the call itself is part of the assertion. The
    # ``exception`` branch forces an internal estimator to raise so the marker
    # arises from ``forecast``'s defensive handler (the internal exception path).
    if force_exception:
        with mock.patch.object(
            forecaster, "compute_drift", side_effect=RuntimeError("forced estimator failure")
        ):
            result = forecast(
                candles, _CONFIG, proposed_direction=proposed_direction,
                symbol="RELIANCE", timeframe="15m",
            )
    else:
        result = forecast(
            candles, _CONFIG, proposed_direction=proposed_direction,
            symbol="RELIANCE", timeframe="15m",
        )

    # The forecaster must always return a dict.
    assert isinstance(result, dict), f"forecast result is not a dict: {result!r}"

    # Every scenario here is engineered to be unavailable. The core assertion is
    # guarded by ``unavailable is True`` per the task, but we additionally require
    # the marker to actually arise so the property exercises the intended paths.
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker, got: {result!r}"
    )

    # An honest marker must cite a (non-empty) reason for being unavailable.
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker is missing a non-empty reason: {result!r}"
    )

    # Core assertion: whenever ``forecast`` returns an Unavailable_Marker it must
    # OMIT every one of the five projection fields — no default, placeholder, or
    # otherwise fabricated value (Requirement 6.3).
    for field in _PROJECTION_FIELDS:
        assert field not in result, (
            f"Unavailable_Marker fabricated '{field}'={result.get(field)!r} "
            f"(must be omitted): {result!r}"
        )

    # The inputs are left unmodified (purity underpins the honest marker, R1.5).
    assert candles == candles_snapshot, "forecast mutated its candle input"
