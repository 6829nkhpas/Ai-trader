"""Relative_Strength_Calculator — pure-math relative-strength & index context.

The Deep Quant agent ("Alpha-Quant") classifies each symbol's setup in
isolation; it has no awareness of the broader market. A veteran trader's
discipline is the opposite — trade the *strongest* stock *with* the market,
never fight the index, and never buy a laggard in a falling market or short a
leader in a rising one. This module implements that discipline as a cheap,
deterministic calculator that, from candle data the system already retrieves,
measures the benchmark index's own direction, the symbol's relative strength
versus that benchmark, and the symbol-vs-index correlation/beta, then labels a
Relative_Strength_State and an Alignment of a proposed trade direction with that
context.

Scope discipline (Requirement 13): everything here is a *filter / context aid*,
never a trade generator. The calculator maps a symbol candle sequence, a
benchmark candle sequence, and a resolved configuration to a structured
Relative_Strength_Label (or an honest Unavailable_Marker); it never emits
BUY/SELL/HOLD, never blocks a trade, and never fabricates data.

Purity (Requirement 1): this module is pure Python. It performs zero network
calls, reads zero data sources other than its three provided inputs (symbol
candles + benchmark candles + config), and touches no file/clock. Parameter
*resolution* (``resolve_rs_config`` / ``resolve_benchmark``) is the only place
the process environment is read, and it does so deterministically, with
documented defaults.

This file (task 1.1) provides the parameter-resolution foundation: the
documented default constants, the frozen ``RSConfig`` dataclass, the
``resolve_rs_config()`` resolver, and the ``Benchmark_Map`` (``DEFAULT_BENCHMARK``
/ ``DEFAULT_BENCHMARK_MAP`` / ``resolve_benchmark``). The time-alignment, measure,
and classification functions are added in subsequent tasks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / out of
# range (Requirement 12.2-12.4). These are the single source of truth for the
# defaults on BOTH the live tool path and the backtest path (Requirement 12.6).

DEFAULT_RS_LOOKBACK = 20            # bars over which RS ratio slope & relative return are measured
DEFAULT_RS_CORR_WINDOW = 30        # bars over which correlation & beta are measured
DEFAULT_RS_LEADER_CUTOFF = 0.02    # relative-return >= this => leader (outperforming)
DEFAULT_RS_LAGGARD_CUTOFF = -0.02  # relative-return <= this => laggard (underperforming)
DEFAULT_RS_INDEX_FLAT_BAND = 0.005  # |index return| <= this over lookback => flat
DEFAULT_RS_MIN_CANDLES = 30        # minimum aligned candles required to classify

# ── Environment variable names ────────────────────────────────────────────────
ENV_RS_LOOKBACK = "RS_LOOKBACK"
ENV_RS_CORR_WINDOW = "RS_CORR_WINDOW"
ENV_RS_LEADER_CUTOFF = "RS_LEADER_CUTOFF"
ENV_RS_LAGGARD_CUTOFF = "RS_LAGGARD_CUTOFF"
ENV_RS_INDEX_FLAT_BAND = "RS_INDEX_FLAT_BAND"
ENV_RS_MIN_CANDLES = "RS_MIN_CANDLES"

# Benchmark_Map env vars.
ENV_RS_DEFAULT_BENCHMARK = "RS_DEFAULT_BENCHMARK"
ENV_RS_BENCHMARK_MAP = "RS_BENCHMARK_MAP"

# ── Valid ranges (inclusive) ──────────────────────────────────────────────────
# Periods/counts are integers >= 2 with no upper bound; the cutoffs are decimals
# in [-1.0, 1.0]; the flat band is a decimal in [0.0, 1.0] (Requirement 12.1).
_PERIOD_MIN = 2
_CUTOFF_MIN = -1.0
_CUTOFF_MAX = 1.0
_FLAT_BAND_MIN = 0.0
_FLAT_BAND_MAX = 1.0


@dataclass(frozen=True)
class RSConfig:
    """The resolved, validated parameter set used to classify relative strength.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the calculator's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on both
    the tool path and the backtest path (Requirement 12.6).
    """

    lookback: int
    corr_window: int
    leader_cutoff: float
    laggard_cutoff: float
    index_flat_band: float
    min_candles: int

    @property
    def largest_lookback(self) -> int:
        """Max aligned candles any single measure requires (drives the gate).

        The relative-return / RS-ratio-slope measures need ``lookback`` bars and
        the correlation / beta measures need ``corr_window`` bars; each is a
        return-based measure so it needs one extra base bar. The classifier
        additionally requires at least ``min_candles``; ``classify_relative_
        strength`` gates on the max of the two.
        """
        return max(self.lookback, self.corr_window) + 1


# ── Default Benchmark_Map ─────────────────────────────────────────────────────
# Documented default symbol -> benchmark entries (Requirements 2.1, 2.2).
# Extended via ``RS_BENCHMARK_MAP`` without code changes (Requirement 2.3). Only
# benchmarks whose candles are available in the data source are given defaults;
# any unmapped symbol resolves to ``DEFAULT_BENCHMARK`` (Requirement 2.2).
#
# The value is the RECOGNISABLE Benchmark_Index identity ("BANKNIFTY"), which is
# what the tool reports and what the options path maps to a chain. It is NOT the
# tradingsymbol the benchmark's CANDLES are stored under — that is the NSE spot
# name ("NIFTY BANK"), and `benchmark_candle_name` bridges the two at the one
# place it matters (the relative-strength candle fetch). See that function for why
# leaving the two conflated made relative strength unavailable for every bank
# stock.
DEFAULT_BENCHMARK = "NIFTY 50"
DEFAULT_BENCHMARK_MAP = {
    "HDFCBANK": "BANKNIFTY",
    "ICICIBANK": "BANKNIFTY",
    "SBIN": "BANKNIFTY",
    "AXISBANK": "BANKNIFTY",
    "KOTAKBANK": "BANKNIFTY",
    "INDUSINDBK": "BANKNIFTY",
    "BANKBARODA": "BANKNIFTY",
    "PNB": "BANKNIFTY",
    "FEDERALBNK": "BANKNIFTY",
    "AUBANK": "BANKNIFTY",
}

# NFO derivative name -> NSE spot tradingsymbol the benchmark's candles are stored
# under. The reverse of `tools.py::_OPTIONS_CHAIN_NAME` (spot -> NFO), needed for
# the opposite reason: options READ the option chain by the NFO name, relative
# strength READS the index candles by the spot name.
_BENCHMARK_CANDLE_NAME = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}


def benchmark_candle_name(benchmark: str) -> str:
    """Map a Benchmark_Index to the tradingsymbol its CANDLES are stored under.

    A benchmark carries a recognisable identity ("BANKNIFTY") but its candles live
    in QuestDB under the NSE spot tradingsymbol ("NIFTY BANK") — measured: 227k
    rows for "NIFTY BANK", ZERO for "BANKNIFTY". The relative-strength tool fetches
    the benchmark's candles, so it must translate here or every bank-benchmarked
    stock (HDFCBANK, ICICIBANK, SBIN, …) fetches a name with no candles and
    degrades to unavailable — the reported "benchmark BANKNIFTY candle retrieval
    returned no usable data". "NIFTY 50" was already the spot name, which is why
    NIFTY-benchmarked stocks worked and only the bank ones failed.

    NFO names become their spot tradingsymbol; spot names, stocks and unknown names
    pass through unchanged. Pure and total; never raises.
    """
    if not isinstance(benchmark, str) or not benchmark.strip():
        return benchmark if isinstance(benchmark, str) else ""
    return _BENCHMARK_CANDLE_NAME.get(benchmark.strip().upper(), benchmark.strip())


def _resolve_float(env_name: str, default: float, low: float, high: float) -> float:
    """Resolve one float parameter from its own env var (Requirement 12.1-12.4).

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
    """Resolve one integer parameter from its own env var (Requirement 12.1-12.4).

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


def resolve_rs_config() -> RSConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 12):
      * unset / empty            -> documented default (R12.2)
      * unparseable as its type  -> documented default (never raises) (R12.3)
      * parses but out of range  -> documented default (never raises) (R12.4)
      * laggard_cutoff >= leader_cutoff -> BOTH cutoffs revert to their
        documented defaults together (R12.5)

    The same function is called on the tool path and the backtest path so the
    resolved values are identical for identical environment (Requirement 12.6).
    This function NEVER raises.
    """
    lookback = _resolve_int(ENV_RS_LOOKBACK, DEFAULT_RS_LOOKBACK, _PERIOD_MIN)
    corr_window = _resolve_int(ENV_RS_CORR_WINDOW, DEFAULT_RS_CORR_WINDOW, _PERIOD_MIN)
    min_candles = _resolve_int(ENV_RS_MIN_CANDLES, DEFAULT_RS_MIN_CANDLES, _PERIOD_MIN)

    leader_cutoff = _resolve_float(
        ENV_RS_LEADER_CUTOFF, DEFAULT_RS_LEADER_CUTOFF, _CUTOFF_MIN, _CUTOFF_MAX
    )
    laggard_cutoff = _resolve_float(
        ENV_RS_LAGGARD_CUTOFF, DEFAULT_RS_LAGGARD_CUTOFF, _CUTOFF_MIN, _CUTOFF_MAX
    )
    index_flat_band = _resolve_float(
        ENV_RS_INDEX_FLAT_BAND, DEFAULT_RS_INDEX_FLAT_BAND, _FLAT_BAND_MIN, _FLAT_BAND_MAX
    )

    # Enforce the strict laggard < leader ordering. If it does not hold (after
    # the per-parameter resolution above), BOTH cutoffs revert to their
    # documented defaults together (Requirement 12.5).
    if laggard_cutoff >= leader_cutoff:
        leader_cutoff = DEFAULT_RS_LEADER_CUTOFF
        laggard_cutoff = DEFAULT_RS_LAGGARD_CUTOFF

    return RSConfig(
        lookback=lookback,
        corr_window=corr_window,
        leader_cutoff=leader_cutoff,
        laggard_cutoff=laggard_cutoff,
        index_flat_band=index_flat_band,
        min_candles=min_candles,
    )


