"""Property-based test for Lee-Ready quote-location sign refinement (task 3.3).

Feature: order-flow-context

This module implements design **Property 7: Quote location refines the tick sign
(Lee-Ready)**:

    When a tick carries a usable best bid/ask, ``compute_tick_ofi`` signs that
    tick by the trade's location relative to the bid/ask mid-price rather than by
    price direction alone (Lee-Ready style): a trade strictly above the mid is
    signed as buying (+1), a trade strictly below the mid is signed as selling
    (-1), and a trade exactly at the mid falls back to the tick-rule
    price-direction sign. This refinement can therefore *override* the pure
    tick-rule sign — e.g. a downtick in price whose trade prints above the mid is
    signed as buying — so the resulting Tick_OFI differs from the OFI computed
    over the identical price/volume path with the quotes removed.

Validates: Requirements 2.2.

Strategy: build a base path of strictly-monotonic prices (so every tick is an
unambiguous up- or down-tick) with strictly-increasing cumulative volume (so
every consecutive delta contributes). The same path is fed to ``compute_tick_ofi``
twice:

  * ``plain`` ticks carry no usable quote (``best_bid == best_ask == 0``) so the
    pure tick rule applies — a strictly-down path yields OFI ``-1.0`` and a
    strictly-up path yields ``+1.0``.
  * ``quoted`` ticks carry a usable best bid/ask positioned either AGAINST the
    price trend (every trade prints on the opposite side of the mid from the
    tick-rule direction) or AT the mid (every trade prints exactly at the mid).

For the "against" placement the quote-location sign overrides the tick-rule sign,
flipping the OFI, which demonstrates the refinement changing the result. For the
"at-mid" placement the refinement falls back to the tick-rule sign, leaving the
OFI unchanged. Ticks are dict-like records with keys ``last_price`` / ``volume``
(cumulative) / ``best_bid`` / ``best_ask`` (matching the ``tools._read_live_ticks``
shape ``order_flow.compute_tick_ofi`` consumes). The sys.path / import pattern
mirrors ``tests/test_of_clamping_properties.py``; config comes from
``resolve_order_flow_config()`` and every sequence carries >= ``config.min_ticks``
usable ticks.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import (  # noqa: E402
    compute_tick_ofi,
    resolve_order_flow_config,
)

_CONFIG = resolve_order_flow_config()

# Generate comfortably more than config.min_ticks usable ticks so the sample is
# always large enough for a trustworthy Tick_OFI.
_MIN_COUNT = max(_CONFIG.min_ticks + 2, 12)


@st.composite
def _scenario(draw):
    """A strictly-monotonic price/volume path plus a quote-placement mode.

    ``trend`` fixes the pure tick-rule outcome (every tick is a clean up/down
    tick); ``mode`` chooses how the usable quote is positioned relative to the
    trade price:
      * ``"against"`` — quote placed so each trade prints on the side of the mid
        OPPOSITE the price trend, so quote location overrides the tick rule.
      * ``"at_mid"``  — quote placed symmetrically around the trade, so the trade
        prints exactly at the mid and the refinement falls back to the tick sign.
    """
    n = draw(st.integers(min_value=_MIN_COUNT, max_value=40))
    start = draw(st.floats(min_value=600.0, max_value=1000.0,
                           allow_nan=False, allow_infinity=False))
    price_step = draw(st.floats(min_value=0.5, max_value=3.0,
                                allow_nan=False, allow_infinity=False))
    vol_step = draw(st.floats(min_value=1.0, max_value=1000.0,
                              allow_nan=False, allow_infinity=False))
    trend = draw(st.sampled_from(["up", "down"]))
    mode = draw(st.sampled_from(["against", "at_mid"]))

    prices = []
    for i in range(n):
        prices.append(start + i * price_step if trend == "up" else start - i * price_step)
    # Strictly increasing cumulative volume => every consecutive delta is positive.
    vols = [1000.0 + i * vol_step for i in range(n)]
    return {"prices": prices, "vols": vols, "trend": trend, "mode": mode}


def _plain_ticks(prices, vols):
    """Ticks with no usable quote (best_bid == best_ask == 0) => pure tick rule."""
    return [
        {"last_price": p, "volume": v, "best_bid": 0.0, "best_ask": 0.0}
        for p, v in zip(prices, vols)
    ]


def _quoted_ticks(prices, vols, trend, mode):
    """Ticks carrying a usable best bid/ask positioned per ``mode``."""
    ticks = []
    for p, v in zip(prices, vols):
        if mode == "at_mid":
            # Zero-width usable quote AT the trade so ``mid == last_price``
            # EXACTLY. A symmetric +/-1.0 spread does NOT yield an exact mid for
            # non-representable prices (e.g. ((1001.7)+(1003.7))/2 != 1002.7 in
            # IEEE-754), which would land the trade a hair off the mid and let
            # the Lee-Ready refinement correctly sign it — spuriously failing the
            # at-mid fallback assertion. ``(p + p) / 2 == p`` holds for every p.
            bid, ask = p, p
        elif trend == "down":
            # Pure tick rule would sign these downticks -1; place the trade ABOVE
            # the mid (mid = p - 2 < p) so quote location signs them +1 (buying).
            bid, ask = p - 3.0, p - 1.0
        else:  # trend == "up"
            # Pure tick rule would sign these upticks +1; place the trade BELOW
            # the mid (mid = p + 2 > p) so quote location signs them -1 (selling).
            bid, ask = p + 1.0, p + 3.0
        ticks.append({"last_price": p, "volume": v, "best_bid": bid, "best_ask": ask})
    return ticks


# Feature: order-flow-context, Property 7: Quote location refines the tick sign (Lee-Ready)
@settings(max_examples=200, deadline=None)
@given(scenario=_scenario())
def test_property_7_quote_location_refines_tick_sign(scenario):
    """Feature: order-flow-context, Property 7: Quote location refines the tick
    sign (Lee-Ready).

    A usable best bid/ask refines each tick's sign by the trade's location
    relative to the mid (above => buy, below => sell, at mid => tick sign),
    overriding the pure price-direction tick rule. Demonstrated by comparing the
    quoted OFI against the OFI of the identical price/volume path with the quotes
    removed.

    Validates: Requirements 2.2
    """
    prices, vols = scenario["prices"], scenario["vols"]
    trend, mode = scenario["trend"], scenario["mode"]

    plain = _plain_ticks(prices, vols)
    quoted = _quoted_ticks(prices, vols, trend, mode)

    ofi_plain = compute_tick_ofi(plain, _CONFIG)
    ofi_quoted = compute_tick_ofi(quoted, _CONFIG)

    # Both sequences have enough usable ticks and strictly-positive volume deltas,
    # so neither is unavailable.
    assert ofi_plain is not None, "plain (no-quote) OFI unexpectedly unavailable"
    assert ofi_quoted is not None, "quoted OFI unexpectedly unavailable"

    # Pure tick rule: a strictly-up path is all buys (+1), a strictly-down path
    # is all sells (-1).
    pure_expected = 1.0 if trend == "up" else -1.0
    assert ofi_plain == pure_expected, (
        f"pure tick-rule OFI {ofi_plain!r} != expected {pure_expected!r} "
        f"for trend={trend!r}"
    )

    if mode == "against":
        # Quote location prints every trade on the side of the mid OPPOSITE the
        # price trend, so the refined sign overrides the tick rule and flips the
        # imbalance: down-trend trades above mid => +1, up-trend trades below
        # mid => -1.
        refined_expected = -1.0 if trend == "up" else 1.0
        assert ofi_quoted == refined_expected, (
            f"quote-refined OFI {ofi_quoted!r} != expected {refined_expected!r} "
            f"for trend={trend!r}"
        )
        # The refinement demonstrably changed the result versus price direction alone.
        assert ofi_quoted != ofi_plain, (
            "quote-location refinement did not change the sign relative to the "
            "pure tick rule"
        )
    else:  # mode == "at_mid"
        # A trade exactly at the mid falls back to the tick-rule price-direction
        # sign, so the quoted OFI matches the pure tick-rule OFI.
        assert ofi_quoted == ofi_plain, (
            f"at-mid refinement changed the OFI ({ofi_quoted!r} != {ofi_plain!r}); "
            "it should fall back to the tick-rule sign"
        )
