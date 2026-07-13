"""Order_Flow_Calculator — pure-math order-flow context for the Deep Quant agent.

The Deep Quant agent ("Alpha-Quant") reasons from candle-derived indicators and,
since the regime and relative-strength features, from market regime and relative
strength too — but it is still blind to *order flow*: who is actually pressing
the trade, buyers or sellers. A veteran trader reads the tape. This module
implements that read within the honest limits of the data the system has.

The data reality drives a two-layer design:

  1. Candle-derived order-flow proxies — a pure-math layer computed from OHLCV
     candles only (a per-candle delta proxy from close-location x volume, a
     cumulative-volume-delta proxy, up/down volume, and a buying-pressure ratio).
     Deterministic, property-testable, and fully backtestable on the candle
     archive.
  2. Live tick-based Order Flow Imbalance (Tick_OFI) — a true tick-rule OFI
     (signed traded volume by uptick/downtick, refined Lee-Ready-style by quote
     location), computed from a provided `live_ticks` tick sequence. It is the
     real intraday edge but is honestly marked *unavailable* when the tick stream
     is absent, never fabricating a neutral value.

Both layers feed a single Order_Flow_Label (an Order_Flow_State plus an Alignment
of a proposed trade direction with the flow), or an honest Unavailable_Marker.

Scope discipline (Requirement 14): everything here is a *filter / context aid*,
never a trade generator. The calculator maps a candle sequence, an optional tick
sequence, and a resolved configuration to a structured Order_Flow_Label (or an
Unavailable_Marker); it never emits BUY/SELL/HOLD, never blocks a trade, and
never fabricates data.

Purity (Requirement 1): this module is pure Python. It performs zero network
calls, reads zero data sources other than its provided inputs (candles + ticks +
config), and touches no file/clock. Parameter *resolution*
(``resolve_order_flow_config``) is the only place the process environment is
read, and it does so once up front, deterministically, with documented defaults.

This file (task 1.1) provides the parameter-resolution foundation: the
documented default constants, the frozen ``OrderFlowConfig`` dataclass, and
``resolve_order_flow_config()``. The measure, Tick_OFI, and classification
functions are added in subsequent tasks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional, Tuple

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / out of
# range (Requirement 13.2-13.4). These are the single source of truth for the
# defaults on BOTH the live tool path and the backtest path (Requirement 13.6).

DEFAULT_OF_LOOKBACK = 20              # bars over which CVD / up-down volume / pressure are measured
DEFAULT_OF_MIN_CANDLES = 20          # minimum valid candles required to classify the proxy layer
DEFAULT_OF_BUY_PRESSURE_THRESHOLD = 0.58   # buying-pressure ratio >= this => buying (proxy layer)
DEFAULT_OF_SELL_PRESSURE_THRESHOLD = 0.42  # buying-pressure ratio <= this => selling (proxy layer)
DEFAULT_OF_OFI_BUY_THRESHOLD = 0.20  # Tick_OFI >= this => buying (tick layer)
DEFAULT_OF_OFI_SELL_THRESHOLD = -0.20  # Tick_OFI <= this => selling (tick layer)
DEFAULT_OF_MIN_TICKS = 10            # minimum ticks for a trustworthy Tick_OFI (matches Rust >= 10)

# ── Environment variable names ────────────────────────────────────────────────
ENV_OF_LOOKBACK = "OF_LOOKBACK"
ENV_OF_MIN_CANDLES = "OF_MIN_CANDLES"
ENV_OF_BUY_PRESSURE_THRESHOLD = "OF_BUY_PRESSURE_THRESHOLD"
ENV_OF_SELL_PRESSURE_THRESHOLD = "OF_SELL_PRESSURE_THRESHOLD"
ENV_OF_OFI_BUY_THRESHOLD = "OF_OFI_BUY_THRESHOLD"
ENV_OF_OFI_SELL_THRESHOLD = "OF_OFI_SELL_THRESHOLD"
ENV_OF_MIN_TICKS = "OF_MIN_TICKS"

# ── Valid ranges (inclusive) ──────────────────────────────────────────────────
# Periods/counts are integers >= 2 with no upper bound; the buying/selling
# pressure thresholds are decimals in [0.0, 1.0]; the Tick_OFI buy/sell
# thresholds are decimals in [-1.0, 1.0] (Requirement 13.1).
_PERIOD_MIN = 2
_PRESSURE_MIN = 0.0
_PRESSURE_MAX = 1.0
_OFI_MIN = -1.0
_OFI_MAX = 1.0


@dataclass(frozen=True)
class OrderFlowConfig:
    """The resolved, validated parameter set used to classify order flow.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the calculator's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on both
    the tool path and the backtest path (Requirement 13.6).
    """

    lookback: int
    min_candles: int
    buy_pressure_threshold: float
    sell_pressure_threshold: float
    ofi_buy_threshold: float
    ofi_sell_threshold: float
    min_ticks: int

    @property
    def largest_lookback(self) -> int:
        """Max valid candles any single proxy measure requires (drives the gate).

        The CVD / up-down-volume / buying-pressure proxy measures each look back
        over ``lookback`` candles, and the classifier additionally requires at
        least ``min_candles`` valid candles; ``classify_order_flow`` gates on the
        max of the two.
        """
        return max(self.lookback, self.min_candles)


def _resolve_float(env_name: str, default: float, low: float, high: float) -> float:
    """Resolve one float parameter from its own env var (Requirement 13.1-13.4).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as a
    float, is non-finite (NaN/inf), or parses but falls outside ``[low, high]``.
    Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (ValueError, TypeError):
        return default
    if not math.isfinite(value):
        return default
    if value < low or value > high:
        return default
    return value