# ── Benchmark_Map resolution ──────────────────────────────────────────────────


def _parse_benchmark_overrides(raw: Optional[str]) -> dict:
    """Parse the ``RS_BENCHMARK_MAP`` override string into a mapping.

    Format: ``SYMBOL:BENCHMARK,SYMBOL:BENCHMARK,...`` (Requirement 2.3). Entries
    that are blank or malformed (missing the ``:`` separator, or an empty symbol
    / benchmark side) are skipped rather than raising. Symbol keys are upper-
    cased and stripped so resolution is case-insensitive on the symbol side.
    Never raises.
    """
    overrides: dict = {}
    if not raw or not raw.strip():
        return overrides
    for entry in raw.split(","):
        if ":" not in entry:
            continue
        sym, _, bench = entry.partition(":")
        sym = sym.strip().upper()
        bench = bench.strip()
        if sym and bench:
            overrides[sym] = bench
    return overrides


def resolve_benchmark(symbol: str, explicit: Optional[str] = None) -> str:
    """Resolve the Benchmark_Index for a symbol (Requirement 2).

    Precedence:
      1. An explicit non-empty ``explicit`` argument wins (Requirement 4.2).
      2. Otherwise the configurable ``RS_BENCHMARK_MAP`` override / the documented
         ``DEFAULT_BENCHMARK_MAP`` entry for the symbol (Requirements 2.1, 2.3).
      3. Otherwise the documented default Benchmark_Index ``DEFAULT_BENCHMARK``
         (Requirement 2.2), itself overridable via ``RS_DEFAULT_BENCHMARK``.

    Resolution is case-insensitive on the symbol side. NEVER raises.
    """
    # 1. An explicit non-empty benchmark argument always wins.
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    # Documented default Benchmark_Index (overridable via env).
    default_raw = os.getenv(ENV_RS_DEFAULT_BENCHMARK)
    default_benchmark = (
        default_raw.strip()
        if isinstance(default_raw, str) and default_raw.strip()
        else DEFAULT_BENCHMARK
    )

    if not isinstance(symbol, str) or not symbol.strip():
        return default_benchmark
    key = symbol.strip().upper()

    # 2. Configurable overrides take precedence over the documented defaults so
    #    the map is extensible without code changes (Requirement 2.3).
    overrides = _parse_benchmark_overrides(os.getenv(ENV_RS_BENCHMARK_MAP))
    if key in overrides:
        return overrides[key]
    if key in DEFAULT_BENCHMARK_MAP:
        return DEFAULT_BENCHMARK_MAP[key]

    # 3. Fall back to the documented default Benchmark_Index (Requirement 2.2).
    return default_benchmark


