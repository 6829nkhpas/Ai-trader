"""Property-based test for zero-denominator nullness and all-null-with-no-tick
unavailability (order_flow.py, task 4.4).

Feature: order-flow-context

This module implements design **Property 5: Zero-denominator measures are null,
and all-null-with-no-tick yields unavailable**:

    An Order_Flow_Proxy_Measure whose denominator is zero is represented as
    ``null`` in the Order_Flow_Label rather than raising:

      * the buying-pressure ratio is ``null`` when the total directional volume
        over the lookback is zero (no up or down candles — R1.5, R4.5),
      * the per-candle delta proxy (``candle_delta``) is ``null`` when the most
        recent valid candle has ``high == low`` (zero range — the close-location
        denominator is zero, R4.5).

    And when EVERY candle-derived proxy is null AND the Tick_OFI is unavailable
    (no tick stream), ``classify_order_flow`` returns an Unavailable_Marker
    (``unavailable: true``) that OMITS ``order_flow_state`` / ``alignment``
    rather than fabricating a label (R4.6). Equivalently (the contrapositive),
    whenever a no-tick input still classifies into a label, at least one
    candle-derived proxy is non-null.

Validates: Requirements 1.5, 4.5, 4.6.

Candles are generated as dict-like OHLCV records with ``open`` / ``high`` /
``low`` / ``close`` / ``volume`` keys, exactly as ``order_flow.py`` reads them
via ``candle.get(...)``. Every scenario passes ``ticks=None`` or ``ticks=[]``
(the no-tick case) so the Tick_OFI is unavailable and only the candle-derived
proxy layer can speak. Several scenarios generate enough valid candles to clear
the ``largest_lookback`` sufficiency gate so the proxy-layer behaviour (not the
insufficient-candle branch) is exercised; a degenerate scenario drives the
all-unavailable outcome so the no-fabrication omission of R4.6 is checked
directly.

The sys.path / import and candle-generation patterns mirror the sibling
``test_of_*_properties.py`` modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import (  # noqa: E402
    classify_order_flow,
    resolve_order_flow_config,
)

# Fields the calculator must NOT fabricate when order flow is unavailable
# (Requirement 4.6 / AD-5): the marker omits these entirely.
_FABRICATED_FIELDS = ("order_flow_state", "alignment")
# The nullable candle-derived proxies whose conjunction-of-null drives R4.6.
_NULLABLE_PROXIES = ("candle_delta", "cvd_proxy", "buying_pressure_ratio")

_ORDER_FLOW_STATES = {"buying", "selling", "balanced"}
_ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}

_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)
_SPAN = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
# A strictly-positive volume so directional candles actually contribute volume.
_POS_VOLUME = st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False)


def _is_finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@st.composite
def _zero_denominator_case(draw):
    """A no-tick scenario exercising a zero-denominator / all-null regime.

    Returns ``(candles, ticks, scenario)``. ``ticks`` is always ``None`` or an
    empty list (the no-tick case, so the Tick_OFI is unavailable). ``scenario``
    selects the candle regime:

      * ``flat``               — every candle has ``high == low`` (degenerate
        zero range): close-location / ``candle_delta`` is null and, since
        ``close == open`` too, the total directional volume is zero so the
        buying-pressure ratio is null. ``cvd_proxy`` is still a computable sum
        (``0.0``), so a label is returned (R4.6 premise unmet).
      * ``close_at_open``      — every candle has ``high > low`` but
        ``close == open``: the total directional volume is zero so the
        buying-pressure ratio is null (R1.5, R4.5), while ``candle_delta`` /
        ``cvd_proxy`` remain computable.
      * ``directional``        — ordinary candles with non-zero directional
        volume: the buying-pressure ratio is a finite value in ``[0, 1]``.
      * ``no_valid``           — every candle carries a non-finite OHLCV field
        so no candle is valid; with no tick stream nothing is computable and the
        calculator must return an Unavailable_Marker omitting state/alignment.
    """
    config = resolve_order_flow_config()
    gate = config.largest_lookback
    scenario = draw(
        st.sampled_from(["flat", "close_at_open", "directional", "no_valid"])
    )
    # Enough valid candles (with headroom) to clear the sufficiency gate for the
    # computable scenarios.
    n = draw(st.integers(min_value=gate, max_value=gate + 15))
    ticks = draw(st.sampled_from([None, []]))

    if scenario == "flat":
        candles = []
        for _ in range(n):
            price = draw(_PRICE)
            candles.append(
                {"open": price, "high": price, "low": price,
                 "close": price, "volume": draw(_VOLUME)}
            )
        return candles, ticks, scenario

    if scenario == "close_at_open":
        candles = []
        for _ in range(n):
            low = draw(_PRICE)
            high = low + draw(_SPAN)
            # close == open, strictly inside the range so high > low holds.
            oc = draw(st.floats(min_value=low, max_value=high,
                                allow_nan=False, allow_infinity=False))
            candles.append(
                {"open": oc, "high": high, "low": low,
                 "close": oc, "volume": draw(_VOLUME)}
            )
        return candles, ticks, scenario

    if scenario == "directional":
        candles = []
        # Guarantee at least one up and one down candle within the lookback
        # window so the total directional volume is strictly positive.
        for i in range(n):
            low = draw(_PRICE)
            high = low + draw(_SPAN)
            if i % 2 == 0:  # up candle: close > open
                o = low
                c = high
            else:  # down candle: close < open
                o = high
                c = low
            candles.append(
                {"open": o, "high": high, "low": low,
                 "close": c, "volume": draw(_POS_VOLUME)}
            )
        return candles, ticks, scenario

    # no_valid: every candle has a non-finite field -> zero valid candles.
    candles = [
        {"open": float("nan"), "high": float("inf"), "low": 0.0,
         "close": 1.0, "volume": 1.0}
        for _ in range(n)
    ]
    return candles, ticks, scenario


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (task 4.4): Zero-denominator measures are null, and
# all-null-with-no-tick yields unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 5: Zero-denominator measures are null, and all-null-with-no-tick yields unavailable
@settings(max_examples=200, deadline=None)
@given(case=_zero_denominator_case())
def test_property_5_zero_denominator_null_and_all_null_no_tick_unavailable(case):
    """Feature: order-flow-context, Property 5: Zero-denominator measures are
    null, and all-null-with-no-tick yields unavailable.

    A proxy measure whose denominator is zero is represented as ``null`` in the
    label rather than raising; when every candle-derived proxy is null and the
    Tick_OFI is unavailable, the calculator returns an Unavailable_Marker that
    omits ``order_flow_state`` / ``alignment`` (and, contrapositively, whenever a
    no-tick input still yields a label, at least one candle-derived proxy is
    non-null).

    Validates: Requirements 1.5, 4.5, 4.6
    """
    candles, ticks, scenario = case

    # The calculator never raises and always yields a dict (Requirement 4).
    result = classify_order_flow(candles, ticks, resolve_order_flow_config())
    assert isinstance(result, dict)

    # No tick stream was supplied, so the Tick_OFI must be unavailable: any
    # returned label carries tick_ofi == None and live_tick_contributed False.
    if "unavailable" not in result:
        assert result.get("tick_ofi") is None, (
            f"no-tick input must leave tick_ofi null, got {result.get('tick_ofi')!r}"
        )
        assert result.get("live_tick_contributed") is False

    if scenario == "no_valid":
        # No valid candle and no tick -> nothing is computable, so the calculator
        # must degrade to an honest Unavailable_Marker that omits the categorical
        # outputs rather than fabricating them (Requirement 4.6 / AD-5).
        assert result.get("unavailable") is True, (
            f"all-unavailable input must yield an Unavailable_Marker, got {result!r}"
        )
        for field in _FABRICATED_FIELDS:
            assert field not in result, (
                f"unavailable marker must not fabricate {field!r}: {result!r}"
            )
        return

    # The computable scenarios clear the gate, so a label (not a marker) is
    # returned with well-formed categorical outputs.
    assert "unavailable" not in result, (
        f"{scenario}: a gate-clearing input should classify into a label, got {result!r}"
    )
    measures = result["measures"]
    assert result["order_flow_state"] in _ORDER_FLOW_STATES
    assert result["alignment"] in _ALIGNMENT_VALUES

    # ── R4.6 contrapositive: a no-tick LABEL never has every proxy null ───────
    # If every candle-derived proxy were null with no tick, the result would be
    # an Unavailable_Marker; since this is a label, at least one is non-null.
    assert any(measures[name] is not None for name in _NULLABLE_PROXIES), (
        f"{scenario}: a no-tick label must have at least one non-null proxy: {measures!r}"
    )

    if scenario == "flat":
        # Zero range on every candle -> the most recent candle's close-location
        # (and hence candle_delta) is null (R4.5); close == open on every candle
        # -> zero total directional volume -> buying-pressure ratio null
        # (R1.5, R4.5).
        assert measures["candle_delta"] is None, (
            f"flat (high==low) last candle must yield a null candle_delta: {measures!r}"
        )
        assert measures["buying_pressure_ratio"] is None, (
            f"zero directional volume must yield a null buying_pressure_ratio: {measures!r}"
        )
        # The CVD proxy is still a computable sum (no zero-denominator), which is
        # exactly why the all-null premise is unmet and a label is returned.
        assert _is_finite_number(measures["cvd_proxy"]), (
            f"flat candles still produce a finite CVD proxy: {measures!r}"
        )

    elif scenario == "close_at_open":
        # close == open on every candle -> zero total directional volume -> the
        # buying-pressure ratio's denominator is zero -> null (R1.5, R4.5).
        assert measures["buying_pressure_ratio"] is None, (
            f"zero directional volume must yield a null buying_pressure_ratio: {measures!r}"
        )

    else:  # directional
        # Non-zero directional volume -> the buying-pressure ratio is a finite
        # value within its bounded range (denominator is non-zero, so not null).
        ratio = measures["buying_pressure_ratio"]
        assert _is_finite_number(ratio), (
            f"directional volume must yield a finite buying_pressure_ratio: {ratio!r}"
        )
        assert 0.0 <= ratio <= 1.0, f"ratio out of bounds: {ratio!r}"
