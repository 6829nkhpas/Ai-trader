"""Property-based test for non-finite candle/tick exclusion (order_flow.py, task 4.9).

Feature: order-flow-context

This module implements design **Property 13: Non-finite candles and ticks are
excluded without affecting the result**:

    A candle carrying a non-finite (NaN / +/-inf) or non-numeric OHLCV field, and
    a tick carrying a non-finite / non-numeric *required* field (last_price /
    cumulative volume), are excluded from EVERY order-flow computation. So
    interleaving such bad candles and bad ticks anywhere within an otherwise-valid
    candle sequence and an otherwise-valid tick sequence does not change the
    classification outcome, and ``classify_order_flow`` never raises:

        classify_order_flow(clean_candles, clean_ticks, ...)
            == classify_order_flow(polluted_candles, polluted_ticks, ...)

    where "polluted" is the clean input with extra guaranteed-invalid
    candles/ticks interleaved.

Validates: Requirements 4.2.

The substantive classification fields (``order_flow_state`` / ``alignment`` /
``measures`` / ``tick_ofi`` / ``live_tick_contributed`` — or, on the
Unavailable_Marker path, ``unavailable`` / ``reason``) must be element-wise
identical between the clean and polluted runs. The count fields
(``candles_used`` / ``ticks_used``) also count only valid entries, so they are
identical too; the property focuses on the substantive fields per the task.

The strategies and sys.path bootstrap mirror the sibling
``test_of_determinism_properties.py`` and ``test_rs_nonfinite_exclusion_properties.py``
modules.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import classify_order_flow, resolve_order_flow_config  # noqa: E402

# Resolve config once (identical on the tool and backtest paths). Its lookback
# (20) / min_candles (20) drive the sufficiency gate; reusing the single resolved
# configuration on every call makes any difference attributable to the
# interleaved bad entries alone.
_CONFIG = resolve_order_flow_config()

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite price / volume values in a sane, non-degenerate band so generated
# sequences frequently reach the Order_Flow_Label path. NaN / inf / non-numeric
# values are injected separately to drive the exclusion path (Requirement 4.2).
_finite_price = st.floats(
    min_value=0.5, max_value=10_000.0, allow_nan=False, allow_infinity=False
)
_finite_volume = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Values that make a field non-finite or non-numeric — guaranteeing the carrying
# candle/tick is excluded from every computation (Requirement 4.2). The string
# "12.5" looks numeric but is non-numeric to the calculator (it reads numbers,
# never parses strings); ``bool`` is excluded by the repo's finite-number
# convention.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "x", "12.5", "", True, False, [], {}]
)


@st.composite
def _clean_candle(draw):
    """A well-formed dict-like OHLCV candle with finite fields and ``high >= low``."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    c = draw(_finite_price)
    d = draw(_finite_price)
    low = min(a, b, c, d)
    high = max(a, b, c, d)
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(_finite_volume),
    }