def _resolve_int(env_name: str, default: int, low: int) -> int:
    """Resolve one integer parameter from its own env var (Requirement 13.1-13.4).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as an
    int, or parses but is below ``low`` (the minimum valid value). Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (ValueError, TypeError):
        return default
    if value < low:
        return default
    return value


def resolve_order_flow_config() -> OrderFlowConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 13):
      * unset / empty            -> documented default (R13.2)
      * unparseable as its type  -> documented default (never raises) (R13.3)
      * parses but out of range  -> documented default (never raises) (R13.4)
      * sell_pressure_threshold >= buy_pressure_threshold -> BOTH pressure
        thresholds revert to their documented defaults together (R13.5)
      * ofi_sell_threshold >= ofi_buy_threshold -> BOTH Tick_OFI thresholds
        revert to their documented defaults together (the same ordering guard)

    The same function is called on the tool path and the backtest path so the
    resolved values are identical for identical environment (Requirement 13.6).
    This function NEVER raises.
    """
    lookback = _resolve_int(ENV_OF_LOOKBACK, DEFAULT_OF_LOOKBACK, _PERIOD_MIN)
    min_candles = _resolve_int(ENV_OF_MIN_CANDLES, DEFAULT_OF_MIN_CANDLES, _PERIOD_MIN)
    min_ticks = _resolve_int(ENV_OF_MIN_TICKS, DEFAULT_OF_MIN_TICKS, _PERIOD_MIN)

    buy_pressure_threshold = _resolve_float(
        ENV_OF_BUY_PRESSURE_THRESHOLD,
        DEFAULT_OF_BUY_PRESSURE_THRESHOLD,
        _PRESSURE_MIN,
        _PRESSURE_MAX,
    )
    sell_pressure_threshold = _resolve_float(
        ENV_OF_SELL_PRESSURE_THRESHOLD,
        DEFAULT_OF_SELL_PRESSURE_THRESHOLD,
        _PRESSURE_MIN,
        _PRESSURE_MAX,
    )
    ofi_buy_threshold = _resolve_float(
        ENV_OF_OFI_BUY_THRESHOLD,
        DEFAULT_OF_OFI_BUY_THRESHOLD,
        _OFI_MIN,
        _OFI_MAX,
    )
    ofi_sell_threshold = _resolve_float(
        ENV_OF_OFI_SELL_THRESHOLD,
        DEFAULT_OF_OFI_SELL_THRESHOLD,
        _OFI_MIN,
        _OFI_MAX,
    )

    # Enforce the strict selling < buying ordering for the pressure thresholds.
    # If it does not hold (after the per-parameter resolution above), BOTH
    # pressure thresholds revert to their documented defaults together (R13.5).
    if sell_pressure_threshold >= buy_pressure_threshold:
        buy_pressure_threshold = DEFAULT_OF_BUY_PRESSURE_THRESHOLD
        sell_pressure_threshold = DEFAULT_OF_SELL_PRESSURE_THRESHOLD

    # Apply the same ordering guard to the Tick_OFI buy/sell thresholds: the
    # selling threshold must be strictly less than the buying threshold; if not,
    # BOTH revert to their documented defaults together.
    if ofi_sell_threshold >= ofi_buy_threshold:
        ofi_buy_threshold = DEFAULT_OF_OFI_BUY_THRESHOLD
        ofi_sell_threshold = DEFAULT_OF_OFI_SELL_THRESHOLD

    return OrderFlowConfig(
        lookback=lookback,
        min_candles=min_candles,
        buy_pressure_threshold=buy_pressure_threshold,
        sell_pressure_threshold=sell_pressure_threshold,
        ofi_buy_threshold=ofi_buy_threshold,
        ofi_sell_threshold=ofi_sell_threshold,
        min_ticks=min_ticks,
    )