# ── Candle validation & alignment helpers (Requirements 3.2, 3.7) ─────────────
# A candle is a "dict-like" OHLCV record from the Rust Tool_Server carrying a
# ``timestamp_ms`` plus ``open`` / ``high`` / ``low`` / ``close`` / ``volume``
# (matching how ``journal.py`` / ``backtest.py`` / ``tools.py`` read candles via
# ``c.get(...)``). A candle is excluded from EVERY Relative_Strength_Measure
# computation when any OHLCV field it carries — including the timestamp — is
# non-numeric or non-finite (NaN / +/-inf), so the measures operate only on clean
# data (Requirement 3.2). None of these helpers mutate their inputs.

# Index of the close price within a parsed ``(ts, o, h, l, c)`` row.
_TS_IDX = 0
_CLOSE_IDX = 4


def _is_finite_number(v) -> bool:
    """True for a finite real number; ``bool`` is excluded (matches the repo's
    ``_is_num`` convention in ``journal.py`` / ``backtest.py`` / ``regime.py``)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_row(candle) -> Optional[tuple]:
    """Read ``(timestamp, open, high, low, close)`` from one dict-like candle.

    Returns the five values as floats when ``timestamp_ms`` / ``open`` / ``high``
    / ``low`` / ``close`` are each finite numbers (and any present ``volume`` is
    also a finite number); returns ``None`` for a candle carrying a non-finite /
    non-numeric OHLCV field (including the timestamp), an absent required field,
    or a non-mapping value (Requirement 3.2). Reads the candle without modifying
    it.
    """
    get = getattr(candle, "get", None)
    if not callable(get):
        return None
    ts = get("timestamp_ms")
    o = get("open")
    h = get("high")
    low = get("low")
    c = get("close")
    if not (
        _is_finite_number(ts)
        and _is_finite_number(o)
        and _is_finite_number(h)
        and _is_finite_number(low)
        and _is_finite_number(c)
    ):
        return None
    # ``volume`` is part of the OHLCV record: if present but non-finite /
    # non-numeric, the candle is excluded too (Requirement 3.2). An absent
    # volume does not, by itself, invalidate an otherwise-clean candle.
    v = get("volume")
    if v is not None and not _is_finite_number(v):
        return None
    return (float(ts), float(o), float(h), float(low), float(c))


def _iter_candles(candles):
    """Yield from a candle sequence, tolerating ``None`` / non-iterable input.

    Returns an empty iterator (rather than raising) for a ``None`` or
    non-iterable input, preserving the calculator's never-raise guarantee.
    """
    if candles is None:
        return iter(())
    try:
        return iter(candles)
    except TypeError:
        return iter(())


def _valid_period(period) -> bool:
    """True when ``period`` is a usable lookback (an integer >= 2).

    Every cross-series measure needs at least two points to be meaningful (a
    return needs a base bar; a slope/correlation needs two observations), so the
    minimum usable lookback is 2 — matching ``_PERIOD_MIN``.
    """
    return isinstance(period, int) and not isinstance(period, bool) and period >= _PERIOD_MIN


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to ``[low, high]`` (Requirement 3.4)."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def time_align(symbol_candles, benchmark_candles) -> tuple:
    """Project both candle sequences to equal-length, timestamp-aligned rows.

    Returns ``(symbol_rows, benchmark_rows)`` — two equal-length lists of parsed
    ``(timestamp, open, high, low, close)`` rows whose timestamps are common to
    BOTH input sequences, in ascending timestamp order (Requirement 3.7). So that
    a mismatched-length or mismatched-timestamp pair of sequences cannot corrupt
    the cross-series measures, only the intersection of timestamps survives.

    Candles with non-finite / non-numeric OHLCV fields — including the timestamp
    — are dropped before intersection (Requirement 3.2). When a sequence carries
    more than one valid candle for the same timestamp the last such candle wins
    (deterministic for identical input). Pure: never mutates either input; reads
    each candle via ``.get`` only. Never raises.
    """
    sym_by_ts: dict = {}
    for candle in _iter_candles(symbol_candles):
        row = _parse_row(candle)
        if row is not None:
            sym_by_ts[row[_TS_IDX]] = row

    bench_by_ts: dict = {}
    for candle in _iter_candles(benchmark_candles):
        row = _parse_row(candle)
        if row is not None:
            bench_by_ts[row[_TS_IDX]] = row

    common = sorted(set(sym_by_ts) & set(bench_by_ts))
    symbol_rows = [sym_by_ts[ts] for ts in common]
    benchmark_rows = [bench_by_ts[ts] for ts in common]
    return symbol_rows, benchmark_rows


def _linreg_slope(ys: list) -> Optional[float]:
    """Least-squares slope of ``ys`` against its own integer index 0..n-1.

    Returns ``None`` when there are fewer than two points or the abscissa has
    zero variance (a degenerate fit — Requirement 3.5). Pure; never raises.
    """
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None  # zero abscissa variance -> slope undefined (Requirement 3.5)
    numer = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    return numer / denom


def _aligned_returns(symbol_rows, benchmark_rows, window) -> tuple:
    """Paired per-bar simple returns over the last ``window`` aligned bars.

    Uses the most recent ``window + 1`` aligned rows to produce up to ``window``
    paired ``(symbol_return, benchmark_return)`` observations. A consecutive bar
    pair is included only when BOTH base closes are non-zero, so the two return
    series stay index-aligned (Requirement 3.5/3.7). Returns ``(None, None)``
    when there are too few aligned rows. Pure; never raises.
    """
    n = min(len(symbol_rows), len(benchmark_rows))
    need = window + 1
    if n < need:
        return None, None
    sub_sym = symbol_rows[-need:]
    sub_bench = benchmark_rows[-need:]
    sym_ret: list = []
    bench_ret: list = []
    for i in range(1, len(sub_sym)):
        s_prev = sub_sym[i - 1][_CLOSE_IDX]
        b_prev = sub_bench[i - 1][_CLOSE_IDX]
        if s_prev == 0 or b_prev == 0:
            continue  # zero base price -> that bar's return is undefined
        sym_ret.append((sub_sym[i][_CLOSE_IDX] - s_prev) / s_prev)
        bench_ret.append((sub_bench[i][_CLOSE_IDX] - b_prev) / b_prev)
    return sym_ret, bench_ret


# ── Relative_Strength_Measure functions (pure) ───────────────────────────────
# Each function operates only on time-aligned rows (the output of ``time_align``,
# which has already excluded non-finite candles — Requirement 3.2). Each returns
# a finite ``float`` when the measure is computable, ``None`` when its
# denominator is zero (zero benchmark price / zero return variance —
# Requirement 3.5), and clamps bounded measures into range (Requirement 3.4).
# None mutates its inputs and none raises.


def compute_rs_ratio_slope(symbol_rows, benchmark_rows, lookback) -> tuple:
    """Latest RS ratio and the slope of that ratio over ``lookback`` bars (R1.3).

    The RS ratio is ``symbol_close / benchmark_close``. The first element is the
    ratio at the most recent aligned bar (``None`` on a zero benchmark price —
    Requirement 3.5); the second is the least-squares slope of the ratio series
    over the most recent ``lookback`` aligned bars (``None`` when the ratio
    series is too short or degenerate — Requirement 3.5). Pure; never raises.
    """
    if not _valid_period(lookback):
        return (None, None)
    n = min(len(symbol_rows), len(benchmark_rows))
    if n == 0:
        return (None, None)

    # Latest RS ratio at the most recent aligned bar.
    bench_last = benchmark_rows[n - 1][_CLOSE_IDX]
    sym_last = symbol_rows[n - 1][_CLOSE_IDX]
    latest_ratio = (sym_last / bench_last) if bench_last != 0 else None

    # Slope of the ratio series over the most recent ``lookback`` aligned bars.
    sub_sym = symbol_rows[-lookback:]
    sub_bench = benchmark_rows[-lookback:]
    ratios: list = []
    for s_row, b_row in zip(sub_sym, sub_bench):
        bc = b_row[_CLOSE_IDX]
        if bc == 0:
            continue  # zero benchmark price -> ratio undefined at this bar
        ratios.append(s_row[_CLOSE_IDX] / bc)
    slope = _linreg_slope(ratios)
    return (latest_ratio, slope)


def compute_relative_return(symbol_rows, benchmark_rows, lookback) -> Optional[float]:
    """Symbol return minus benchmark return over ``lookback`` aligned bars (R1.4).

    Each leg's return is ``(close_last - close_base) / close_base`` over the most
    recent ``lookback + 1`` aligned bars. Returns ``None`` when either base close
    is zero (Requirement 3.5) or there are too few aligned bars. Pure; never
    raises.
    """
    if not _valid_period(lookback):
        return None
    need = lookback + 1
    if len(symbol_rows) < need or len(benchmark_rows) < need:
        return None
    sym_base = symbol_rows[-need][_CLOSE_IDX]
    bench_base = benchmark_rows[-need][_CLOSE_IDX]
    if sym_base == 0 or bench_base == 0:
        return None  # zero base price -> return undefined (Requirement 3.5)
    sym_ret = (symbol_rows[-1][_CLOSE_IDX] - sym_base) / sym_base
    bench_ret = (benchmark_rows[-1][_CLOSE_IDX] - bench_base) / bench_base
    return sym_ret - bench_ret


def compute_correlation(symbol_rows, benchmark_rows, window) -> Optional[float]:
    """Pearson correlation of per-bar returns over ``window`` aligned bars (R1.5).

    Clamped to ``[-1.0, 1.0]`` (Requirements 1.5, 3.4). Returns ``None`` when
    either return series has zero variance (Requirement 3.5) or there are too few
    aligned bars. Pure; never raises.
    """
    if not _valid_period(window):
        return None
    sym_ret, bench_ret = _aligned_returns(symbol_rows, benchmark_rows, window)
    if sym_ret is None or len(sym_ret) < 2:
        return None
    n = len(sym_ret)
    mean_s = sum(sym_ret) / n
    mean_b = sum(bench_ret) / n
    var_s = sum((s - mean_s) ** 2 for s in sym_ret)
    var_b = sum((b - mean_b) ** 2 for b in bench_ret)
    if var_s <= 0 or var_b <= 0:
        return None  # zero variance -> correlation undefined (Requirement 3.5)
    cov = sum((sym_ret[i] - mean_s) * (bench_ret[i] - mean_b) for i in range(n))
    corr = cov / math.sqrt(var_s * var_b)
    return _clamp(corr, -1.0, 1.0)


def compute_beta(symbol_rows, benchmark_rows, window) -> Optional[float]:
    """Beta of the symbol versus the benchmark over ``window`` aligned bars (R1.5).

    ``beta = cov(symbol_ret, benchmark_ret) / var(benchmark_ret)`` over the
    per-bar returns (the common ``1/n`` factor cancels). Returns ``None`` when
    the benchmark-return variance is zero (Requirement 3.5) or there are too few
    aligned bars. Pure; never raises.
    """
    if not _valid_period(window):
        return None
    sym_ret, bench_ret = _aligned_returns(symbol_rows, benchmark_rows, window)
    if sym_ret is None or len(sym_ret) < 2:
        return None
    n = len(sym_ret)
    mean_s = sum(sym_ret) / n
    mean_b = sum(bench_ret) / n
    var_b = sum((b - mean_b) ** 2 for b in bench_ret)
    if var_b <= 0:
        return None  # zero benchmark variance -> beta undefined (Requirement 3.5)
    cov = sum((sym_ret[i] - mean_s) * (bench_ret[i] - mean_b) for i in range(n))
    return cov / var_b


def compute_index_return(benchmark_rows, lookback) -> Optional[float]:
    """Benchmark return over ``lookback`` aligned bars; drives Index_Direction.

    ``(close_last - close_base) / close_base`` over the most recent ``lookback +
    1`` aligned benchmark bars. Returns ``None`` when the base close is zero
    (Requirement 3.5) or there are too few bars. Pure; never raises.
    """
    if not _valid_period(lookback):
        return None
    need = lookback + 1
    if len(benchmark_rows) < need:
        return None
    base = benchmark_rows[-need][_CLOSE_IDX]
    if base == 0:
        return None  # zero base price -> return undefined (Requirement 3.5)
    return (benchmark_rows[-1][_CLOSE_IDX] - base) / base


# ── Classification functions (pure, total) ────────────────────────────────────
# Each classifier maps a (possibly ``None``) measure plus the resolved config to
# exactly one categorical value, per the design's total mapping tables. Every
# input — including ``None`` for any measure and an absent proposed direction —
# maps to exactly one value, so the classifiers are total. None mutates its
# inputs and none raises.


def classify_index_direction(index_return: Optional[float], config: RSConfig) -> str:
    """Classify the Index_Direction (Requirement 1.6).

    Returns exactly one of ``'up'`` / ``'down'`` / ``'flat'`` from the benchmark
    return over the configured lookback and the configured flat band, per the
    design's Index_Direction mapping table:

      * ``index_return > +index_flat_band`` -> ``'up'``
      * ``index_return < -index_flat_band`` -> ``'down'``
      * otherwise (within +/- the band, or ``index_return`` is ``None``)
        -> ``'flat'``

    Total: every input (including ``None``) maps to exactly one Index_Direction.
    Pure; never raises.
    """
    if index_return is None:
        return "flat"
    if index_return > config.index_flat_band:
        return "up"
    if index_return < -config.index_flat_band:
        return "down"
    return "flat"


def classify_relative_strength_state(
    relative_return: Optional[float],
    rs_ratio_slope: Optional[float],
    config: RSConfig,
) -> str:
    """Classify the Relative_Strength_State (Requirement 1.7).

    Returns exactly one of ``'leader'`` / ``'inline'`` / ``'laggard'``. The
    primary signal is the relative-return measure compared against the configured
    leader/laggard cutoffs (``rs_ratio_slope`` corroborates but does not, on its
    own, override the relative-return classification), per the design's
    Relative_Strength_State mapping table:

      * ``relative_return >= leader_cutoff``  -> ``'leader'``
      * ``relative_return <= laggard_cutoff`` -> ``'laggard'``
      * otherwise (between the cutoffs, or ``relative_return`` is ``None``)
        -> ``'inline'``

    The cutoffs satisfy ``laggard_cutoff < leader_cutoff`` (enforced at
    resolution), so the leader / laggard branches cannot both fire. Total: every
    input (including ``None``) maps to exactly one Relative_Strength_State. Pure;
    never raises.
    """
    if relative_return is None:
        return "inline"
    if relative_return >= config.leader_cutoff:
        return "leader"
    if relative_return <= config.laggard_cutoff:
        return "laggard"
    return "inline"


# Alignment lookup tables over (Index_Direction, Relative_Strength_State) for a
# BUY and for a SELL proposed direction (design's Alignment derivation tables).
# Any combination absent from a table is ``neutral`` so the derivation is total.
_ALIGNMENT_BUY = {
    ("up", "leader"): "aligned",
    ("down", "laggard"): "misaligned",
}
_ALIGNMENT_SELL = {
    ("up", "leader"): "misaligned",
    ("down", "laggard"): "aligned",
}


def derive_alignment(
    index_direction: str,
    rs_state: str,
    proposed_direction: Optional[str],
) -> str:
    """Derive the Alignment (Requirements 1.8, 1.9).

    Returns exactly one of ``'aligned'`` / ``'misaligned'`` / ``'neutral'``,
    expressing the veteran principle: trade the strongest names *with* the
    market. Total over every (Index_Direction x Relative_Strength_State x
    proposed_direction) combination:

      * A BUY into a ``leader`` while the index is ``up``  -> ``'aligned'``;
        a BUY into a ``laggard`` while the index is ``down`` -> ``'misaligned'``.
      * A SELL of a ``leader`` while the index is ``up``    -> ``'misaligned'``;
        a SELL of a ``laggard`` while the index is ``down``  -> ``'aligned'``.
      * Every other (direction, index, state) combination   -> ``'neutral'``.
      * An absent / ``None`` / non-directional (e.g. HOLD) proposed direction
        -> ``'neutral'`` (Requirement 1.9).

    Pure; never raises.
    """
    if not isinstance(proposed_direction, str):
        return "neutral"
    direction = proposed_direction.strip().upper()
    if direction == "BUY":
        return _ALIGNMENT_BUY.get((index_direction, rs_state), "neutral")
    if direction == "SELL":
        return _ALIGNMENT_SELL.get((index_direction, rs_state), "neutral")
    return "neutral"


# ── Unavailable_Marker helper ─────────────────────────────────────────────────


def _rs_unavailable(
    reason: str,
    symbol: Optional[str],
    timeframe: Optional[str],
    benchmark: Optional[str],
) -> dict:
    """Build an honest Unavailable_Marker (Requirements 3.1, 3.6, 5.2, 5.3).

    Index_Direction / Relative_Strength_State / Alignment are *omitted* (never
    defaulted or fabricated — Requirement 5.3). ``symbol`` / ``timeframe`` /
    ``benchmark`` are included only when the caller provides them (the
    classifier itself has no knowledge of them). The ``reason`` cites the cause;
    for the insufficient-data case it includes the count of aligned candles
    available and the count required (Requirements 3.1, 5.2).
    """
    marker: dict = {}
    if symbol is not None:
        marker["symbol"] = symbol
    if timeframe is not None:
        marker["timeframe"] = timeframe
    if benchmark is not None:
        marker["benchmark"] = benchmark
    marker["unavailable"] = True
    marker["reason"] = reason
    return marker


def classify_relative_strength(
    symbol_candles,
    benchmark_candles,
    config: RSConfig,
    proposed_direction: Optional[str] = None,
    symbol: Optional[str] = None,
    benchmark: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Top-level entry point: map candles + config to a label or marker.

    Returns either a Relative_Strength_Label dict (``index_direction`` /
    ``relative_strength_state`` / ``alignment`` / ``measures`` /
    ``aligned_candles`` — plus ``benchmark`` / ``symbol`` / ``timeframe`` when the
    caller supplies them) or an Unavailable_Marker dict.

    Behaviour (Requirements 1, 3, 5, 13):
      * Time-aligns the symbol and benchmark candles by timestamp first, so only
        candles common to BOTH sequences feed the cross-series measures
        (Requirement 3.7); non-finite/non-numeric candles are excluded by the
        alignment step (Requirement 3.2).
      * Computes every Relative_Strength_Measure from those valid, aligned
        candles only (Requirements 1.3, 1.4, 1.5).
      * Returns an Unavailable_Marker — citing the count of aligned candles
        available and the count required — when the aligned-candle count is below
        the gate ``max(min_candles, largest_lookback)`` (Requirements 3.1, 5.2).
      * Returns an Unavailable_Marker citing "no relative-strength measure could
        be computed" when every named measure is ``None`` (Requirement 3.6).
      * Each reported measure is a finite number or ``null`` (Requirements 3.3,
        3.5); bounded measures are clamped by their measure function
        (Requirement 3.4).
      * Pure (no input mutation — Requirements 1.1, 1.10), deterministic
        (Requirement 1.2), and non-raising (Requirement 3). Emits ONLY a label or
        marker — never a BUY/SELL/HOLD action, conviction, or decision field
        (Requirements 13.1, 13.3).
    """
    try:
        # Time-align first: every cross-series measure is computed only from
        # candles whose timestamps are common to both sequences (Requirement 3.7).
        # The two returned lists are equal-length, so either length is the
        # aligned-candle count.
        symbol_rows, benchmark_rows = time_align(symbol_candles, benchmark_candles)
        aligned = len(symbol_rows)

        # Sufficiency gate: the classifier requires at least ``min_candles`` and
        # at least the largest single-measure lookback (Requirements 3.1, 5.2).
        required = max(config.min_candles, config.largest_lookback)
        if aligned < required:
            return _rs_unavailable(
                f"insufficient aligned data: {aligned} aligned candles available, "
                f"{required} required",
                symbol,
                timeframe,
                benchmark,
            )

        # Compute each named Relative_Strength_Measure from the aligned rows.
        rs_ratio, rs_ratio_slope = compute_rs_ratio_slope(
            symbol_rows, benchmark_rows, config.lookback
        )
        relative_return = compute_relative_return(
            symbol_rows, benchmark_rows, config.lookback
        )
        correlation = compute_correlation(symbol_rows, benchmark_rows, config.corr_window)
        beta = compute_beta(symbol_rows, benchmark_rows, config.corr_window)
        # ``index_return`` drives Index_Direction only; it is not a reported
        # Relative_Strength_Measure, so it stays out of the ``measures`` dict.
        index_return = compute_index_return(benchmark_rows, config.lookback)

        measures = {
            "rs_ratio": rs_ratio,
            "rs_ratio_slope": rs_ratio_slope,
            "relative_return": relative_return,
            "correlation": correlation,
            "beta": beta,
        }

        # If no measure could be computed at all, relative strength is genuinely
        # unavailable rather than a default label (Requirement 3.6).
        if all(value is None for value in measures.values()):
            return _rs_unavailable(
                "no relative-strength measure could be computed",
                symbol,
                timeframe,
                benchmark,
            )

        index_direction = classify_index_direction(index_return, config)
        relative_strength_state = classify_relative_strength_state(
            relative_return, rs_ratio_slope, config
        )
        alignment = derive_alignment(
            index_direction, relative_strength_state, proposed_direction
        )

        label: dict = {
            "index_direction": index_direction,
            "relative_strength_state": relative_strength_state,
            "alignment": alignment,
            "measures": measures,
        }
        if benchmark is not None:
            label["benchmark"] = benchmark
        if symbol is not None:
            label["symbol"] = symbol
        if timeframe is not None:
            label["timeframe"] = timeframe
        label["aligned_candles"] = aligned
        return label
    except Exception as exc:  # pragma: no cover - defensive; classifier is pure
        # The classifier must never raise into its callers (Requirement 3). Any
        # unexpected failure degrades to an honest Unavailable_Marker rather than
        # an exception or a fabricated label.
        return _rs_unavailable(
            f"relative-strength classification error: {exc.__class__.__name__}",
            symbol,
            timeframe,
            benchmark,
        )