@st.composite
def _bad_candle(draw):
    """A candle guaranteed to be excluded: one OHLCV field carries a bad value.

    Every OHLCV field is required by ``order_flow._parse_ohlcv``, so corrupting
    any single one of them guarantees the candle is dropped from every proxy
    computation (Requirement 4.2).
    """
    candle = draw(_clean_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close", "volume"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _clean_tick(draw):
    """A well-formed dict-like tick with ``last_price`` / ``volume`` / quotes.

    The quote is either present (``bid > 0`` and ``ask >= bid``) so the Lee-Ready
    refinement engages, or absent (``0.0``) so it is skipped — both paths covered.
    ``volume`` is set by the sequence builder to a running cumulative value.
    """
    last_price = draw(_finite_price)
    if draw(st.booleans()):
        bid = draw(_finite_price)
        ask = bid + draw(st.floats(min_value=0.0, max_value=50.0,
                                   allow_nan=False, allow_infinity=False))
    else:
        bid = 0.0
        ask = 0.0
    return {"last_price": last_price, "best_bid": bid, "best_ask": ask, "volume": 0.0}


@st.composite
def _bad_tick(draw):
    """A tick guaranteed to be excluded: a *required* field carries a bad value.

    ``last_price`` and the cumulative ``volume`` are required by
    ``order_flow._parse_tick`` — a bad value in either drops the tick from the
    Tick_OFI computation (Requirement 4.2). (A bad value in only ``best_bid`` /
    ``best_ask`` would NOT exclude the tick — those are coerced to 0.0 — so this
    generator deliberately corrupts a required field.)
    """
    tick = draw(_clean_tick())
    # Always corrupt a required field; optionally corrupt the other too.
    if draw(st.booleans()):
        tick["last_price"] = draw(_bad_field)
        if draw(st.booleans()):
            tick["volume"] = draw(_bad_field)
    else:
        tick["volume"] = draw(_bad_field)
        if draw(st.booleans()):
            tick["last_price"] = draw(_bad_field)
    return tick


@st.composite
def _clean_candles(draw):
    """A clean candle sequence: 0..60 well-formed candles.

    The range spans both the Unavailable_Marker path (fewer valid candles than
    the configured ``largest_lookback``) and the Order_Flow_Label path (enough
    valid candles), so the exclusion invariant is exercised on both.
    """
    n = draw(st.integers(min_value=0, max_value=60))
    return [draw(_clean_candle()) for _ in range(n)]


@st.composite
def _clean_ticks(draw):
    """An optional, chronological (oldest-first) clean tick sequence.

    ``None`` is the proxy-only (backtest) input. When present, the cumulative
    ``volume`` is a running, non-decreasing sum of non-negative increments so the
    sequence is a realistic day's cumulative volume.
    """
    if draw(st.booleans()):
        return None
    n = draw(st.integers(min_value=0, max_value=40))
    cumulative = draw(_finite_price)
    ticks = []
    for _ in range(n):
        tick = draw(_clean_tick())
        cumulative += draw(st.floats(min_value=0.0, max_value=1e6,
                                     allow_nan=False, allow_infinity=False))
        tick["volume"] = cumulative
        ticks.append(tick)
    return ticks


@st.composite
def _interleaved(draw, clean, bad_strategy):
    """Return ``clean`` with 0..15 guaranteed-bad entries inserted at arbitrary
    positions, preserving the relative order of the valid entries."""
    polluted = list(clean)
    bad_entries = draw(st.lists(bad_strategy, max_size=15))
    for bad in bad_entries:
        idx = draw(st.integers(min_value=0, max_value=len(polluted)))
        polluted.insert(idx, bad)
    return polluted


@st.composite
def _clean_and_polluted(draw):
    """Produce ``(clean_candles, clean_ticks, polluted_candles, polluted_ticks)``.

    The polluted candle sequence is the clean one with bad candles interleaved;
    the polluted tick sequence is the clean one with bad ticks interleaved (or
    ``None`` when ticks are absent — the proxy-only path).
    """
    clean_candles = draw(_clean_candles())
    clean_ticks = draw(_clean_ticks())

    polluted_candles = draw(_interleaved(clean_candles, _bad_candle()))

    if clean_ticks is None:
        polluted_ticks = None
    else:
        polluted_ticks = draw(_interleaved(clean_ticks, _bad_tick()))

    return clean_candles, clean_ticks, polluted_candles, polluted_ticks


_proposed_direction = st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", None])


def _deep_equal(a, b):
    """Structural equality treating NaN as equal to NaN (defensive guard)."""
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b
    return a == b


def _substantive(result):
    """Project an order-flow result to its substantive classification fields.

    For an Order_Flow_Label this is ``order_flow_state`` / ``alignment`` /
    ``measures`` / ``tick_ofi`` / ``live_tick_contributed``; for an
    Unavailable_Marker it is ``unavailable`` / ``reason``. Count fields
    (``candles_used`` / ``ticks_used``) are intentionally excluded (the task
    focuses on the substantive fields — they count only valid entries and are
    equal regardless).
    """
    if result.get("unavailable"):
        return {"unavailable": True, "reason": result.get("reason")}
    return {
        "order_flow_state": result.get("order_flow_state"),
        "alignment": result.get("alignment"),
        "measures": result.get("measures"),
        "tick_ofi": result.get("tick_ofi"),
        "live_tick_contributed": result.get("live_tick_contributed"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: Non-finite candles and ticks are excluded without affecting result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 13: Non-finite candles and ticks are excluded without affecting the result
@settings(max_examples=150, deadline=None)
@given(data=_clean_and_polluted(), proposed_direction=_proposed_direction)
def test_property_13_non_finite_candles_and_ticks_excluded(data, proposed_direction):
    """Feature: order-flow-context, Property 13: Non-finite candles and ticks are
    excluded without affecting the result.

    Classifying a clean candle/tick sequence equals classifying the same sequence
    with extra non-finite/non-numeric candles and ticks interleaved — the bad
    entries do not affect the Order_Flow_State, the Alignment, the named measures,
    the Tick_OFI, or the live-tick-contributed flag (and, on the unavailable path,
    the marker reason) — and ``classify_order_flow`` never raises.

    Validates: Requirements 4.2
    """
    clean_candles, clean_ticks, polluted_candles, polluted_ticks = data

    # Snapshot the inputs so we can confirm the calls did not mutate them
    # (exclusion must be non-destructive — Requirement 4.2 / purity).
    clean_candles_snapshot = copy.deepcopy(clean_candles)
    clean_ticks_snapshot = copy.deepcopy(clean_ticks)
    polluted_candles_snapshot = copy.deepcopy(polluted_candles)
    polluted_ticks_snapshot = copy.deepcopy(polluted_ticks)

    clean_result = classify_order_flow(
        clean_candles, clean_ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )
    polluted_result = classify_order_flow(
        polluted_candles, polluted_ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )

    # The interleaved bad candles/ticks must not change the substantive outcome.
    assert _deep_equal(_substantive(clean_result), _substantive(polluted_result)), (
        "interleaved non-finite candles/ticks changed the classification:\n"
        f" clean={clean_result!r}\n polluted={polluted_result!r}"
    )

    # The count fields count only valid entries, so they must agree too.
    assert clean_result.get("candles_used") == polluted_result.get("candles_used")
    assert clean_result.get("ticks_used") == polluted_result.get("ticks_used")

    # Neither call may mutate its inputs.
    assert _deep_equal(clean_candles, clean_candles_snapshot)
    assert _deep_equal(clean_ticks, clean_ticks_snapshot)
    assert _deep_equal(polluted_candles, polluted_candles_snapshot)
    assert _deep_equal(polluted_ticks, polluted_ticks_snapshot)