# ── Candle validation helpers (Requirement 4.2) ──────────────────────────────
# A candle is a "dict-like" OHLCV record from the Rust Tool_Server with keys
# ``open`` / ``high`` / ``low`` / ``close`` / ``volume`` (mirroring how
# ``regime.py`` / ``rs.py`` / ``journal.py`` / ``backtest.py`` read candles via
# ``c.get(...)``). A candle is excluded from EVERY proxy computation when any
# OHLCV field it carries is non-numeric or non-finite (NaN / +/-inf), so the
# measures operate only on clean data (Requirement 4.2). None of these helpers
# mutate their inputs.


def _is_finite_number(v: Any) -> bool:
    """True for a finite real number; ``bool`` is excluded (matches the repo's
    ``_is_num`` convention in ``journal.py`` / ``regime.py`` / ``rs.py``)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_ohlcv(candle: Any) -> Optional[Tuple[float, float, float, float, float]]:
    """Read ``(open, high, low, close, volume)`` from one dict-like candle.

    Returns the five values as floats when ``open`` / ``high`` / ``low`` /
    ``close`` / ``volume`` are each finite numbers; returns ``None`` for a candle
    carrying a non-finite/non-numeric OHLCV field, an absent required field, or a
    non-mapping value (Requirement 4.2). Reads the candle without modifying it.

    Unlike the regime/rs OHLC parsers, ``volume`` is *required* and validated
    here because every order-flow proxy is volume-weighted; a candle missing or
    carrying a bad volume is excluded from all proxy computations.
    """
    get = getattr(candle, "get", None)
    if not callable(get):
        return None
    o = get("open")
    h = get("high")
    low = get("low")
    c = get("close")
    v = get("volume")
    if not (
        _is_finite_number(o)
        and _is_finite_number(h)
        and _is_finite_number(low)
        and _is_finite_number(c)
        and _is_finite_number(v)
    ):
        return None
    return (float(o), float(h), float(low), float(c), float(v))


def _valid_ohlcv_rows(candles: Any) -> list:
    """Project a candle sequence to a list of valid ``(o, h, l, c, v)`` rows.

    Candles with non-finite/non-numeric OHLCV fields are dropped
    (Requirement 4.2). The original sequence and its candle objects are left
    unmodified (the calculator's purity guarantee — Requirement 1.7). Returns an
    empty list for a ``None`` or non-iterable input rather than raising.
    """
    rows: list = []
    if candles is None:
        return rows
    try:
        iterator = iter(candles)
    except TypeError:
        return rows
    for candle in iterator:
        parsed = _parse_ohlcv(candle)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _valid_period(period: Any) -> bool:
    """True when ``period`` is a usable positive integer lookback (>= 1)."""
    return isinstance(period, int) and not isinstance(period, bool) and period >= 1


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to ``[low, high]`` (Requirement 4.4)."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _clv_from_row(row: Tuple[float, float, float, float, float]) -> Optional[float]:
    """Close-location value from a parsed ``(o, h, l, c, v)`` row.

    ``((close - low) - (high - close)) / (high - low)`` clamped to ``[-1, 1]``;
    ``None`` when ``high == low`` (zero range -> denominator is zero,
    Requirements 1.2, 4.5). Pure.
    """
    _o, high, low, close, _v = row
    if high == low:
        return None
    clv = ((close - low) - (high - close)) / (high - low)
    return _clamp(clv, -1.0, 1.0)


# ── Candle-derived proxy measure functions (pure, candle-only) ───────────────
# Each function:
#   * operates only on candles whose OHLCV fields are finite numbers (R4.2),
#   * returns a finite value when the measure is computable,
#   * returns ``None`` when its denominator is zero (R1.5, R4.5),
#   * clamps bounded measures into their defined range (R4.4),
#   * never mutates its inputs, never reads the network/clock, and never raises.


def compute_close_location_value(candle: Any) -> Optional[float]:
    """Close-location value of a single candle (Requirement 1.2).

    ``((close - low) - (high - close)) / (high - low)`` in ``[-1.0, 1.0]``;
    measures where the close sits within the candle's range (+1 at the high,
    -1 at the low, 0 at the midpoint). Returns ``None`` when ``high == low``
    (zero range — the denominator is zero, Requirements 1.2, 4.5) or when the
    candle carries a non-finite/non-numeric OHLCV field (excluded, R4.2). Pure;
    never mutates the candle; never raises.
    """
    parsed = _parse_ohlcv(candle)
    if parsed is None:
        return None
    return _clv_from_row(parsed)


def compute_candle_delta_proxy(candle: Any) -> Optional[float]:
    """Per-candle delta proxy = close-location value * volume (Requirement 1.2).

    Returns ``None`` when the close-location value is ``None`` (``high == low``)
    or when the candle carries a non-finite/non-numeric OHLCV field (excluded,
    R4.2). The sign indicates net buying (positive, close near the high) versus
    selling (negative, close near the low) pressure, weighted by volume. Pure;
    never mutates the candle; never raises.
    """
    parsed = _parse_ohlcv(candle)
    if parsed is None:
        return None
    clv = _clv_from_row(parsed)
    if clv is None:
        return None
    volume = parsed[4]
    return clv * volume


def compute_cvd_proxy(candles: Any, lookback: Any) -> Optional[float]:
    """Cumulative-volume-delta (CVD) proxy (Requirement 1.3).

    The running sum of the per-candle delta proxy over the last ``lookback``
    valid candles. A valid candle whose delta proxy is ``None`` (``high == low``)
    contributes ``0`` to the sum. Returns ``None`` when no valid candle is
    available (an empty or all-invalid sequence) or when ``lookback`` is not a
    usable positive integer. Pure; never mutates its inputs; never raises.
    """
    if not _valid_period(lookback):
        return None
    rows = _valid_ohlcv_rows(candles)
    if not rows:
        return None
    window = rows[-lookback:]
    total = 0.0
    for row in window:
        clv = _clv_from_row(row)
        if clv is None:
            continue  # None-delta candle contributes 0 (Requirement 1.3)
        total += clv * row[4]
    return total


def compute_up_down_volume(candles: Any, lookback: Any) -> Tuple[float, float]:
    """Up-volume and down-volume over the last ``lookback`` valid candles (R1.4).

    Up-volume is the sum of volume on candles closing above their open;
    down-volume is the sum of volume on candles closing below their open. Candles
    closing exactly at their open contribute to neither (they are directionally
    neutral). Returns the tuple ``(up_volume, down_volume)`` — ``(0.0, 0.0)`` when
    there are no valid candles or ``lookback`` is not a usable positive integer.
    Pure; never mutates its inputs; never raises.
    """
    if not _valid_period(lookback):
        return (0.0, 0.0)
    rows = _valid_ohlcv_rows(candles)
    if not rows:
        return (0.0, 0.0)
    window = rows[-lookback:]
    up_volume = 0.0
    down_volume = 0.0
    for o, _h, _low, c, v in window:
        if c > o:
            up_volume += v
        elif c < o:
            down_volume += v
        # c == o: directionally neutral, contributes to neither (R1.4)
    return (up_volume, down_volume)


def compute_buying_pressure_ratio(candles: Any, lookback: Any) -> Optional[float]:
    """Buying-pressure ratio in ``[0.0, 1.0]`` over the lookback (R1.5, R4.4).

    ``up_volume / (up_volume + down_volume)`` — the fraction of directional
    volume that traded on up candles — clamped into ``[0.0, 1.0]``
    (Requirement 4.4). Returns ``None`` when the total directional volume is zero
    (no up or down candles in the window — the denominator is zero,
    Requirements 1.5, 4.5). Pure; never mutates its inputs; never raises.
    """
    up_volume, down_volume = compute_up_down_volume(candles, lookback)
    total = up_volume + down_volume
    if total == 0:
        return None  # zero directional volume -> denominator is zero (R1.5, R4.5)
    return _clamp(up_volume / total, 0.0, 1.0)


# ── Live tick-based Order Flow Imbalance (Tick_OFI) ──────────────────────────
# This is the second, "live" layer of the calculator. It mirrors the
# authoritative Rust `compute_order_flow_imbalance`
# (`frontend/src-tauri/src/commands/deep_quant.rs`) exactly (AD-8): the tick rule
# over cumulative-volume deltas, refined Lee-Ready-style by quote location, then
# normalized and clamped into [-1.0, 1.0]. Where the Rust function returns
# `f64::NAN` to mean "unavailable", this Python mirror returns ``None`` so the
# caller never fabricates a neutral ``0.0`` (Requirements 2.3, 14.6).
#
# A "tick" is a dict-like record as produced by ``tools._read_live_ticks`` with
# keys ``last_price`` / ``volume`` / ``best_bid`` / ``best_ask`` (``volume`` is
# the day's *cumulative* traded volume, matching the Rust ``live_ticks.volume``
# column). A positional ``(last_price, cumulative_volume, best_bid, best_ask)``
# sequence is accepted too. Ticks are expected oldest-first (chronological), the
# same order ``_read_live_ticks`` yields after reversing the DESC query — this
# matches the Rust ``.rev()`` step.

# Quote-location refinement only engages when a usable best bid/ask is present;
# an absent / non-finite quote is treated as 0.0 so the refinement is skipped
# (mirrors the Rust ``unwrap_or(0.0)`` + ``bid > 0.0 && ask > 0.0`` guard).
_QUOTE_ABSENT = 0.0
# Total-traded-volume floor below which the imbalance is not trustworthy; the
# tick layer is then reported unavailable (mirrors the Rust ``total_vol < 1e-6``).
_OFI_TOTAL_VOLUME_EPSILON = 1e-6


def _coerce_finite_number(value: Any) -> Optional[float]:
    """Return ``value`` as a float when it is a finite real number, else ``None``.

    ``bool`` is rejected (matches ``_is_finite_number`` / the repo's ``_is_num``
    convention). Used to validate the per-tick fields (Requirement 4.2)."""
    if _is_finite_number(value):
        return float(value)
    return None


def _parse_tick(tick: Any) -> Optional[Tuple[float, float, float, float]]:
    """Read ``(last_price, cumulative_volume, best_bid, best_ask)`` from one tick.

    Accepts either a dict-like tick (the ``tools._read_live_ticks`` shape: keys
    ``last_price`` / ``volume`` / ``best_bid`` / ``best_ask``; common aliases are
    tolerated) or a positional ``(last_price, cumulative_volume, best_bid,
    best_ask)`` sequence.

    ``last_price`` and the cumulative ``volume`` are *required* and must be
    finite numbers — a tick missing either, or carrying a non-finite/non-numeric
    value for either, is excluded from the computation entirely (returns
    ``None``, Requirement 4.2). ``best_bid`` / ``best_ask`` are optional: a
    non-finite/non-numeric/absent quote is coerced to ``0.0`` so the quote
    refinement is simply skipped for that tick (mirrors the Rust
    ``unwrap_or(0.0)``). Reads the tick without modifying it (Requirement 2.5)."""
    get = getattr(tick, "get", None)
    if callable(get):
        last_price = _coerce_finite_number(
            get("last_price", get("last_traded_price", get("ltp")))
        )
        volume = _coerce_finite_number(get("volume", get("cumulative_volume")))
        bid_raw = _coerce_finite_number(get("best_bid", get("bid")))
        ask_raw = _coerce_finite_number(get("best_ask", get("ask")))
    else:
        # Positional sequence form: (last_price, cumulative_volume, best_bid, best_ask).
        # Strings are sequences but never a valid tick record, so reject them.
        if isinstance(tick, (str, bytes)):
            return None
        try:
            fields = tuple(tick)
        except TypeError:
            return None
        if len(fields) < 2:
            return None
        last_price = _coerce_finite_number(fields[0])
        volume = _coerce_finite_number(fields[1])
        bid_raw = _coerce_finite_number(fields[2]) if len(fields) > 2 else None
        ask_raw = _coerce_finite_number(fields[3]) if len(fields) > 3 else None

    if last_price is None or volume is None:
        return None
    bid = bid_raw if bid_raw is not None else _QUOTE_ABSENT
    ask = ask_raw if ask_raw is not None else _QUOTE_ABSENT
    return (last_price, volume, bid, ask)


def _usable_ticks(ticks: Any) -> list:
    """Project a tick sequence to a list of valid ``(ltp, vol, bid, ask)`` rows.

    Ticks with non-finite/non-numeric required fields are dropped
    (Requirement 4.2). Returns an empty list for a ``None`` or non-iterable input
    rather than raising. The original sequence and its tick objects are left
    unmodified (Requirement 2.5)."""
    rows: list = []
    if ticks is None:
        return rows
    try:
        iterator = iter(ticks)
    except TypeError:
        return rows
    for tick in iterator:
        parsed = _parse_tick(tick)
        if parsed is not None:
            rows.append(parsed)
    return rows


def compute_tick_ofi(ticks: Any, config: OrderFlowConfig) -> Optional[float]:
    """Tick-rule Order Flow Imbalance over a tick sequence (AD-8, Requirement 2).

    Mirrors the authoritative Rust ``compute_order_flow_imbalance``
    (``frontend/src-tauri/src/commands/deep_quant.rs``) exactly:

      * Per-tick traded size is the POSITIVE delta of the day's cumulative
        ``volume`` between consecutive (usable) ticks; non-positive deltas —
        session/counter resets — are skipped (Requirement 2.1).
      * Each delta is signed by the tick rule: uptick (price up) => +1 buy,
        downtick (price down) => -1 sell, zero-tick inherits the previous sign
        (the first sign seeds at +1, matching the Rust ``last_sign = 1.0``).
      * The sign is refined by quote location when a usable best bid/ask is
        present (``bid > 0`` and ``ask > 0`` and ``ask >= bid``): a trade above
        the bid/ask mid => +1, below the mid => -1, exactly at the mid => the
        tick sign (Lee-Ready style, Requirement 2.2).
      * OFI = net signed volume / total signed (traded) volume, clamped into
        ``[-1.0, 1.0]`` (Requirement 2.4).

    Returns ``None`` (unavailable) when ``ticks`` is empty, has fewer than
    ``config.min_ticks`` usable ticks, or yields a total signed volume at/below
    the trustworthiness floor (Requirement 2.3) — NEVER a fabricated neutral
    ``0.0`` (Requirement 14.6). Pure and deterministic; never mutates the tick
    sequence (Requirement 2.5); never returns a non-finite value (Requirement
    2.4); never raises (Requirement 4)."""
    rows = _usable_ticks(ticks)
    # Need a meaningful sample of usable ticks to derive a stable imbalance
    # (mirrors the Rust ``rows.len() < 10`` guard, parameterized by min_ticks).
    if len(rows) < config.min_ticks:
        return None

    signed_vol = 0.0
    total_vol = 0.0
    last_sign = 1.0  # seed (matches the Rust ``let mut last_sign = 1.0``)
    for i in range(1, len(rows)):
        prev_ltp, prev_vol, _prev_bid, _prev_ask = rows[i - 1]
        ltp, vol, bid, ask = rows[i]

        dv = vol - prev_vol
        # Guard against cumulative-counter resets (new session) -> skip non-positive.
        if dv <= 0.0:
            continue

        dp = ltp - prev_ltp
        if dp > 0.0:
            tick_sign = 1.0
        elif dp < 0.0:
            tick_sign = -1.0
        else:
            tick_sign = last_sign  # zero-tick inherits previous direction (tick rule)
        last_sign = tick_sign

        # Lee-Ready quote-location refinement when a usable best bid/ask is present.
        if bid > 0.0 and ask > 0.0 and ask >= bid:
            mid = (bid + ask) / 2.0
            if ltp > mid:
                refined_sign = 1.0
            elif ltp < mid:
                refined_sign = -1.0
            else:
                refined_sign = tick_sign
        else:
            refined_sign = tick_sign

        signed_vol += refined_sign * dv
        total_vol += dv

    # Zero (or negligible) total traded volume -> not trustworthy (R2.3, R14.6).
    if total_vol < _OFI_TOTAL_VOLUME_EPSILON:
        return None

    ofi = _clamp(signed_vol / total_vol, _OFI_MIN, _OFI_MAX)
    # Defensive: never surface a non-finite value (Requirement 2.4).
    if not math.isfinite(ofi):
        return None
    return ofi


# ── Classification functions (pure, total) ───────────────────────────────────
# These map the computed signals (the live Tick_OFI and the candle-derived
# buying-pressure ratio) and an optional proposed trade direction onto the two
# categorical outputs — the Order_Flow_State and the Alignment — exactly per the
# design's total mapping tables. Both are total functions: every input
# (including ``None`` / absent) maps to exactly one output value (Requirements
# 3.1, 3.3, 3.4). Pure; never raise.


def classify_order_flow_state(
    tick_ofi: Optional[float],
    buying_pressure_ratio: Optional[float],
    config: OrderFlowConfig,
) -> str:
    """Classify the Order_Flow_State (Requirements 3.1, 3.2).

    Returns exactly one of ``'buying'`` / ``'selling'`` / ``'balanced'`` with
    **tick-first priority** (Requirement 3.2): when ``tick_ofi`` is a usable
    finite value the live tick layer decides, compared against the configured
    Tick_OFI buy/sell thresholds; otherwise the candle-derived
    ``buying_pressure_ratio`` decides, compared against the configured pressure
    thresholds. A ``None`` on the deciding signal yields ``'balanced'``.

    Tick-first mapping (when a usable finite Tick_OFI is present):

      * ``tick_ofi >= ofi_buy_threshold``  -> ``'buying'``
      * ``tick_ofi <= ofi_sell_threshold`` -> ``'selling'``
      * otherwise (between thresholds)     -> ``'balanced'``

    Proxy mapping (otherwise):

      * ``buying_pressure_ratio >= buy_pressure_threshold``  -> ``'buying'``
      * ``buying_pressure_ratio <= sell_pressure_threshold`` -> ``'selling'``
      * otherwise (between thresholds, or ``None``)          -> ``'balanced'``

    Both threshold pairs satisfy ``sell < buy`` (enforced at resolution), so the
    buying / selling branches cannot both fire. Total: every input (including
    ``None``) maps to exactly one Order_Flow_State. Pure; never raises.
    """
    # Tick-first priority: a usable finite Tick_OFI overrides the candle proxies.
    if _is_finite_number(tick_ofi):
        if tick_ofi >= config.ofi_buy_threshold:
            return "buying"
        if tick_ofi <= config.ofi_sell_threshold:
            return "selling"
        return "balanced"

    # Fall back to the candle-derived buying-pressure ratio.
    if buying_pressure_ratio is None:
        return "balanced"
    if buying_pressure_ratio >= config.buy_pressure_threshold:
        return "buying"
    if buying_pressure_ratio <= config.sell_pressure_threshold:
        return "selling"
    return "balanced"


# Alignment lookup tables over the Order_Flow_State for a BUY and for a SELL
# proposed direction (design's Alignment derivation tables). Any Order_Flow_State
# absent from a table (i.e. ``balanced``) is ``neutral`` so the derivation stays
# total.
_ALIGNMENT_BUY = {
    "buying": "aligned",
    "selling": "misaligned",
}
_ALIGNMENT_SELL = {
    "selling": "aligned",
    "buying": "misaligned",
}


def derive_alignment(
    order_flow_state: str,
    proposed_direction: Optional[str],
) -> str:
    """Derive the Alignment (Requirements 3.3, 3.4).

    Returns exactly one of ``'aligned'`` / ``'misaligned'`` / ``'neutral'``,
    expressing whether the proposed trade direction agrees with the prevailing
    order-flow pressure. Total over every (Order_Flow_State x proposed_direction)
    combination:

      * A BUY with a ``buying`` state  -> ``'aligned'``;
        a BUY with a ``selling`` state -> ``'misaligned'``.
      * A SELL with a ``selling`` state -> ``'aligned'``;
        a SELL with a ``buying`` state  -> ``'misaligned'``.
      * A ``balanced`` state, for any direction -> ``'neutral'``.
      * An absent / ``None`` / non-directional (e.g. HOLD) proposed direction
        -> ``'neutral'`` (Requirement 3.4).

    Pure; never raises.
    """
    if not isinstance(proposed_direction, str):
        return "neutral"
    direction = proposed_direction.strip().upper()
    if direction == "BUY":
        return _ALIGNMENT_BUY.get(order_flow_state, "neutral")
    if direction == "SELL":
        return _ALIGNMENT_SELL.get(order_flow_state, "neutral")
    return "neutral"


# ── Unavailable_Marker helper ─────────────────────────────────────────────────


def _order_flow_unavailable(
    reason: str,
    symbol: Optional[str],
    timeframe: Optional[str],
) -> dict:
    """Build an honest Unavailable_Marker (Requirements 4.1, 4.6, 6.3, 14.6).

    ``order_flow_state`` / ``alignment`` are *omitted* (never defaulted or
    fabricated — Requirement 14.6); the marker carries no proxy measures, no
    Tick_OFI, and no live-tick flag. ``symbol`` / ``timeframe`` are included only
    when the caller supplies them (the classifier itself has no knowledge of
    them). The ``reason`` cites the cause; for the insufficient-data case it
    includes the count of valid candles received and the count required
    (Requirement 4.1).
    """
    marker: dict = {}
    if symbol is not None:
        marker["symbol"] = symbol
    if timeframe is not None:
        marker["timeframe"] = timeframe
    marker["unavailable"] = True
    marker["reason"] = reason
    return marker


def classify_order_flow(
    candles,
    ticks,
    config: OrderFlowConfig,
    proposed_direction: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Top-level entry point: map candles + ticks + config to a label or marker.

    Returns either an Order_Flow_Label dict (``order_flow_state`` / ``alignment``
    / ``measures`` / ``tick_ofi`` / ``live_tick_contributed`` — plus ``symbol`` /
    ``timeframe`` / ``candles_used`` / ``ticks_used`` when applicable) or an
    Unavailable_Marker dict.

    Behaviour (Requirements 1, 2, 3, 4, 6, 14):
      * Projects the candle sequence to its valid OHLCV rows (non-finite/
        non-numeric candles excluded — Requirement 4.2) and gates on the valid
        count: when it is fewer than ``config.largest_lookback`` the result is an
        Unavailable_Marker citing the count received and the count required
        (Requirement 4.1).
      * Computes every candle-derived Order_Flow_Proxy_Measure (``candle_delta``
        — the most recent valid candle's delta proxy, ``cvd_proxy``,
        ``up_volume``, ``down_volume``, ``buying_pressure_ratio``) from those
        valid candles only; each is a finite number or ``null`` (Requirements
        1.2-1.5, 4.3, 4.5), bounded measures clamped by their measure function
        (Requirement 4.4).
      * Computes the live Tick_OFI from the (possibly empty / ``None``) tick
        sequence and sets ``live_tick_contributed`` true **only** when a usable
        Tick_OFI was produced (Requirements 2, 3.5, 6.6); a missing/untrustworthy
        tick stream leaves ``tick_ofi`` ``null`` and the proxy layer still
        classifies (Requirement 6.1, 6.6).
      * Returns an Unavailable_Marker when every candle-derived proxy is ``null``
        AND the Tick_OFI is unavailable (Requirement 4.6).
      * Classifies the Order_Flow_State (tick-first — Requirement 3.2) and derives
        the Alignment (Requirements 3.3, 3.4).
      * Pure (no input mutation — Requirements 1.1, 1.7, 2.5), deterministic
        (Requirements 1.6, 2.5), and non-raising (Requirement 4). Emits ONLY a
        label or marker — never a BUY/SELL/HOLD action, conviction, or decision
        field (Requirements 14.1, 14.3).
    """
    try:
        # Project to valid OHLCV rows; non-finite/non-numeric candles excluded
        # (Requirement 4.2). The original inputs are left unmodified (R1.7).
        rows = _valid_ohlcv_rows(candles)
        valid_count = len(rows)

        # Sufficiency gate: require at least ``largest_lookback`` valid candles
        # (the max of the proxy lookback and the minimum-candle floor). Otherwise
        # the proxy layer cannot be trusted -> honest Unavailable_Marker citing
        # the received-vs-required counts (Requirement 4.1).
        required = config.largest_lookback
        if valid_count < required:
            return _order_flow_unavailable(
                f"insufficient data: {valid_count} valid candles received, "
                f"{required} required",
                symbol,
                timeframe,
            )

        # ── Candle-derived proxy measures (proxy layer) ──────────────────────
        # ``candle_delta`` is the most recent valid candle's per-candle delta
        # proxy (close-location value x volume), ``null`` when that candle's range
        # is zero (high == low). The remaining proxies look back over
        # ``config.lookback`` valid candles.
        last_clv = _clv_from_row(rows[-1])
        candle_delta = None if last_clv is None else last_clv * rows[-1][4]
        cvd_proxy = compute_cvd_proxy(candles, config.lookback)
        up_volume, down_volume = compute_up_down_volume(candles, config.lookback)
        buying_pressure_ratio = compute_buying_pressure_ratio(candles, config.lookback)

        # ── Live Tick_OFI (tick layer) ───────────────────────────────────────
        tick_ofi = compute_tick_ofi(ticks, config)
        live_tick_contributed = tick_ofi is not None
        ticks_used = len(_usable_ticks(ticks))

        measures = {
            "candle_delta": candle_delta,
            "cvd_proxy": cvd_proxy,
            "up_volume": up_volume,
            "down_volume": down_volume,
            "buying_pressure_ratio": buying_pressure_ratio,
        }

        # A structurally zero-volume candle series (e.g. a spot INDEX whose feed
        # carries no traded volume) makes every candle-derived proxy a
        # meaningless artifact: ``candle_delta`` and ``cvd_proxy`` collapse to
        # ``0.0`` (close-location value x zero volume) and ``buying_pressure_ratio``
        # is ``None``. Left unchecked those would be misreported as a real
        # ``balanced`` read. When the total traded volume over the lookback is
        # negligible AND no live Tick_OFI is available, candle-derived order flow
        # is genuinely UNAVAILABLE, not balanced — surface an honest marker so the
        # agent does not count a phantom "balanced" as confirmation (R4.6).
        window_volume = sum(r[4] for r in rows[-config.lookback:])
        if window_volume < _OFI_TOTAL_VOLUME_EPSILON and tick_ofi is None:
            return _order_flow_unavailable(
                "no traded volume in the candle series (zero-volume / index spot "
                "feed) and no live tick order flow — candle-derived order flow "
                "cannot be trusted",
                symbol,
                timeframe,
            )

        # If every candle-derived proxy is null AND the Tick_OFI is unavailable,
        # order flow is genuinely unavailable rather than a default label
        # (Requirement 4.6). ``up_volume`` / ``down_volume`` are always finite
        # sums (>= 0.0), so the null-able proxies are the deciding set here.
        all_proxies_null = (
            candle_delta is None
            and cvd_proxy is None
            and buying_pressure_ratio is None
        )
        if all_proxies_null and tick_ofi is None:
            return _order_flow_unavailable(
                "no order-flow measure could be computed",
                symbol,
                timeframe,
            )

        # ── Classification (tick-first) and alignment derivation ─────────────
        order_flow_state = classify_order_flow_state(
            tick_ofi, buying_pressure_ratio, config
        )
        alignment = derive_alignment(order_flow_state, proposed_direction)

        label: dict = {
            "order_flow_state": order_flow_state,
            "alignment": alignment,
            "measures": measures,
            "tick_ofi": tick_ofi,
            "live_tick_contributed": live_tick_contributed,
        }
        if symbol is not None:
            label["symbol"] = symbol
        if timeframe is not None:
            label["timeframe"] = timeframe
        label["candles_used"] = valid_count
        label["ticks_used"] = ticks_used
        return label
    except Exception as exc:  # pragma: no cover - defensive; classifier is pure
        # The classifier must never raise into its callers (Requirement 4). Any
        # unexpected failure degrades to an honest Unavailable_Marker rather than
        # an exception or a fabricated label.
        return _order_flow_unavailable(
            f"order-flow classification error: {exc.__class__.__name__}",
            symbol,
            timeframe,
        )
