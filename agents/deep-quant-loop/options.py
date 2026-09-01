"""Options_Analytics_Engine — pure-math options-chain analytics for the Deep Quant agent.

This is **Phase F2 — the Options Analytics Engine**. Phase F1 (Options Data
Foundation) put F&O instruments, open interest, option ticks, and periodic chain
snapshots into QuestDB. F2 turns that raw data into the signals a derivatives
trader actually reads off the chain: **Put-Call Ratio** (OI and volume), **Max
Pain**, per-strike **OI Buildup** classification (and call/put aggregates),
per-option **Implied Volatility** (Black-Scholes inversion) and **Greeks**
(delta, gamma, theta, vega), the **IV Skew** across strikes, **OI-Wall**
support/resistance, and the **Futures Basis**. These assemble into a single
``Options_Analytics_Result``.

Consistent with the project's established pattern, this engine mirrors the
structure of ``regime.py`` / ``rs.py`` / ``order_flow.py``:

- The genuinely numeric cores (Black-Scholes price/IV/Greeks, PCR, max-pain,
  OI-buildup classification, IV-skew, OI-walls, basis) are **deterministic, pure
  functions** of their arguments. They take in-memory snapshots and a resolved
  configuration, never touch I/O, a clock, or globals, and never raise.
- A thin **read/query layer** (added in a later task) reads the F1 QuestDB
  tables over the same QuestDB HTTP ``/exec`` API that ``tools.py`` and
  ``backtest.py`` already use. This layer is the only place that performs I/O and
  is cleanly separated so the analytics can be property-tested on in-memory
  snapshots with no QuestDB (Requirement 5.3).
- Configuration is resolved **once, deterministically, from environment
  variables with documented defaults**, following the ``resolve_*_config()``
  convention in ``regime.py`` / ``rs.py``.

The engine **degrades gracefully**: missing or insufficient chain data yields an
honest ``Unavailable_Marker`` (``{"unavailable": true, "reason": ...}``); any
single analytic that cannot be computed is represented as ``null`` while the rest
of the result is still returned. It **never** fabricates a value and **never**
raises into its caller.

Scope discipline (Requirement 10): F2 is **analytics only** — it adds no agent
tool (Phase F3) and no UI (Phase F4) and emits no BUY/SELL/HOLD. Its output is
the structured result those later phases will consume.

This file (task 1.1) provides the scaffold: the standard-library-only imports,
the frozen in-memory data models (``StrikeQuote`` / ``ChainSnapshot``), and the
frozen ``OptionsConfig`` dataclass. The config resolver, Black-Scholes core, pure
chain analytics, read layer, and orchestrator are added in subsequent tasks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx  # used only by the read/query layer; the pure analytic core has no I/O


# ── QuestDB read endpoint (the read/query layer's only outbound dependency) ───
# Resolved once from the environment with the same default the rest of the agent
# uses (``tools.py`` / ``backtest.py``), so the options read layer talks to the
# same QuestDB HTTP ``/exec`` API the F1 ingestion path writes to.
QUESTDB_HTTP_URL = os.getenv("QUESTDB_HTTP_URL", "http://127.0.0.1:9000")


# ── Documented default configuration values ───────────────────────────────────
# Applied whenever a config env var is unset / empty / unparseable / out of range
# (Requirement 8.2). These are the single source of truth for the defaults.

DEFAULT_RISK_FREE_RATE = 0.065        # annualized risk-free rate (decimal), 6.5%
DEFAULT_IV_TOLERANCE = 1e-6           # IV solver price-convergence tolerance
DEFAULT_IV_MAX_ITERATIONS = 100       # IV solver max bisection iterations
DEFAULT_IV_MIN_VOL = 0.005            # lower volatility bound (decimal)
DEFAULT_IV_MAX_VOL = 5.0              # upper volatility bound (decimal)
DEFAULT_OI_WALL_MIN_OI = 0.0          # min OI for a strike to qualify as a wall
DEFAULT_BUILDUP_OI_EPSILON = 0.0      # |ΔOI| <= this → neutral
DEFAULT_BUILDUP_PRICE_EPSILON = 0.0   # |Δprice| <= this → neutral

# ── Environment variable names ────────────────────────────────────────────────
ENV_RISK_FREE_RATE = "OPTIONS_RISK_FREE_RATE"
ENV_IV_TOLERANCE = "OPTIONS_IV_TOLERANCE"
ENV_IV_MAX_ITERATIONS = "OPTIONS_IV_MAX_ITERATIONS"
ENV_IV_MIN_VOL = "OPTIONS_IV_MIN_VOL"
ENV_IV_MAX_VOL = "OPTIONS_IV_MAX_VOL"
ENV_OI_WALL_MIN_OI = "OPTIONS_OI_WALL_MIN_OI"
ENV_BUILDUP_OI_EPSILON = "OPTIONS_BUILDUP_OI_EPSILON"
ENV_BUILDUP_PRICE_EPSILON = "OPTIONS_BUILDUP_PRICE_EPSILON"

# ── Valid ranges ──────────────────────────────────────────────────────────────
# Documented per the design's Configuration table. ``math.inf`` denotes an
# unbounded upper edge; the lower edge of a range is inclusive unless the
# resolver is told otherwise (the volatility/tolerance lower edges are exclusive
# of zero so a zero-width or zero-tolerance solver can never be configured).
_RATE_MIN, _RATE_MAX = 0.0, 1.0
_TOL_MIN, _TOL_MAX = 0.0, 1.0          # (0.0, 1.0] — lower edge exclusive
_ITER_MIN = 1
_VOL_MIN = 0.0                          # iv_min_vol: [0.0, inf)
_MAXVOL_MIN = 0.0                       # iv_max_vol: (0.0, inf) — lower edge exclusive
_NONNEG_MIN = 0.0                       # [0.0, inf)


@dataclass(frozen=True)
class StrikeQuote:
    """A single strike's CE/PE quote within a chain snapshot.

    Every numeric field is ``Optional[float]`` so that a missing or non-finite /
    non-numeric value (NaN, ±inf, absent volume, ...) is honestly represented as
    ``None`` rather than fabricated (Requirements 6.2, 9.3). Frozen so the pure
    analytic functions cannot mutate their inputs (Requirement 9.2).
    """

    strike: float
    ce_price: Optional[float]
    pe_price: Optional[float]
    ce_oi: Optional[float]
    pe_oi: Optional[float]
    ce_volume: Optional[float]
    pe_volume: Optional[float]


@dataclass(frozen=True)
class ChainSnapshot:
    """A point-in-time per-strike capture of an option chain for an expiry.

    ``strikes`` is an ascending, distinct-strike tuple of ``StrikeQuote`` so the
    discrete strike ladder is well-defined for max-pain and OI-wall computation.
    ``snapshot_ts`` is the epoch-ms timestamp of this snapshot. Frozen so the
    pure analytic functions cannot mutate their inputs (Requirement 9.2).
    """

    underlying: str
    expiry: str
    snapshot_ts: int
    strikes: tuple[StrikeQuote, ...]


@dataclass(frozen=True)
class OptionsConfig:
    """The resolved, validated parameter set used to compute options analytics.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the engine's purity guarantee — Requirement 9.2). For
    identical environment-variable values the resolved configuration is identical
    on repeated invocations (Requirement 8.3). Fields are resolved from
    environment variables with documented defaults by ``resolve_options_config()``
    (added in a later task).
    """

    risk_free_rate: float        # annualized, decimal (e.g. 0.065 = 6.5%)
    iv_tolerance: float          # price-convergence tolerance for the IV solver
    iv_max_iterations: int       # max bisection iterations
    iv_min_vol: float            # lower volatility bound (decimal, e.g. 0.005)
    iv_max_vol: float            # upper volatility bound (decimal, e.g. 5.0)
    oi_wall_min_oi: float        # minimum OI for a strike to qualify as an OI-wall
    buildup_oi_epsilon: float    # |ΔOI| <= this is treated as "no change" → neutral
    buildup_price_epsilon: float # |Δprice| <= this is treated as "no change" → neutral


# ── Configuration resolution (the only place the environment is read) ─────────
# Mirrors the ``_resolve_float`` / ``_resolve_int`` convention in ``regime.py`` /
# ``rs.py``: each parameter is resolved from its own env var, falling back to a
# documented default on unset / empty / unparseable / non-finite / out-of-range,
# and NEVER raising (Requirement 8.1, 8.2).


def _resolve_float(
    env_name: str,
    default: float,
    low: float,
    high: float,
    inclusive_low: bool = True,
) -> float:
    """Resolve one float parameter from its own env var (Requirement 8.1-8.2).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as a
    float, is non-finite (NaN/inf), or parses but falls outside the valid range.
    The upper edge is inclusive (use ``math.inf`` for an unbounded range); the
    lower edge is inclusive when ``inclusive_low`` is true and exclusive
    otherwise (so ``> 0`` ranges reject a configured zero). Never raises.
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
    if value < low or (not inclusive_low and value == low):
        return default
    if value > high:
        return default
    return value


def _resolve_int(env_name: str, default: int, low: int) -> int:
    """Resolve one integer parameter from its own env var (Requirement 8.1-8.2).

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


def resolve_options_config() -> OptionsConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 8):
      * unset / empty            -> documented default
      * unparseable as its type  -> documented default (never raises)
      * non-finite / out of range -> documented default (never raises)
      * iv_min_vol >= iv_max_vol -> BOTH volatility bounds revert to defaults

    For identical environment-variable values the resolved configuration is
    identical on repeated invocations (Requirement 8.3). This function NEVER
    raises.
    """
    risk_free_rate = _resolve_float(
        ENV_RISK_FREE_RATE, DEFAULT_RISK_FREE_RATE, _RATE_MIN, _RATE_MAX
    )
    iv_tolerance = _resolve_float(
        ENV_IV_TOLERANCE, DEFAULT_IV_TOLERANCE, _TOL_MIN, _TOL_MAX,
        inclusive_low=False,
    )
    iv_max_iterations = _resolve_int(
        ENV_IV_MAX_ITERATIONS, DEFAULT_IV_MAX_ITERATIONS, _ITER_MIN
    )
    iv_min_vol = _resolve_float(
        ENV_IV_MIN_VOL, DEFAULT_IV_MIN_VOL, _VOL_MIN, math.inf
    )
    iv_max_vol = _resolve_float(
        ENV_IV_MAX_VOL, DEFAULT_IV_MAX_VOL, _MAXVOL_MIN, math.inf,
        inclusive_low=False,
    )
    oi_wall_min_oi = _resolve_float(
        ENV_OI_WALL_MIN_OI, DEFAULT_OI_WALL_MIN_OI, _NONNEG_MIN, math.inf
    )
    buildup_oi_epsilon = _resolve_float(
        ENV_BUILDUP_OI_EPSILON, DEFAULT_BUILDUP_OI_EPSILON, _NONNEG_MIN, math.inf
    )
    buildup_price_epsilon = _resolve_float(
        ENV_BUILDUP_PRICE_EPSILON, DEFAULT_BUILDUP_PRICE_EPSILON, _NONNEG_MIN, math.inf
    )

    # Enforce the strict min < max volatility ordering. If it does not hold
    # (after the per-parameter resolution above), BOTH volatility bounds revert
    # to their documented defaults together — mirroring ``regime.py``'s
    # ``vol_low < vol_high`` rule (Requirement 8.2).
    if iv_min_vol >= iv_max_vol:
        iv_min_vol = DEFAULT_IV_MIN_VOL
        iv_max_vol = DEFAULT_IV_MAX_VOL

    return OptionsConfig(
        risk_free_rate=risk_free_rate,
        iv_tolerance=iv_tolerance,
        iv_max_iterations=iv_max_iterations,
        iv_min_vol=iv_min_vol,
        iv_max_vol=iv_max_vol,
        oi_wall_min_oi=oi_wall_min_oi,
        buildup_oi_epsilon=buildup_oi_epsilon,
        buildup_price_epsilon=buildup_price_epsilon,
    )


# ── Pure Black-Scholes core (price) ───────────────────────────────────────────
# Deterministic, dependency-free closed-form European option pricing. These
# functions take only their scalar arguments, never touch I/O / a clock /
# globals, never mutate anything, and NEVER raise — an invalid input resolves to
# ``None`` (Requirements 1.1, 1.5, 9.1, 9.2).
#
# Option-type convention (documented): ``option_type`` is the same CE/PE tag the
# F1 chain snapshot stores. ``bs_price`` accepts, case-insensitively, ``"CE"`` /
# ``"C"`` / ``"CALL"`` for a call and ``"PE"`` / ``"P"`` / ``"PUT"`` for a put.
# Any other value yields ``None`` (never an exception). This keeps the core
# directly callable with the snapshot's native ``option_type`` strings.

_CALL_TAGS = frozenset({"ce", "c", "call"})
_PUT_TAGS = frozenset({"pe", "p", "put"})


def _normalize_option_type(option_type: Any) -> Optional[str]:
    """Map a CE/PE-style tag to ``"call"`` / ``"put"``; ``None`` if unrecognized.

    Case-insensitive and whitespace-tolerant. Returns ``None`` (never raises) for
    a non-string or an unrecognized tag so callers can degrade to a null result.
    """
    if not isinstance(option_type, str):
        return None
    tag = option_type.strip().lower()
    if tag in _CALL_TAGS:
        return "call"
    if tag in _PUT_TAGS:
        return "put"
    return None


def _is_pos_finite(x: Any) -> bool:
    """True iff ``x`` is a real, finite number strictly greater than zero."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(x) and x > 0.0


def _norm_cdf(x: float) -> float:
    """Standard-normal cumulative distribution Φ(x) via ``math.erf``.

    ``Φ(x) = 0.5·(1 + erf(x/√2))``. Keeps the module dependency-free (no SciPy).
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard-normal probability density φ(x) = e^(−x²/2) / √(2π)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> Optional[float]:
    """European Black-Scholes price for a call or put (Requirement 1.1).

    Closed form (no continuous dividend yield, ``q = 0``)::

        d1 = (ln(S/K) + (r + σ²/2)·T) / (σ·√T)
        d2 = d1 − σ·√T
        call = S·Φ(d1) − K·e^(−rT)·Φ(d2)
        put  = K·e^(−rT)·Φ(−d2) − S·Φ(−d1)

    Returns ``None`` (never raises) when ``option_type`` is unrecognized, when any
    of ``S``, ``K``, ``T``, or ``sigma`` is non-positive or non-finite
    (Requirement 1.5), when ``r`` is non-finite, or when the computed price is not
    a finite number. The result is otherwise guaranteed finite. Pure and
    deterministic: identical inputs always yield an identical result
    (Requirements 1.6, 9.1, 9.2).
    """
    kind = _normalize_option_type(option_type)
    if kind is None:
        return None
    if not (_is_pos_finite(S) and _is_pos_finite(K) and _is_pos_finite(T)
            and _is_pos_finite(sigma)):
        return None
    if not (isinstance(r, (int, float)) and not isinstance(r, bool)
            and math.isfinite(r)):
        return None

    try:
        sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        discount = math.exp(-r * T)
        if kind == "call":
            price = S * _norm_cdf(d1) - K * discount * _norm_cdf(d2)
        else:  # put
            price = K * discount * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    except (ValueError, OverflowError):
        return None

    if not math.isfinite(price):
        return None
    return price


# ── Pure Black-Scholes core (implied volatility) ──────────────────────────────
# Bounded bisection IV inversion. Black-Scholes price is monotonically increasing
# in volatility, so a target price brackets to a unique σ inside the configured
# bounds whenever it lies between bs_price(min_vol) and bs_price(max_vol). The
# solver exploits that monotonicity: it never needs derivatives and cannot
# diverge (preferred over Newton-Raphson, which can overshoot near-zero vega).


def bs_implied_vol(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    price: float,
    config: OptionsConfig,
) -> Optional[float]:
    """Invert ``bs_price`` for σ via bounded bisection (Requirements 1.1-1.3, 1.5).

    Brackets the observed ``price`` between ``bs_price(iv_min_vol)`` and
    ``bs_price(iv_max_vol)`` (price is monotonically increasing in σ) and bisects
    until ``|bs_price(mid) - price| <= config.iv_tolerance`` or
    ``config.iv_max_iterations`` is reached, returning the in-bounds σ.

    Returns ``None`` (never raises) when:
      * ``T <= 0`` or any of ``S``, ``K`` is non-positive / non-finite, or
        ``r`` / ``price`` is non-finite (the bracket prices cannot be formed) —
        Requirement 1.5;
      * ``option_type`` is unrecognized;
      * the observed ``price`` lies outside the ``[bs_price(min), bs_price(max)]``
        bracket — i.e. below intrinsic value or above the no-arbitrage ceiling —
        rather than clamping to a bound (Requirement 1.3);
      * no in-bounds solution can be formed (e.g. the bracket prices are
        unavailable).

    Pure and deterministic: identical inputs always yield an identical result
    (Requirements 1.6, 9.1, 9.2).
    """
    try:
        # ``price`` must be a real, finite number (a negative or non-finite
        # observed price admits no σ and is rejected up front).
        if not (isinstance(price, (int, float)) and not isinstance(price, bool)
                and math.isfinite(price)):
            return None

        kind = _normalize_option_type(option_type)
        if kind is None:
            return None

        # Pull the configured bounds / tolerance / iteration cap. Guard against a
        # malformed config object by degrading to ``None`` rather than raising.
        min_vol = config.iv_min_vol
        max_vol = config.iv_max_vol
        tolerance = config.iv_tolerance
        max_iterations = config.iv_max_iterations
        if not (_is_pos_finite(max_vol) and isinstance(min_vol, (int, float))
                and not isinstance(min_vol, bool) and math.isfinite(min_vol)
                and 0.0 <= min_vol < max_vol):
            return None
        if not (_is_pos_finite(tolerance)):
            return None
        if not (isinstance(max_iterations, int) and not isinstance(max_iterations, bool)
                and max_iterations >= 1):
            return None

        # Bracket prices. ``bs_price`` returns ``None`` for non-positive /
        # non-finite S, K, T or non-finite r (so ``T <= 0`` is rejected here),
        # which means the bracket cannot be formed and IV is null.
        price_low = bs_price(option_type, S, K, T, r, min_vol)
        price_high = bs_price(option_type, S, K, T, r, max_vol)
        if price_low is None or price_high is None:
            return None

        # The target must lie within the bracket. Outside it (below intrinsic /
        # above the no-arb ceiling) there is no in-bounds solution → null. A
        # target within ``tolerance`` of a boundary resolves to that bound.
        if price <= price_low:
            return min_vol if (price_low - price) <= tolerance else None
        if price >= price_high:
            return max_vol if (price - price_high) <= tolerance else None

        # Bisect over [min_vol, max_vol]; price monotonically increases in σ.
        lo, hi = min_vol, max_vol
        for _ in range(max_iterations):
            mid = 0.5 * (lo + hi)
            price_mid = bs_price(option_type, S, K, T, r, mid)
            if price_mid is None:
                return None
            diff = price_mid - price
            if abs(diff) <= tolerance:
                return mid
            if price_mid < price:
                lo = mid
            else:
                hi = mid

        # Iteration cap reached without hitting the tolerance: return the current
        # in-bounds best estimate (the bracket has shrunk by 2**-max_iterations).
        return 0.5 * (lo + hi)
    except (ValueError, OverflowError, AttributeError, TypeError):
        return None


# ── Pure Black-Scholes core (Greeks) ──────────────────────────────────────────
# Closed-form first-order sensitivities of the European Black-Scholes price
# (no continuous dividend yield, ``q = 0``). Like ``bs_price`` / ``bs_implied_vol``
# these are deterministic, dependency-free, never touch I/O / a clock / globals,
# never mutate anything, and NEVER raise — any input that admits no well-defined
# Greek resolves to ``None`` rather than an exception (Requirements 1.4, 1.5,
# 9.1, 9.2).

_NULL_GREEKS: dict = {"delta": None, "gamma": None, "theta": None, "vega": None}


def bs_greeks(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: Optional[float],
) -> dict:
    """Black-Scholes Greeks for a call or put (Requirements 1.4, 1.5).

    Returns a dict with keys ``'delta'``, ``'gamma'``, ``'theta'``, ``'vega'``,
    each a finite float or ``None``. Computed from the supplied volatility
    ``sigma`` (typically the IV produced by :func:`bs_implied_vol`).

    Closed form (no continuous dividend yield, ``q = 0``)::

        d1 = (ln(S/K) + (r + σ²/2)·T) / (σ·√T)
        d2 = d1 − σ·√T
        delta_call =  Φ(d1)              delta_put = Φ(d1) − 1
        gamma      =  φ(d1) / (S·σ·√T)
        vega       =  S·φ(d1)·√T                      (per 1.0 of volatility, per year)
        theta_call = −S·φ(d1)·σ/(2·√T) − r·K·e^(−rT)·Φ(d2)
        theta_put  = −S·φ(d1)·σ/(2·√T) + r·K·e^(−rT)·Φ(−d2)   (per year)

    These satisfy the Black-Scholes invariants (Property 3): call delta lies in
    ``[0, 1]``, put delta lies in ``[-1, 0]``, ``gamma`` and ``vega`` are
    non-negative, and the parity ``delta_call − delta_put = 1`` holds.

    Returns **all-``None``** (never raises) when ``sigma is None`` or ``T <= 0``
    (Requirement 1.5), when ``option_type`` is unrecognized, when any of ``S``,
    ``K``, or ``sigma`` is non-positive / non-finite, or when ``r`` is non-finite.
    Any individual Greek that comes out non-finite is reported as ``None`` while
    the others stand. Pure and deterministic: identical inputs always yield an
    identical result (Requirements 1.6, 9.1, 9.2).
    """
    if sigma is None:
        return dict(_NULL_GREEKS)

    kind = _normalize_option_type(option_type)
    if kind is None:
        return dict(_NULL_GREEKS)

    # ``T <= 0`` and any non-positive / non-finite S, K, sigma yield all-None;
    # ``r`` must be a real finite number.
    if not (_is_pos_finite(S) and _is_pos_finite(K) and _is_pos_finite(T)
            and _is_pos_finite(sigma)):
        return dict(_NULL_GREEKS)
    if not (isinstance(r, (int, float)) and not isinstance(r, bool)
            and math.isfinite(r)):
        return dict(_NULL_GREEKS)

    try:
        sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        discount = math.exp(-r * T)
        pdf_d1 = _norm_pdf(d1)

        if kind == "call":
            delta = _norm_cdf(d1)
            theta = (-S * pdf_d1 * sigma / (2.0 * sqrt_t)
                     - r * K * discount * _norm_cdf(d2))
        else:  # put
            delta = _norm_cdf(d1) - 1.0
            theta = (-S * pdf_d1 * sigma / (2.0 * sqrt_t)
                     + r * K * discount * _norm_cdf(-d2))

        gamma = pdf_d1 / (S * sigma * sqrt_t)
        vega = S * pdf_d1 * sqrt_t
    except (ValueError, OverflowError, ZeroDivisionError):
        return dict(_NULL_GREEKS)

    # Each leaf is finite-or-None: a non-finite component degrades to None while
    # the remaining well-defined Greeks are still reported.
    return {
        "delta": delta if math.isfinite(delta) else None,
        "gamma": gamma if math.isfinite(gamma) else None,
        "theta": theta if math.isfinite(theta) else None,
        "vega": vega if math.isfinite(vega) else None,
    }


# ── Pure chain analytics (Put-Call Ratio) ─────────────────────────────────────
# Deterministic, dependency-free aggregates over a single in-memory chain
# snapshot. Like the Black-Scholes core these never touch I/O / a clock /
# globals, never mutate their inputs, and NEVER raise — a degenerate chain (empty
# ladder, all-null fields, non-finite / non-numeric values) resolves to ``None``
# rather than an exception (Requirements 2.1, 2.2, 2.5, 9.1, 9.2, 9.3).


def _is_finite(x: Any) -> bool:
    """True iff ``x`` is a real, finite number (any sign, zero allowed).

    Excludes ``None``, ``bool``, ``NaN``, ``±inf``, and non-numeric values so a
    field can be safely treated as a finite number (Requirement 9.3). Unlike
    :func:`_is_pos_finite` this admits zero and negative values, which is correct
    for summing open-interest / volume columns where a strike may legitimately
    carry zero.
    """
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(x)


def _sum_finite(values: Any) -> float:
    """Sum only the finite-number entries of ``values`` (Requirement 9.3).

    Non-finite / non-numeric / ``None`` entries are excluded from the total. An
    empty or all-excluded iterable sums to ``0.0``. Never raises on a
    non-numeric element.
    """
    total = 0.0
    for v in values:
        if _is_finite(v):
            total += float(v)
    return total


def compute_pcr_oi(snapshot: ChainSnapshot) -> Optional[float]:
    """Put-Call Ratio by open interest (Requirements 2.1, 2.5).

    ``sum(put OI) / sum(call OI)`` over the analyzed strikes, counting only
    finite OI values (non-finite / non-numeric / absent OI is excluded —
    Requirement 9.3). Returns ``None`` when the total call OI is ``0`` (no
    division by zero) and on any degenerate chain (empty ladder, malformed
    snapshot). Pure, deterministic, and never raises (Requirements 2.5, 9.1).
    """
    try:
        strikes = getattr(snapshot, "strikes", None)
        if not strikes:
            return None
        total_call_oi = _sum_finite(q.ce_oi for q in strikes)
        total_put_oi = _sum_finite(q.pe_oi for q in strikes)
        if total_call_oi == 0.0:
            return None
        result = total_put_oi / total_call_oi
        return result if math.isfinite(result) else None
    except (AttributeError, TypeError, ZeroDivisionError):
        return None


def compute_pcr_volume(snapshot: ChainSnapshot) -> Optional[float]:
    """Put-Call Ratio by traded volume (Requirements 2.2, 2.5).

    ``sum(put volume) / sum(call volume)`` over the analyzed strikes, counting
    only finite volume values (non-finite / non-numeric / absent volume is
    excluded — Requirement 9.3). Returns ``None`` when the total call volume is
    ``0`` *or unavailable* — both manifest as a zero finite total because every
    missing volume is excluded — so PCR-by-volume degrades to ``null`` without
    affecting PCR-by-OI or the rest of the result (Requirement 7.3). Also returns
    ``None`` on any degenerate chain. Pure, deterministic, and never raises
    (Requirements 2.5, 9.1).
    """
    try:
        strikes = getattr(snapshot, "strikes", None)
        if not strikes:
            return None
        total_call_vol = _sum_finite(q.ce_volume for q in strikes)
        total_put_vol = _sum_finite(q.pe_volume for q in strikes)
        if total_call_vol == 0.0:
            return None
        result = total_put_vol / total_call_vol
        return result if math.isfinite(result) else None
    except (AttributeError, TypeError, ZeroDivisionError):
        return None


# ── Pure chain analytics (Max Pain) ───────────────────────────────────────────
# Deterministic minimization of total intrinsic payout to option holders over the
# discrete strike ladder. Like the PCR aggregates this never touches I/O / a
# clock / globals, never mutates its input, and NEVER raises — a degenerate
# ladder (empty strikes, all-non-finite strikes / OI) resolves to ``None`` rather
# than an exception (Requirements 2.3, 2.5, 9.1, 9.2, 9.3).


def compute_max_pain(snapshot: ChainSnapshot) -> Optional[float]:
    """Max-pain strike over the discrete ladder (Requirements 2.3, 2.5).

    Returns the strike ``K`` on the snapshot's discrete ladder that **minimizes**
    the total intrinsic payout to option *holders* at a settlement of ``S = K``::

        payout(K) = Σ_k  call_OI(k)·max(0, K − k)  +  put_OI(k)·max(0, k − K)

    where the sum runs over every ladder strike ``k`` (the per-leg payouts follow
    the design's mapping table — a call at ``k`` pays ``max(0, K − k)`` per unit
    of OI, a put at ``k`` pays ``max(0, k − K)``). The candidate ``K`` is iterated
    over the snapshot's own strikes (the discrete ladder); for each candidate the
    payout is summed over all strikes.

    Non-finite / non-numeric / absent open interest is excluded from the payout
    sum (treated as zero contribution — Requirement 9.3), and a strike whose own
    ``strike`` value is non-finite cannot serve as a candidate nor contribute to
    any payout sum. Ties are broken **deterministically toward the lowest
    strike**: candidates are evaluated in ascending strike order and a new best is
    adopted only on a strictly smaller payout.

    Returns ``None`` (never raises) on an empty or degenerate ladder — no strikes,
    or no strike with a finite strike value — and when the minimal payout is not a
    finite number. Pure and deterministic: identical inputs always yield an
    identical result (Requirements 2.5, 9.1, 9.2).
    """
    try:
        strikes = getattr(snapshot, "strikes", None)
        if not strikes:
            return None

        # The discrete ladder: only strikes carrying a finite ``strike`` value can
        # be a settlement candidate or contribute to a payout sum. Sort ascending
        # so the lowest-strike tie-break is purely a matter of evaluation order.
        ladder = sorted(
            (q for q in strikes if _is_finite(getattr(q, "strike", None))),
            key=lambda q: q.strike,
        )
        if not ladder:
            return None

        best_strike: Optional[float] = None
        best_payout: Optional[float] = None
        for candidate in ladder:
            K = float(candidate.strike)
            payout = 0.0
            for q in ladder:
                k = float(q.strike)
                if K > k:
                    # Calls at strikes below the settlement are in the money.
                    if _is_finite(q.ce_oi):
                        payout += float(q.ce_oi) * (K - k)
                elif k > K:
                    # Puts at strikes above the settlement are in the money.
                    if _is_finite(q.pe_oi):
                        payout += float(q.pe_oi) * (k - K)
                # k == K contributes zero on both legs (max(0, 0)).
            if not math.isfinite(payout):
                continue
            # Strictly-smaller comparison + ascending iteration => lowest strike
            # wins any tie.
            if best_payout is None or payout < best_payout:
                best_payout = payout
                best_strike = K

        return best_strike
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


# ── Pure chain analytics (IV Skew) ────────────────────────────────────────────
# Deterministic summary of how implied volatility varies across the strike ladder
# for an expiry. Like the other pure analytics this never touches I/O / a clock /
# globals, never mutates its input, and NEVER raises — a degenerate map (fewer
# than two non-null IV points, non-finite spot, non-numeric keys/values) resolves
# to ``None`` or to null fields rather than an exception (Requirements 2.4, 2.5,
# 9.1, 9.2, 9.3).
#
# Input contract (documented for downstream assembly — task 9.1):
#   ``per_strike_iv`` is a mapping ``{strike: iv}`` associating each analyzed
#   strike (a finite number) with that strike's representative Implied_Volatility
#   (a finite number, or ``None`` when unsolvable). The assembly layer builds this
#   map from the per-strike IV it has already computed (e.g. the OTM-side IV per
#   strike: the put IV for strikes at/below spot, the call IV for strikes above
#   spot, or any single representative IV per strike). This function consumes
#   exactly that ``{strike: iv}`` shape — it does NOT expect a nested CE/PE dict
#   per strike. ``spot`` is the underlying's current price (a finite number);
#   when it is unavailable / non-finite the spot-relative fields degrade to null
#   while the spot-independent ``slope`` is still reported.


def compute_iv_skew(per_strike_iv: dict, spot: float) -> Optional[dict]:
    """IV variation across strikes from a per-strike IV map (Requirements 2.4, 2.5).

    Consumes a ``{strike: iv}`` mapping (see the input contract above) and returns
    a dict summarizing the volatility surface across the expiry's strikes::

        {
            "put_minus_call": <float|None>,   # mean put-side IV − mean call-side IV
            "slope":          <float|None>,   # OLS slope of IV regressed on strike
            "atm_iv":         <float|None>,   # IV at the strike nearest spot
        }

    Only strikes whose IV is a **finite, non-null** number at a **finite** strike
    are used — strikes with a null / non-finite / non-numeric IV (or a non-finite
    strike key) are excluded, so adding or removing null-IV strikes does not change
    the result (Property 6, Requirement 9.3). Returns ``None`` (never raises) when
    fewer than two such non-null IV points exist (Requirement 2.4).

    The three fields, computed over the non-null IV points (ascending by strike):

    * ``slope`` — the ordinary-least-squares slope of IV regressed on strike
      (``Σ(x−x̄)(y−ȳ) / Σ(x−x̄)²``). ``None`` when the regression is degenerate
      (zero strike variance) or non-finite.
    * ``atm_iv`` — the IV at the strike closest to ``spot`` (lowest strike wins a
      tie, via ascending evaluation). ``None`` when ``spot`` is non-finite.
    * ``put_minus_call`` — the mean IV of put-side strikes (strictly below
      ``spot``) minus the mean IV of call-side strikes (strictly above ``spot``),
      matching the glossary's "put-side IV minus call-side IV" skew. ``None`` when
      ``spot`` is non-finite or either side has no qualifying strike.

    Every field is a finite number or ``None`` (Requirement 6.2). Pure and
    deterministic: identical inputs always yield an identical result
    (Requirements 2.5, 9.1, 9.2).
    """
    try:
        if not isinstance(per_strike_iv, dict):
            return None

        # Collect (strike, iv) for non-null finite IV at finite strikes only;
        # everything else is excluded (Property 6, Requirement 9.3).
        points = [
            (float(raw_strike), float(raw_iv))
            for raw_strike, raw_iv in per_strike_iv.items()
            if _is_finite(raw_strike) and _is_finite(raw_iv)
        ]

        # Fewer than two non-null IV points => no skew (Requirement 2.4).
        if len(points) < 2:
            return None

        # Deterministic ascending-strike order (drives the lowest-strike tie-break
        # for the ATM lookup below).
        points.sort(key=lambda p: p[0])
        n = len(points)

        # ── slope: ordinary-least-squares regression of IV on strike ──
        mean_x = sum(p[0] for p in points) / n
        mean_y = sum(p[1] for p in points) / n
        denom = sum((p[0] - mean_x) ** 2 for p in points)
        slope: Optional[float] = None
        if denom > 0.0:
            numer = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
            candidate = numer / denom
            slope = candidate if math.isfinite(candidate) else None

        # ── spot-relative fields (degrade to null on a non-finite spot) ──
        atm_iv: Optional[float] = None
        put_minus_call: Optional[float] = None
        if _is_finite(spot):
            # ATM IV: IV at the strike closest to spot; ascending order + strict
            # ``<`` keeps the lowest strike on an equidistant tie.
            best_dist: Optional[float] = None
            for strike, iv in points:
                dist = abs(strike - spot)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    atm_iv = iv

            # put-side = strikes strictly below spot; call-side = strictly above.
            put_side = [iv for strike, iv in points if strike < spot]
            call_side = [iv for strike, iv in points if strike > spot]
            if put_side and call_side:
                candidate = (sum(put_side) / len(put_side)
                             - sum(call_side) / len(call_side))
                put_minus_call = candidate if math.isfinite(candidate) else None

        return {
            "put_minus_call": put_minus_call,
            "slope": slope,
            "atm_iv": atm_iv,
        }
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


# ── Pure chain analytics (OI Buildup classification) ──────────────────────────
# Deterministic classification of a single strike's open-interest buildup from
# the sign of its open-interest change against the sign of its price change. Like
# the other pure analytics this never touches I/O / a clock / globals, never
# mutates anything, and NEVER raises — a non-finite / non-numeric change resolves
# to ``"neutral"`` rather than an exception (Requirements 3.1, 3.4, 3.5, 9.1,
# 9.3). The function is **total**: every possible (ΔOI, Δprice) input maps to
# exactly one of the five labels.

# The five OI-buildup labels (the complete, closed classification set).
BUILDUP_LONG = "long_buildup"        # rising OI + rising price
BUILDUP_SHORT = "short_buildup"      # rising OI + falling price
BUILDUP_SHORT_COVERING = "short_covering"   # falling OI + rising price
BUILDUP_LONG_UNWINDING = "long_unwinding"   # falling OI + falling price
BUILDUP_NEUTRAL = "neutral"          # any dead-banded / zero / undefined change


def classify_oi_buildup(d_oi: float, d_price: float, config: OptionsConfig) -> str:
    """Classify per-strike OI buildup from sign(ΔOI) × sign(Δprice) (R3.1, 3.4, 3.5).

    Maps the open-interest change ``d_oi`` and price change ``d_price`` (latest
    snapshot versus a prior snapshot) to exactly one label, following the design's
    mapping table over the nine sign combinations after dead-banding:

        | sign(ΔOI) \\ sign(Δprice) |   > 0          |   < 0           |   0       |
        | ------------------------- | -------------- | --------------- | --------- |
        |   > 0                     | long_buildup   | short_buildup   | neutral   |
        |   < 0                     | short_covering | long_unwinding  | neutral   |
        |   0                       | neutral        | neutral         | neutral   |

    Concretely:
      * rising OI  + rising price  -> ``"long_buildup"``
      * rising OI  + falling price -> ``"short_buildup"``
      * falling OI + rising price  -> ``"short_covering"``
      * falling OI + falling price -> ``"long_unwinding"``
      * a zero / within-dead-band ΔOI or Δprice -> ``"neutral"``

    A change is treated as "no change" (mapped to the zero column/row above) when
    its magnitude is within the configured dead-band: ``|d_oi| <=
    config.buildup_oi_epsilon`` or ``|d_price| <= config.buildup_price_epsilon``
    (Requirements 3.4, 3.5). With the documented default epsilons of ``0.0`` this
    reduces to an exact-zero dead-band.

    **Total and never raises** (Requirements 3.5, 9.1, 9.3): a non-finite /
    non-numeric ``d_oi`` or ``d_price`` (``None``, ``NaN``, ``±inf``, a string,
    ``bool``), or a malformed / missing ``config``, resolves to ``"neutral"``
    rather than an exception. Pure and deterministic: identical inputs always
    yield an identical label (Requirements 9.1, 9.2).
    """
    try:
        # Non-finite / non-numeric changes carry no defined direction → neutral.
        if not (_is_finite(d_oi) and _is_finite(d_price)):
            return BUILDUP_NEUTRAL

        # Resolve the dead-bands from config, degrading any malformed / missing
        # epsilon to the documented default (0.0) rather than raising. A
        # non-finite or negative epsilon is not a valid dead-band, so it falls
        # back to the exact-zero dead-band.
        oi_eps = getattr(config, "buildup_oi_epsilon", DEFAULT_BUILDUP_OI_EPSILON)
        price_eps = getattr(config, "buildup_price_epsilon", DEFAULT_BUILDUP_PRICE_EPSILON)
        if not (_is_finite(oi_eps) and oi_eps >= 0.0):
            oi_eps = DEFAULT_BUILDUP_OI_EPSILON
        if not (_is_finite(price_eps) and price_eps >= 0.0):
            price_eps = DEFAULT_BUILDUP_PRICE_EPSILON

        # Apply the dead-bands: a change within its epsilon is "no change".
        oi_sign = 0
        if abs(d_oi) > oi_eps:
            oi_sign = 1 if d_oi > 0.0 else -1

        price_sign = 0
        if abs(d_price) > price_eps:
            price_sign = 1 if d_price > 0.0 else -1

        # Any dead-banded / zero side → neutral (the zero row and column).
        if oi_sign == 0 or price_sign == 0:
            return BUILDUP_NEUTRAL

        # The four defined quadrants (sign(ΔOI) × sign(Δprice)).
        if oi_sign > 0:
            return BUILDUP_LONG if price_sign > 0 else BUILDUP_SHORT
        # oi_sign < 0
        return BUILDUP_SHORT_COVERING if price_sign > 0 else BUILDUP_LONG_UNWINDING
    except (AttributeError, TypeError, ValueError):
        return BUILDUP_NEUTRAL


# ── Pure chain analytics (aggregate OI Buildup) ───────────────────────────────
# Deterministic aggregation of per-strike OI buildup into a single call-side or
# put-side label over the analyzed strikes. Like ``classify_oi_buildup`` this
# never touches I/O / a clock / globals, never mutates its inputs, and is
# **total** — every input (including a missing prior snapshot, an empty / mismatched
# ladder, or non-finite fields) resolves to one of the five buildup labels rather
# than an exception (Requirements 3.2, 3.3, 9.1, 9.2, 9.3).


def aggregate_oi_buildup(
    latest: ChainSnapshot,
    prior: Optional[ChainSnapshot],
    config: OptionsConfig,
    side: str,
) -> str:
    """Aggregate call-side / put-side OI buildup over the analyzed strikes (R3.2, 3.3).

    Classifies the open-interest buildup for one side of the chain (``side``
    selects the call side — ``"CE"`` / ``"C"`` / ``"call"`` — or the put side —
    ``"PE"`` / ``"P"`` / ``"put"``, case-insensitively) from the **net** change
    between the ``latest`` and ``prior`` snapshots over the strikes they share,
    then delegates the sign mapping to :func:`classify_oi_buildup` so the aggregate
    uses exactly the same (ΔOI sign × Δprice sign) → label rule as the per-strike
    classification (Property 7/8 consistency).

    Matching and aggregation (over strikes present in **both** snapshots, matched
    by finite strike value):

      * ``net ΔOI`` = ``Σ latest side-OI − Σ prior side-OI`` over the matched
        strikes (only finite OI values contribute on each side — non-finite /
        non-numeric / absent OI is excluded, Requirement 9.3).
      * ``net Δprice`` = ``Σ latest side-price − Σ prior side-price`` over the same
        matched strikes (a representative net price move for the side; only finite
        prices contribute on each side).

    The resulting ``(net ΔOI, net Δprice)`` is passed to
    :func:`classify_oi_buildup`, so the dead-bands in ``config`` and the
    zero-change → ``"neutral"`` rule apply to the aggregate exactly as they do
    per strike (Requirements 3.1, 3.4, 3.5).

    Returns ``"neutral"`` (never raises) when:
      * ``prior`` is ``None`` — no prior snapshot for the OI-change comparison, so
        no direction is fabricated (Requirement 3.3);
      * ``side`` is unrecognized;
      * either snapshot has no strikes, or the two snapshots share no matchable
        strike (no comparison is possible);
      * any malformed input is encountered.

    Pure and deterministic: identical inputs always yield an identical label
    (Requirements 3.2, 9.1, 9.2). The output is always one of the five labels
    defined by :func:`classify_oi_buildup` (it is **total**).
    """
    try:
        # No prior snapshot → no defined direction; never fabricate one (R3.3).
        if prior is None:
            return BUILDUP_NEUTRAL

        # Resolve which side of the chain we are aggregating. An unrecognized
        # side tag carries no defined direction → neutral.
        kind = _normalize_option_type(side)
        if kind is None:
            return BUILDUP_NEUTRAL

        latest_strikes = getattr(latest, "strikes", None)
        prior_strikes = getattr(prior, "strikes", None)
        if not latest_strikes or not prior_strikes:
            return BUILDUP_NEUTRAL

        # Index the prior snapshot by finite strike value so latest strikes can
        # be matched by strike (first occurrence wins on a duplicate strike).
        prior_by_strike: dict = {}
        for q in prior_strikes:
            s = getattr(q, "strike", None)
            if _is_finite(s):
                prior_by_strike.setdefault(float(s), q)
        if not prior_by_strike:
            return BUILDUP_NEUTRAL

        # Select the side's OI / price attributes once.
        oi_attr = "ce_oi" if kind == "call" else "pe_oi"
        price_attr = "ce_price" if kind == "call" else "pe_price"

        latest_oi_vals = []
        prior_oi_vals = []
        latest_price_vals = []
        prior_price_vals = []

        # Accumulate the side's OI / price for strikes present in both snapshots.
        for q in latest_strikes:
            s = getattr(q, "strike", None)
            if not _is_finite(s):
                continue
            match = prior_by_strike.get(float(s))
            if match is None:
                continue
            latest_oi_vals.append(getattr(q, oi_attr, None))
            prior_oi_vals.append(getattr(match, oi_attr, None))
            latest_price_vals.append(getattr(q, price_attr, None))
            prior_price_vals.append(getattr(match, price_attr, None))

        # No shared strike → no comparison is possible → neutral.
        if not latest_oi_vals:
            return BUILDUP_NEUTRAL

        # Net change between the snapshots (finite-only sums; non-finite excluded).
        d_oi = _sum_finite(latest_oi_vals) - _sum_finite(prior_oi_vals)
        d_price = _sum_finite(latest_price_vals) - _sum_finite(prior_price_vals)

        # Delegate the sign mapping (with dead-bands) to the per-strike rule.
        return classify_oi_buildup(d_oi, d_price, config)
    except (AttributeError, TypeError, ValueError):
        return BUILDUP_NEUTRAL


# ── Pure chain analytics (OI Walls) ───────────────────────────────────────────
# Deterministic identification of the open-interest "walls" — the strikes whose
# outsized open interest acts as support / resistance relative to spot. Like the
# other pure analytics this never touches I/O / a clock / globals, never mutates
# its input, and NEVER raises — a degenerate chain (empty ladder, non-finite
# spot, all-null / sub-threshold OI, non-finite strikes) resolves to ``None`` on
# the affected side rather than an exception (Requirements 4.1, 4.2, 4.4, 9.1,
# 9.2, 9.3).


def compute_oi_walls(snapshot: ChainSnapshot, spot: float, config: OptionsConfig) -> dict:
    """OI-wall support / resistance strikes around spot (Requirements 4.1, 4.2, 4.4).

    Returns ``{'support': <float|None>, 'resistance': <float|None>}`` where:

    * ``resistance`` — the strike **at or above** spot (``strike >= spot``)
      carrying the **greatest qualifying call open interest** (``ce_oi``); the
      level where call writers sit overhead (Requirement 4.1).
    * ``support`` — the strike **at or below** spot (``strike <= spot``) carrying
      the **greatest qualifying put open interest** (``pe_oi``); the level where
      put writers sit beneath (Requirement 4.1).

    A strike **qualifies** on a side only when its relevant OI is a finite number
    (non-finite / non-numeric / absent OI is excluded — Requirement 9.3) **and**
    is greater than or equal to ``config.oi_wall_min_oi`` (the configured minimum
    OI for a strike to count as a wall). When no strike qualifies on a side — no
    strikes on that side of spot, or none meeting the threshold — that wall is
    ``None`` rather than a fabricated level (Requirement 4.2). A strike whose own
    ``strike`` value is non-finite cannot serve as a wall, and a non-finite
    ``spot`` makes the at/above and at/below tests undefined, so **both** walls
    are ``None`` (Requirement 4.4).

    Tie-break (documented, deterministic): when two qualifying strikes carry an
    equal greatest OI, the one **nearest to spot** wins — the *lowest* qualifying
    strike for resistance (all candidates are ``>= spot``) and the *highest*
    qualifying strike for support (all candidates are ``<= spot``). This is
    realized by ascending-strike iteration: resistance adopts a new best only on
    a strictly greater OI (keeping the lowest strike on a tie), support adopts a
    new best on a greater-or-equal OI (keeping the highest strike on a tie).

    Pure and deterministic: identical inputs always yield an identical result,
    and the input snapshot is never mutated (Requirements 9.1, 9.2). NEVER raises
    (Requirement 4.4).
    """
    try:
        # A non-finite spot makes "at/above" and "at/below" undefined → both null.
        if not _is_finite(spot):
            return {"support": None, "resistance": None}

        strikes = getattr(snapshot, "strikes", None)
        if not strikes:
            return {"support": None, "resistance": None}

        # The configured minimum OI for a strike to qualify as a wall. Degrade a
        # malformed / non-finite / negative threshold to the documented default
        # rather than raising.
        min_oi = getattr(config, "oi_wall_min_oi", DEFAULT_OI_WALL_MIN_OI)
        if not (_is_finite(min_oi) and min_oi >= 0.0):
            min_oi = DEFAULT_OI_WALL_MIN_OI

        # Evaluate strikes in ascending order so the tie-breaks below resolve to
        # the strike nearest spot on each side.
        ladder = sorted(
            (q for q in strikes if _is_finite(getattr(q, "strike", None))),
            key=lambda q: float(q.strike),
        )

        resistance: Optional[float] = None
        best_call_oi: Optional[float] = None
        support: Optional[float] = None
        best_put_oi: Optional[float] = None

        for q in ladder:
            k = float(q.strike)

            # Resistance: qualifying call OI at/above spot. Strictly-greater
            # comparison + ascending order keeps the lowest (nearest) strike on a
            # tie.
            if k >= spot and _is_finite(q.ce_oi):
                call_oi = float(q.ce_oi)
                if call_oi >= min_oi and (best_call_oi is None or call_oi > best_call_oi):
                    best_call_oi = call_oi
                    resistance = k

            # Support: qualifying put OI at/below spot. Greater-or-equal
            # comparison + ascending order keeps the highest (nearest) strike on
            # a tie.
            if k <= spot and _is_finite(q.pe_oi):
                put_oi = float(q.pe_oi)
                if put_oi >= min_oi and (best_put_oi is None or put_oi >= best_put_oi):
                    best_put_oi = put_oi
                    support = k

        return {"support": support, "resistance": resistance}
    except (AttributeError, TypeError, ValueError, OverflowError):
        return {"support": None, "resistance": None}


# ── Pure chain analytics (Futures Basis) ──────────────────────────────────────
# Deterministic spread of the (near-month) future over spot. Like the other pure
# analytics this never touches I/O / a clock / globals, never mutates anything,
# and NEVER raises — a missing future or any degenerate / non-finite input
# resolves to ``None`` rather than an exception (Requirements 4.3, 4.4, 9.1,
# 9.2, 9.3).


def compute_futures_basis(future_price: Optional[float], spot: float) -> Optional[float]:
    """Futures basis: ``future_price − spot`` (Requirements 4.3, 4.4).

    Returns the basis (the future's premium/discount to spot) as a finite float,
    or ``None`` when it cannot be computed. Per Property 10 the basis equals the
    future price minus spot and is null **exactly** when the future price is
    unavailable.

    Returns ``None`` (never raises) when:
      * ``future_price`` is ``None`` — no near-month future is subscribed/stored,
        so no basis is fabricated (Requirement 4.3);
      * ``future_price`` is non-finite / non-numeric (``NaN`` / ``±inf`` / a
        non-number degrades to null rather than propagating — Requirement 9.3);
      * ``spot`` is non-finite / non-numeric (the subtraction is undefined);
      * the computed difference is not a finite number.

    Pure and deterministic: identical inputs always yield an identical result,
    and nothing is mutated (Requirements 1.6, 9.1, 9.2).
    """
    try:
        # No future → null basis (never fabricated). This is the only case in
        # which a finite spot yields ``None`` (Requirement 4.3 / Property 10).
        if future_price is None:
            return None
        # A non-finite / non-numeric future or spot makes the spread undefined.
        if not (_is_finite(future_price) and _is_finite(spot)):
            return None
        basis = float(future_price) - float(spot)
        return basis if math.isfinite(basis) else None
    except (TypeError, ValueError, OverflowError):
        return None


# ── Read / query layer over the F1 QuestDB tables (the only impure component) ──
# Isolated I/O boundary: everything above this line is a deterministic, pure
# function of its arguments; everything below reads the F1 tables
# (``option_chain_snapshots`` / ``option_ticks`` / ``live_ticks``) over the same
# QuestDB HTTP ``/exec`` API ``tools.py::_read_live_ticks`` uses. This layer
# performs ONLY read-only ``SELECT`` queries (Requirement 5.4), turns any failure
# (unreachable server, query error, no rows, malformed payload) into a sentinel
# rather than an exception, and NEVER raises into its caller (Requirements 7.4,
# 9.1). Keeping it cleanly separated lets the analytics be property-tested on
# in-memory snapshots with no QuestDB (Requirement 5.3).


def _escape_sql_literal(value: Any) -> str:
    """Escape a value for safe inclusion inside a single-quoted SQL string literal.

    Doubles every embedded single quote (``'`` -> ``''``), the SQL-standard
    escape QuestDB accepts, so an ``underlying`` / ``expiry`` value can never
    break out of its string literal. Mirrors ``tools.py::_read_live_ticks``.
    """
    return str(value).replace("'", "''")


def _questdb_select(query: str, timeout: float = 10.0) -> Optional[list]:
    """Execute a read-only ``SELECT`` against QuestDB ``/exec``; return its dataset.

    Returns the ``dataset`` list from the QuestDB JSON response on success (an
    empty list when the query matched no rows), or ``None`` on ANY failure —
    unreachable server, HTTP error, a query-level ``error`` in the payload, or a
    malformed / missing ``dataset`` (mirroring ``tools.py::_read_live_ticks``'s
    degrade-to-sentinel contract). NEVER raises (Requirements 7.4, 9.1).

    The caller is responsible for issuing only ``SELECT`` statements
    (Requirement 5.4); this helper is a thin transport that does not mutate any
    table.
    """
    try:
        r = httpx.get(
            f"{QUESTDB_HTTP_URL}/exec", params={"query": query}, timeout=timeout
        )
        r.raise_for_status()
        body = r.json()
    except Exception as exc:  # noqa: BLE001 — any failure degrades to the sentinel
        print(f"[Options Warning] _questdb_select: query failed: {exc}")
        return None

    if not isinstance(body, dict) or body.get("error"):
        return None
    dataset = body.get("dataset")
    if not isinstance(dataset, list):
        return None
    return dataset


def _coerce_optional_float(value: Any) -> Optional[float]:
    """Project a raw QuestDB cell to a finite ``float`` or ``None`` (Requirement 9.3).

    A SQL ``NULL`` (``None``), a ``bool``, a ``NaN`` / ``±inf``, or a non-numeric
    value resolves to ``None`` rather than being fabricated or propagated. A
    numeric value (or numeric string) that is finite is returned as a ``float``.
    Never raises.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    return parsed if math.isfinite(parsed) else None


def _read_latest_option_volume(underlying: str, expiry: str) -> dict:
    """Latest traded ``option_ticks.volume`` per instrument symbol (best-effort).

    Issues a single read-only ``SELECT ... LATEST ON timestamp PARTITION BY
    symbol`` over ``option_ticks`` for the ``(underlying, expiry)`` chain, so each
    subscribed instrument contributes its most-recent cumulative traded volume.
    Returns a ``{symbol: volume}`` mapping of only the symbols whose latest volume
    is a finite number (non-finite / absent volume is omitted, so a later lookup
    yields ``None`` — Requirement 9.3). Returns an empty mapping on any failure or
    when no ticks exist; NEVER raises (Requirement 7.4).
    """
    u = _escape_sql_literal(underlying)
    e = _escape_sql_literal(expiry)
    query = (
        "SELECT symbol, volume FROM option_ticks "
        f"WHERE underlying='{u}' AND expiry='{e}' "
        "LATEST ON timestamp PARTITION BY symbol"
    )
    rows = _questdb_select(query)
    volume_by_symbol: dict = {}
    if not rows:
        return volume_by_symbol
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        symbol = row[0]
        volume = _coerce_optional_float(row[1])
        if isinstance(symbol, str) and volume is not None:
            volume_by_symbol[symbol] = volume
    return volume_by_symbol


def _build_chain_snapshot(
    underlying: str,
    expiry: str,
    snapshot_ts_micros: int,
    volume_by_symbol: dict,
) -> Optional[ChainSnapshot]:
    """Project one ``option_chain_snapshots`` timestamp into a ``ChainSnapshot``.

    Reads the per-instrument rows (one per CE/PE leg) captured at
    ``snapshot_ts_micros`` (epoch microseconds) for the ``(underlying, expiry)``
    chain, groups them by ``strike`` into per-strike CE/PE ``last_price`` +
    ``open_interest``, and joins the latest ``option_ticks.volume`` for each leg's
    instrument ``symbol`` from ``volume_by_symbol`` (a missing symbol -> ``None``).

    Every numeric field is projected to a finite ``float`` or ``None`` via
    :func:`_coerce_optional_float` (Requirement 9.3); a row with a non-finite
    ``strike`` or an unrecognized ``option_type`` is skipped. The resulting
    ``strikes`` tuple is ascending and distinct by strike. Returns ``None`` when
    the read fails or yields no usable strike; NEVER raises (Requirement 7.4).
    """
    u = _escape_sql_literal(underlying)
    e = _escape_sql_literal(expiry)
    query = (
        "SELECT strike, option_type, symbol, last_price, open_interest "
        "FROM option_chain_snapshots "
        f"WHERE underlying='{u}' AND expiry='{e}' "
        f"AND cast(snapshot_ts AS LONG)={int(snapshot_ts_micros)}"
    )
    rows = _questdb_select(query)
    if not rows:
        return None

    by_strike: dict = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        strike = _coerce_optional_float(row[0])
        if strike is None:
            continue
        kind = _normalize_option_type(row[1])
        if kind is None:
            continue
        symbol = row[2]
        last_price = _coerce_optional_float(row[3])
        open_interest = _coerce_optional_float(row[4])
        volume = (
            volume_by_symbol.get(symbol) if isinstance(symbol, str) else None
        )

        entry = by_strike.setdefault(
            strike,
            {
                "ce_price": None, "pe_price": None,
                "ce_oi": None, "pe_oi": None,
                "ce_volume": None, "pe_volume": None,
            },
        )
        if kind == "call":
            entry["ce_price"] = last_price
            entry["ce_oi"] = open_interest
            entry["ce_volume"] = volume
        else:  # put
            entry["pe_price"] = last_price
            entry["pe_oi"] = open_interest
            entry["pe_volume"] = volume

    if not by_strike:
        return None

    strikes = tuple(
        StrikeQuote(
            strike=strike,
            ce_price=entry["ce_price"],
            pe_price=entry["pe_price"],
            ce_oi=entry["ce_oi"],
            pe_oi=entry["pe_oi"],
            ce_volume=entry["ce_volume"],
            pe_volume=entry["pe_volume"],
        )
        for strike, entry in sorted(by_strike.items(), key=lambda kv: kv[0])
    )

    return ChainSnapshot(
        underlying=str(underlying),
        expiry=str(expiry),
        snapshot_ts=int(snapshot_ts_micros) // 1000,  # epoch micros -> epoch ms
        strikes=strikes,
    )


def resolve_active_expiry(underlying: str) -> Optional[str]:
    """Resolve which expiry to analyze when the caller passes an EMPTY expiry.

    The snapshot rows are keyed by a concrete expiry (e.g. ``"2026-07-15"``), so a
    literal ``expiry=''`` filter matches nothing even when the chain is fully
    populated — which surfaced as a false "no chain snapshot" for indices. This
    resolves the expiry of the MOST RECENTLY captured snapshot for the underlying
    (the chain actively being ingested — normally the front / nearest expiry),
    avoiding any date-format / timezone comparison. Returns ``None`` when the
    underlying has no snapshot rows. Read-only ``SELECT`` only; never raises."""
    try:
        u = _escape_sql_literal(underlying)
        q = (
            "SELECT expiry FROM option_chain_snapshots "
            f"WHERE underlying='{u}' "
            "ORDER BY snapshot_ts DESC LIMIT 1"
        )
        rows = _questdb_select(q)
        if not rows or not isinstance(rows[0], (list, tuple)) or not rows[0]:
            return None
        exp = rows[0][0]
        return exp.strip() if isinstance(exp, str) and exp.strip() else None
    except Exception as exc:  # noqa: BLE001 — totality guarantee
        print(f"[Options Warning] resolve_active_expiry failed: {exc}")
        return None


def read_latest_and_prior_snapshot(
    underlying: str, expiry: str
) -> tuple[Optional[ChainSnapshot], Optional[ChainSnapshot]]:
    """Read the latest and immediately-prior chain snapshots (Requirements 5.1, 7.1).

    Resolves the two most recent ``option_chain_snapshots`` capture timestamps for
    the ``(underlying, expiry)`` chain and projects each into a
    :class:`ChainSnapshot` of per-strike CE/PE ``last_price`` + ``open_interest``
    (via :func:`_build_chain_snapshot`), then joins the latest
    ``option_ticks.volume`` per instrument ``symbol`` onto the matching strikes —
    a strike whose instrument has no stored volume gets ``None`` (Requirements
    7.3, 9.3).

    Returns ``(latest, prior)``:
      * ``(None, None)`` when no snapshot exists for the chain, on any read
        failure, or when the rows cannot be projected (Requirement 7.1);
      * ``(latest, None)`` when only one capture timestamp exists (no prior
        snapshot to compare for OI-buildup);
      * ``(latest, prior)`` when at least two captures exist, with ``latest`` the
        most recent and ``prior`` the immediately-preceding capture.

    Issues ONLY read-only ``SELECT`` statements (Requirement 5.4) and NEVER raises
    into its caller (Requirements 7.4, 9.1): every failure degrades to
    ``(None, None)`` or to ``None`` per-field.
    """
    try:
        u = _escape_sql_literal(underlying)
        e = _escape_sql_literal(expiry)

        # The two most recent distinct capture timestamps (epoch microseconds),
        # most-recent first. ``cast(... AS LONG)`` yields micros since epoch so
        # the per-snapshot read can match an exact capture without timestamp
        # string-format ambiguity.
        ts_query = (
            "SELECT DISTINCT cast(snapshot_ts AS LONG) ts "
            "FROM option_chain_snapshots "
            f"WHERE underlying='{u}' AND expiry='{e}' "
            "ORDER BY ts DESC LIMIT 2"
        )
        ts_rows = _questdb_select(ts_query)
        if not ts_rows:
            return (None, None)

        ts_values: list[int] = []
        for row in ts_rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            ts = row[0]
            if (isinstance(ts, (int, float)) and not isinstance(ts, bool)
                    and math.isfinite(ts)):
                ts_values.append(int(ts))
        if not ts_values:
            return (None, None)

        # Latest cumulative traded volume per instrument symbol, joined onto the
        # matching strikes below (missing -> None, Requirement 7.3).
        volume_by_symbol = _read_latest_option_volume(underlying, expiry)

        latest = _build_chain_snapshot(
            underlying, expiry, ts_values[0], volume_by_symbol
        )
        if latest is None:
            return (None, None)

        prior = None
        if len(ts_values) >= 2:
            prior = _build_chain_snapshot(
                underlying, expiry, ts_values[1], volume_by_symbol
            )

        return (latest, prior)
    except Exception as exc:  # noqa: BLE001 — totality guarantee (R7.4 / R9.1)
        print(f"[Options Warning] read_latest_and_prior_snapshot failed: {exc}")
        return (None, None)


def read_future_price(underlying: str) -> Optional[float]:
    """Best-effort near-month FUT last price from ``option_ticks`` (Requirements 4.3, 7.3).

    Reads the most-recent ``last_traded_price`` of the **near-month future** for
    ``underlying`` from ``option_ticks`` (the ``option_type='FUT'`` rows). A single
    read-only ``SELECT ... LATEST ON timestamp PARTITION BY symbol`` yields each
    subscribed future's latest tick; among those the near-month contract is the
    one with the **lexicographically smallest expiry** (F1 stores expiries as ISO
    ``YYYY-MM-DD`` strings, so the smallest is the nearest), and its latest
    ``last_traded_price`` is returned as a finite ``float``.

    Returns ``None`` (never raises) when **no future is subscribed/stored** — the
    common case today, since F1's chain selection subscribes only CE/PE legs and
    not ``FUT`` tokens (Requirement 4.3) — on any read failure, or when no FUT row
    carries a finite ``last_traded_price``. The caller maps this ``None`` to a
    ``null`` Futures_Basis (Requirements 4.3, 7.3).

    Issues ONLY a read-only ``SELECT`` (Requirement 5.4) and NEVER raises into its
    caller (Requirements 7.4, 9.1): every failure degrades to ``None``.
    """
    try:
        u = _escape_sql_literal(underlying)
        # Latest tick per future contract symbol for this underlying. Each row is
        # (expiry, last_traded_price) for one subscribed FUT instrument.
        query = (
            "SELECT expiry, last_traded_price FROM option_ticks "
            f"WHERE underlying='{u}' AND option_type='FUT' "
            "LATEST ON timestamp PARTITION BY symbol"
        )
        rows = _questdb_select(query)
        if not rows:
            return None

        # Pick the near-month contract: the finite-priced FUT row with the
        # smallest expiry string (ISO dates sort chronologically). Rows whose
        # latest price is non-finite / non-numeric are excluded (Requirement 9.3).
        best_expiry: Optional[str] = None
        best_price: Optional[float] = None
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            expiry = row[0]
            if not isinstance(expiry, str):
                continue
            price = _coerce_optional_float(row[1])
            if price is None:
                continue
            if best_expiry is None or expiry < best_expiry:
                best_expiry = expiry
                best_price = price

        return best_price
    except Exception as exc:  # noqa: BLE001 — totality guarantee (R7.4 / R9.1)
        print(f"[Options Warning] read_future_price failed: {exc}")
        return None

# ── Orchestrator and assembly (pure assembly half — task 9.1) ─────────────────
# ``assemble_result`` is the pure half of the orchestrator: given the in-memory
# latest (and optional prior) ``ChainSnapshot``, the underlying spot, the
# best-effort near-month future price, and a resolved ``OptionsConfig``, it bundles
# every analytic into the design's ``Options_Analytics_Result`` success shape. It
# only calls the pure analytic functions above (no I/O, no clock, no globals),
# never mutates its inputs, and NEVER raises — every numeric leaf of the returned
# dict is a finite number or ``None`` and every value is derived from the chain
# data rather than fabricated (Requirements 6.1, 6.2, 6.3, 9.1, 9.2, 9.3).

# 15:30 IST market close ≈ 10:00 UTC; NSE index options settle at the close on the
# expiry date. Anchoring time-to-expiry at the close keeps T deterministic and
# derived purely from the chain's own ``expiry`` + ``snapshot_ts`` (never a clock).
_EXPIRY_CLOSE_HOUR_UTC = 10
_MS_PER_YEAR = 365.0 * 24.0 * 60.0 * 60.0 * 1000.0


def _years_to_expiry(expiry: Any, snapshot_ts_ms: Any) -> Optional[float]:
    """Time-to-expiry in years from the chain's ``expiry`` date and ``snapshot_ts``.

    ``expiry`` is the F1 ISO ``YYYY-MM-DD`` string; ``snapshot_ts_ms`` is the
    latest snapshot's epoch-ms timestamp. The expiry instant is anchored at the
    NSE market close (15:30 IST ≈ 10:00 UTC) on the expiry date, and ``T`` is the
    gap to that instant expressed in years (``ms / _MS_PER_YEAR``).

    Both inputs come from the snapshot, so ``T`` is derived from chain data and
    never fabricated. Returns ``None`` (never raises) when ``expiry`` is not a
    parseable ISO date or ``snapshot_ts_ms`` is non-finite / non-numeric; a
    zero-or-negative ``T`` (a snapshot at/after expiry) is returned as-is and the
    Black-Scholes core maps it to null IV/Greeks (Requirement 1.5).
    """
    try:
        if not isinstance(expiry, str):
            return None
        text = expiry.strip()
        if not text:
            return None
        parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        expiry_dt = parsed.replace(
            hour=_EXPIRY_CLOSE_HOUR_UTC, minute=0, second=0, microsecond=0,
            tzinfo=timezone.utc,
        )
        if not (isinstance(snapshot_ts_ms, (int, float))
                and not isinstance(snapshot_ts_ms, bool)
                and math.isfinite(snapshot_ts_ms)):
            return None
        years = (expiry_dt.timestamp() * 1000.0 - float(snapshot_ts_ms)) / _MS_PER_YEAR
        return years if math.isfinite(years) else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _signed_delta(latest_val: Any, prior_val: Any) -> float:
    """Finite ``latest − prior`` change, or ``NaN`` when either side is non-finite.

    A ``NaN`` result carries no defined direction, so :func:`classify_oi_buildup`
    maps it to ``"neutral"`` (Requirement 3.5) — the honest classification when a
    strike's OI / price is missing on either snapshot.
    """
    if _is_finite(latest_val) and _is_finite(prior_val):
        return float(latest_val) - float(prior_val)
    return float("nan")


def _sanitize_numeric_leaves(obj: Any) -> Any:
    """Recursively map every non-finite float leaf to ``None`` (Requirements 6.2, 9.3).

    Walks dicts / lists / tuples and replaces any ``float`` that is ``NaN`` /
    ``±inf`` with ``None`` so the assembled result can never carry a non-finite
    numeric leaf. ``int`` (e.g. ``snapshot_ts``), ``str`` (labels, underlying,
    expiry), ``bool``, and ``None`` pass through unchanged. Pure; never raises.
    """
    if isinstance(obj, dict):
        return {k: _sanitize_numeric_leaves(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_numeric_leaves(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def _greek_leaf(greeks: Any, iv: Optional[float], buildup: str) -> dict:
    """Assemble one side's per-strike leaf: ``iv`` + Greeks + ``oi_buildup``.

    ``greeks`` is the dict returned by :func:`bs_greeks` (each field finite-or-
    ``None``); ``iv`` is the side's implied volatility (finite-or-``None``);
    ``buildup`` is the per-strike OI-buildup label for the side. Defensive
    ``.get`` access means a malformed ``greeks`` degrades to null leaves rather
    than raising.
    """
    g = greeks if isinstance(greeks, dict) else _NULL_GREEKS
    return {
        "iv": iv,
        "delta": g.get("delta"),
        "gamma": g.get("gamma"),
        "theta": g.get("theta"),
        "vega": g.get("vega"),
        "oi_buildup": buildup,
    }


def assemble_result(
    latest: ChainSnapshot,
    prior: Optional[ChainSnapshot],
    spot: float,
    future_price: Optional[float],
    config: OptionsConfig,
) -> dict:
    """Bundle every analytic into the ``Options_Analytics_Result`` success shape.

    Given the in-memory ``latest`` snapshot, an optional ``prior`` snapshot (for
    the OI-buildup comparison), the underlying ``spot``, the best-effort near-month
    ``future_price``, and a resolved ``config``, this returns the design's success
    shape::

        {
          "underlying", "expiry", "spot", "snapshot_ts",
          "pcr_oi", "pcr_volume", "max_pain",
          "oi_buildup": {"call", "put"},
          "iv_skew":   {"put_minus_call", "slope", "atm_iv"} | None,
          "oi_walls":  {"support", "resistance"},
          "futures_basis",
          "per_strike": [ {"strike",
                           "ce": {"iv","delta","gamma","theta","vega","oi_buildup"},
                           "pe": {"iv","delta","gamma","theta","vega","oi_buildup"}}, ... ]
        }

    Every analytic is produced by the pure functions above; per-strike IV is the
    Black-Scholes inversion of the leg's ``last_price`` at the chain-derived
    time-to-expiry (:func:`_years_to_expiry`), and per-strike Greeks are computed
    from that IV. Per-strike ``oi_buildup`` classifies the leg's ΔOI/Δprice versus
    the matching strike in ``prior`` — ``"neutral"`` when no prior snapshot exists
    or the strike is unmatched (Requirement 3.3). The IV-skew consumes a
    representative per-strike IV map (the OTM side: put IV at/below spot, call IV
    above spot), excluding null-IV strikes (Requirement 2.4).

    **Guarantees** (Requirements 6.1, 6.2, 6.3, 9.1, 9.2, 9.3): the result is
    structurally complete (all listed fields present); every numeric leaf is a
    finite number or ``None`` — never ``NaN`` / ``±inf`` — enforced by a final
    sanitization pass; every value is derived from the chain data, never
    fabricated; the inputs are never mutated; and the function NEVER raises.
    """
    try:
        underlying = str(getattr(latest, "underlying", ""))
        expiry = str(getattr(latest, "expiry", ""))
        raw_ts = getattr(latest, "snapshot_ts", None)
        snapshot_ts = (
            int(raw_ts)
            if (isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool)
                and math.isfinite(raw_ts))
            else None
        )

        spot_value = float(spot) if _is_finite(spot) else None

        # Risk-free rate from config, degrading a malformed value to the default.
        r = getattr(config, "risk_free_rate", DEFAULT_RISK_FREE_RATE)
        if not _is_finite(r):
            r = DEFAULT_RISK_FREE_RATE

        # ── chain-wide analytics ──
        pcr_oi = compute_pcr_oi(latest)
        pcr_volume = compute_pcr_volume(latest)
        max_pain = compute_max_pain(latest)
        oi_walls = compute_oi_walls(latest, spot, config)
        futures_basis = compute_futures_basis(future_price, spot)
        oi_buildup = {
            "call": aggregate_oi_buildup(latest, prior, config, "call"),
            "put": aggregate_oi_buildup(latest, prior, config, "put"),
        }

        # Time-to-expiry (years) for per-strike Black-Scholes — derived purely
        # from the chain's own expiry + snapshot timestamp (never a clock).
        time_to_expiry = _years_to_expiry(expiry, snapshot_ts)

        # Index the prior snapshot by finite strike for per-strike buildup; an
        # absent prior (or unmatched strike) yields a neutral label (R3.3).
        prior_by_strike: dict = {}
        prior_strikes = getattr(prior, "strikes", None) if prior is not None else None
        if prior_strikes:
            for pq in prior_strikes:
                ps = getattr(pq, "strike", None)
                if _is_finite(ps):
                    prior_by_strike.setdefault(float(ps), pq)

        per_strike: list = []
        per_strike_iv: dict = {}
        strikes = getattr(latest, "strikes", None) or ()
        for q in strikes:
            strike = getattr(q, "strike", None)
            strike_value = float(strike) if _is_finite(strike) else None
            ce_price = getattr(q, "ce_price", None)
            pe_price = getattr(q, "pe_price", None)

            # Per-strike IV: invert each leg's observed price. A missing strike /
            # price or unparseable time-to-expiry yields a null IV (and the
            # Black-Scholes core would in any case reject T <= 0 / non-positive S,K).
            ce_iv = (
                bs_implied_vol("CE", spot, strike, time_to_expiry, r, ce_price, config)
                if (strike_value is not None and ce_price is not None
                    and time_to_expiry is not None)
                else None
            )
            pe_iv = (
                bs_implied_vol("PE", spot, strike, time_to_expiry, r, pe_price, config)
                if (strike_value is not None and pe_price is not None
                    and time_to_expiry is not None)
                else None
            )

            ce_greeks = bs_greeks("CE", spot, strike, time_to_expiry, r, ce_iv)
            pe_greeks = bs_greeks("PE", spot, strike, time_to_expiry, r, pe_iv)

            # Per-strike buildup versus the matching prior strike (neutral when
            # there is no prior snapshot or no match — never fabricated, R3.3).
            match = prior_by_strike.get(strike_value) if strike_value is not None else None
            if match is None:
                ce_buildup = BUILDUP_NEUTRAL
                pe_buildup = BUILDUP_NEUTRAL
            else:
                ce_buildup = classify_oi_buildup(
                    _signed_delta(getattr(q, "ce_oi", None), getattr(match, "ce_oi", None)),
                    _signed_delta(ce_price, getattr(match, "ce_price", None)),
                    config,
                )
                pe_buildup = classify_oi_buildup(
                    _signed_delta(getattr(q, "pe_oi", None), getattr(match, "pe_oi", None)),
                    _signed_delta(pe_price, getattr(match, "pe_price", None)),
                    config,
                )

            per_strike.append({
                "strike": strike_value,
                "ce": _greek_leaf(ce_greeks, ce_iv, ce_buildup),
                "pe": _greek_leaf(pe_greeks, pe_iv, pe_buildup),
            })

            # Representative IV for the skew: the OTM side (put IV at/below spot,
            # call IV above spot); fall back to whichever leg is solvable when
            # spot is unavailable. Null IVs are simply omitted (R2.4 / Property 6).
            if strike_value is not None:
                if spot_value is not None:
                    rep_iv = pe_iv if strike_value <= spot_value else ce_iv
                else:
                    rep_iv = ce_iv if ce_iv is not None else pe_iv
                if rep_iv is not None:
                    per_strike_iv[strike_value] = rep_iv

        iv_skew = compute_iv_skew(per_strike_iv, spot)

        result = {
            "underlying": underlying,
            "expiry": expiry,
            "spot": spot_value,
            "snapshot_ts": snapshot_ts,
            "pcr_oi": pcr_oi,
            "pcr_volume": pcr_volume,
            "max_pain": max_pain,
            "oi_buildup": oi_buildup,
            "iv_skew": iv_skew,
            "oi_walls": oi_walls,
            "futures_basis": futures_basis,
            "per_strike": per_strike,
        }
        # Final guarantee: no non-finite numeric leaf survives (R6.2 / Property 11).
        return _sanitize_numeric_leaves(result)
    except Exception as exc:  # noqa: BLE001 — totality guarantee (R6.1 / R9.1)
        print(f"[Options Warning] assemble_result failed: {exc}")
        # Honest, structurally-complete fallback: present the shape with null
        # analytics rather than raising or fabricating values.
        return {
            "underlying": str(getattr(latest, "underlying", "")),
            "expiry": str(getattr(latest, "expiry", "")),
            "spot": float(spot) if _is_finite(spot) else None,
            "snapshot_ts": None,
            "pcr_oi": None,
            "pcr_volume": None,
            "max_pain": None,
            "oi_buildup": {"call": BUILDUP_NEUTRAL, "put": BUILDUP_NEUTRAL},
            "iv_skew": None,
            "oi_walls": {"support": None, "resistance": None},
            "futures_basis": None,
            "per_strike": [],
        }

# ── Spot read (the underlying's live price — the only spot-gate input) ────────
# Mirrors ``tools.py::_read_live_ticks``: the spot is the most-recent
# ``live_ticks.last_traded_price`` for the underlying symbol, read over the same
# QuestDB HTTP ``/exec`` API via the read-only ``_questdb_select`` helper. Any
# failure / empty dataset degrades to ``None`` (Requirements 5.2, 7.2).


def _spot_symbol_candidates(underlying: str) -> list:
    """Candidate ``live_ticks`` symbol names for an index underlying's spot.

    The option chain is keyed by the NFO short name (``"NIFTY"``), but the tick
    feed stores the index SPOT under its NSE tradingsymbol (``"NIFTY 50"``) or a
    Kite-prefixed form (``"NSE:NIFTY 50"``). A literal ``symbol='NIFTY'`` lookup
    therefore finds no spot and the whole options result degrades to
    "spot price unavailable" even though the tick is present under "NIFTY 50".
    Tries the raw name first, then index aliases. Mirrors the Rust subscriber's
    ``spot_symbol_candidates`` so the options spot read matches how ticks are
    written. A single-stock underlying is its own only candidate (unchanged)."""
    out = [underlying]

    def push(s: str) -> None:
        if not any(e.strip().upper() == s.strip().upper() for e in out):
            out.append(s)

    key = underlying.strip().upper()
    if key in ("NIFTY", "NIFTY 50", "NIFTY50", "NSE:NIFTY 50"):
        push("NIFTY 50"); push("NIFTY"); push("NSE:NIFTY 50")
    elif key in ("BANKNIFTY", "NIFTY BANK", "NSE:NIFTY BANK"):
        push("NIFTY BANK"); push("BANKNIFTY"); push("NSE:NIFTY BANK")
    elif key in ("FINNIFTY", "NIFTY FIN SERVICE", "NSE:NIFTY FIN SERVICE"):
        push("NIFTY FIN SERVICE"); push("FINNIFTY"); push("NSE:NIFTY FIN SERVICE")
    elif key in ("MIDCPNIFTY", "NIFTY MIDCAP SELECT"):
        push("NIFTY MIDCAP SELECT"); push("MIDCPNIFTY")
    return out


def read_spot(underlying: str) -> Optional[float]:
    """Latest ``live_ticks.last_traded_price`` for the underlying (Requirements 5.2, 7.2).

    Issues read-only ``SELECT ... ORDER BY timestamp DESC LIMIT 1`` queries against
    the ``live_ticks`` table (the same spot source ``tools.py`` reads), trying each
    :func:`_spot_symbol_candidates` name so an index whose chain is keyed by the
    NFO short name (``"NIFTY"``) still resolves its spot stored under the NSE
    tradingsymbol (``"NIFTY 50"``). Projects the most-recent ``last_traded_price``
    to a finite positive ``float`` via :func:`_coerce_optional_float`.

    Returns ``None`` (never raises) when no candidate has a usable tick, on an
    unreachable server, query error, malformed payload, or a non-finite / non-
    positive price — the orchestrator maps that ``None`` to an
    ``Unavailable_Marker`` rather than computing from a fabricated spot (R7.2).

    Issues ONLY read-only ``SELECT`` statements (Requirement 5.4) and NEVER raises
    into its caller (Requirements 7.4, 9.1): every failure degrades to ``None``.
    """
    try:
        for cand in _spot_symbol_candidates(underlying):
            u = _escape_sql_literal(cand)
            query = (
                "SELECT last_traded_price FROM live_ticks "
                f"WHERE symbol='{u}' "
                "ORDER BY timestamp DESC LIMIT 1"
            )
            rows = _questdb_select(query)
            if not rows:
                continue
            first = rows[0]
            if not isinstance(first, (list, tuple)) or not first:
                continue
            price = _coerce_optional_float(first[0])
            if price is not None and price > 0.0:
                return price
        return None
    except Exception as exc:  # noqa: BLE001 — totality guarantee (R7.4 / R9.1)
        print(f"[Options Warning] read_spot failed: {exc}")
        return None


# ── Top-level orchestrator (read → degradation gates → pure compute → assemble) ─
# ``compute_options_analytics`` is the engine's single public entry point. It
# wires the impure read layer to the pure analytic core: it resolves config,
# reads the latest/prior chain snapshots and the spot, applies the two
# degradation gates (no snapshot / no spot → ``Unavailable_Marker``), reads the
# best-effort future price, and hands the in-memory snapshots to
# :func:`assemble_result`. It turns every reader sentinel into an honest marker
# or null field and NEVER raises into its caller (Requirements 6.1, 7.1, 7.2,
# 7.3, 7.4, 9.1).


def _options_unavailable(underlying: Any, expiry: Any, reason: str) -> dict:
    """Build the degraded ``Unavailable_Marker`` shape (Requirements 7.1, 7.2).

    Mirrors ``regime.py::_unavailable`` / ``rs.py::_rs_unavailable``: the analytic
    fields are **omitted** (never defaulted or fabricated); only the chain
    identity and an honest missing-data ``reason`` are reported.
    """
    return {
        "underlying": str(underlying),
        "expiry": str(expiry),
        "unavailable": True,
        "reason": reason,
    }


def compute_options_analytics(
    underlying: str,
    expiry: str,
    config: Optional[OptionsConfig] = None,
) -> dict:
    """Top-level options-analytics entry point (Requirements 6.1, 7.1-7.4, 9.1).

    Control flow (design's "Control flow" section):
      1. **Config.** Use the injected ``config`` when supplied, otherwise resolve
         it once via :func:`resolve_options_config` (the only place the
         environment is read — Requirement 8).
      2. **Read (impure, isolated).** Read the latest and prior
         ``option_chain_snapshots`` for ``(underlying, expiry)`` via
         :func:`read_latest_and_prior_snapshot`.
      3. **Degradation gate — no snapshot.** When no latest snapshot exists,
         return an ``Unavailable_Marker`` whose reason names the missing chain
         (Requirement 7.1) — never compute over fabricated data.
      4. **Read spot + degradation gate.** Read the underlying spot via
         :func:`read_spot`; when it is unavailable, return an
         ``Unavailable_Marker`` rather than computing spot-relative analytics from
         a fabricated spot (Requirement 7.2).
      5. **Best-effort future price.** Read the near-month future via
         :func:`read_future_price` (``None`` → ``null`` futures basis, the common
         case — Requirements 4.3, 7.3).
      6. **Pure compute + assemble.** Hand the in-memory snapshots, spot, future
         price, and config to :func:`assemble_result`, which returns the complete
         ``Options_Analytics_Result`` over the pure analytic core.

    Returns either an ``Options_Analytics_Result`` dict (success shape) or an
    ``Unavailable_Marker`` dict (degraded shape). Reader sentinels (``None`` /
    ``(None, None)``) become markers/null fields here, never exceptions: the whole
    body is guarded so the orchestrator NEVER raises into its caller (Requirements
    7.4, 9.1).
    """
    try:
        # 1. Resolve configuration (injected wins; else resolve from the env once).
        resolved_config = config if config is not None else resolve_options_config()

        # 1b. Resolve the expiry when the caller passed none. Snapshot rows are
        #     keyed by a concrete expiry, so a literal expiry='' filter matches
        #     nothing (the false "no chain snapshot" bug); pick the actively-
        #     ingested (most-recent-snapshot) expiry instead.
        if not (isinstance(expiry, str) and expiry.strip()):
            resolved_expiry = resolve_active_expiry(underlying)
            if resolved_expiry:
                expiry = resolved_expiry

        # 1c. Resolve the expiry from the EXCHANGE when QuestDB knew none. A chain we
        #     do not ingest has no rows to resolve from, which is what made every
        #     unconfigured underlying a permanent dead end.
        if not (isinstance(expiry, str) and expiry.strip()):
            listed = read_listed_expiries(underlying)
            if listed:
                expiry = listed[0]

        # 2. Read the chain — QuestDB when it is ingested, the exchange when it is
        #    not. `live_spot` is set only on the fallback path.
        latest, prior, live_spot = read_chain_for_analytics(underlying, expiry)

        # 3. Degradation gate — no chain available from either source (Requirement 7.1).
        if latest is None:
            return _options_unavailable(
                underlying,
                expiry,
                f"no chain snapshot available for {underlying} / {expiry}",
            )

        # 4. Read spot + degradation gate — spot unavailable (Requirement 7.2).
        #    `live_ticks` only carries the subscribed spot symbols, so an underlying
        #    nothing ingests has no tick either; the fallback ladder came priced
        #    against a spot, so use that rather than degrading a chain we just read.
        spot = read_spot(underlying)
        if spot is None:
            spot = live_spot
        if spot is None:
            return _options_unavailable(
                underlying,
                expiry,
                f"spot price unavailable for {underlying}",
            )

        # 5. Best-effort near-month future price (None → null futures basis).
        future_price = read_future_price(underlying)

        # 6. Pure compute over the in-memory snapshots → assembled result.
        return assemble_result(latest, prior, spot, future_price, resolved_config)
    except Exception as exc:  # noqa: BLE001 — totality guarantee (R7.4 / R9.1)
        print(f"[Options Warning] compute_options_analytics failed: {exc}")
        return _options_unavailable(
            underlying,
            expiry,
            f"options analytics unavailable for {underlying} / {expiry}",
        )


# ── Live chain fallback for underlyings that are not ingested ─────────────────
#
# Everything above reads QuestDB, which only ever holds the chains
# `option_chain_selector` subscribes to — a bounded set, and permanently so: Kite
# allows 3000 instruments on one WebSocket and the selector already spends ~1300 of
# it, nowhere near enough for every F&O-listed stock. Selecting HINDUNILVR
# therefore found zero rows and the panel reported "F&O DATA UNAVAILABLE" forever,
# with no path to recovery, even though the exchange lists it with three live
# expiries.
#
# So a chain we do not ingest is read straight from the exchange instead: the
# aggregator's `/api/kite/option_chain` resolves the listed expiries and the bounded
# ATM±band ladder out of its instrument cache, and `/api/kite/quote` prices that
# ladder in one call. Same shape, same bounds, real data — it costs no WebSocket
# budget, so it works for ANY underlying.
#
# What it cannot give is history. `oi_buildup` compares against a prior snapshot and
# there is none, so it degrades to "neutral" exactly as it does for a chain whose
# second snapshot has not landed yet (Requirement 3.3). That is a real limitation,
# reported honestly rather than filled in.

KITE_API_URL = os.getenv("KITE_API_URL", "http://127.0.0.1:8087/api/kite")


def _kite_get(path: str, params: dict, timeout: float = 10.0) -> Optional[Any]:
    """GET one aggregator Kite-proxy endpoint; ``None`` on ANY failure.

    Mirrors :func:`_questdb_select`'s degrade-to-sentinel contract so a Kite outage
    surfaces as an unavailable marker rather than an exception, and NEVER raises.

    An EMPTY ``KITE_API_URL`` turns the live fallback off altogether, and does so
    here rather than at each call site so there is one switch. Two uses: an operator
    who does not want the extra Kite calls gets the old ingested-only behaviour, and
    the test suite runs hermetically — every existing options test asserts on the
    QuestDB path, and reaching for a network in those would be both slow and a
    different contract.
    """
    if not KITE_API_URL.strip():
        return None
    try:
        r = httpx.get(f"{KITE_API_URL}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        body = r.json()
    except Exception as exc:  # noqa: BLE001 — any failure degrades to the sentinel
        print(f"[Options Warning] _kite_get {path} failed: {exc}")
        return None
    if isinstance(body, dict) and body.get("error"):
        print(f"[Options Warning] _kite_get {path}: {body['error']}")
        return None
    return body


def read_listed_expiries(underlying: str) -> list:
    """Every live expiry the exchange lists for ``underlying``, ascending.

    Independent of whether the chain is ingested, which is the point: it is what
    lets an expiry resolve for a name that has no snapshot rows. Returns ``[]`` on
    any failure or when the underlying has no listed chain; never raises.
    """
    body = _kite_get("/option_chain", {"underlying": underlying})
    if not isinstance(body, dict):
        return []
    expiries = body.get("expiries")
    if not isinstance(expiries, list):
        return []
    return [e for e in expiries if isinstance(e, str) and e.strip()]


def build_live_chain_snapshot(
    underlying: str, expiry: str
) -> Optional[tuple]:
    """Build a :class:`ChainSnapshot` for ``(underlying, expiry)`` from the exchange.

    Two calls: the aggregator resolves the bounded ladder (`/option_chain`), then
    prices it in one batch (`/quote`). Returns ``(snapshot, spot)`` — spot comes back
    with the ladder, so a non-ingested underlying whose ticks are not in
    ``live_ticks`` either can still satisfy the analytics' spot gate.

    Every numeric field goes through :func:`_coerce_optional_float`, so a missing or
    non-finite price / OI / volume is ``None`` rather than fabricated, exactly as in
    the QuestDB path. Returns ``None`` when the ladder or its prices cannot be read,
    or when no strike survives projection. NEVER raises.
    """
    try:
        body = _kite_get("/option_chain", {"underlying": underlying, "expiry": expiry})
        if not isinstance(body, dict):
            return None
        contracts = body.get("contracts")
        exchange = body.get("exchange")
        spot = _coerce_optional_float(body.get("spot"))
        if not isinstance(contracts, list) or not contracts or not isinstance(exchange, str):
            return None
        if spot is None or spot <= 0:
            # No defensible ATM-relative reading without spot; the caller reports
            # unavailable rather than computing spot-relative analytics from a guess.
            return None

        symbols = [
            c.get("tradingsymbol")
            for c in contracts
            if isinstance(c, dict) and isinstance(c.get("tradingsymbol"), str)
        ]
        if not symbols:
            return None

        # One request for the whole ladder. Kite takes up to 500 instruments per
        # quote call and the band is at most 42, so this never needs paging.
        quotes = _kite_get(
            "/quote", {"i": ",".join(f"{exchange}:{s}" for s in symbols)}, timeout=15.0
        )
        by_symbol: dict = {}
        if isinstance(quotes, dict) and isinstance(quotes.get("quotes"), list):
            for q in quotes["quotes"]:
                if isinstance(q, dict) and isinstance(q.get("symbol"), str):
                    by_symbol[q["symbol"].upper()] = q

        by_strike: dict = {}
        for c in contracts:
            if not isinstance(c, dict):
                continue
            strike = _coerce_optional_float(c.get("strike"))
            kind = _normalize_option_type(c.get("option_type"))
            symbol = c.get("tradingsymbol")
            if strike is None or kind is None or not isinstance(symbol, str):
                continue
            q = by_symbol.get(symbol.upper(), {})
            price = _coerce_optional_float(q.get("last_price"))
            oi = _coerce_optional_float(q.get("oi"))
            volume = _coerce_optional_float(q.get("volume"))

            entry = by_strike.setdefault(
                strike,
                {
                    "ce_price": None, "pe_price": None,
                    "ce_oi": None, "pe_oi": None,
                    "ce_volume": None, "pe_volume": None,
                },
            )
            if kind == "call":
                entry["ce_price"], entry["ce_oi"], entry["ce_volume"] = price, oi, volume
            else:
                entry["pe_price"], entry["pe_oi"], entry["pe_volume"] = price, oi, volume

        if not by_strike:
            return None

        strikes = tuple(
            StrikeQuote(
                strike=strike,
                ce_price=e["ce_price"], pe_price=e["pe_price"],
                ce_oi=e["ce_oi"], pe_oi=e["pe_oi"],
                ce_volume=e["ce_volume"], pe_volume=e["pe_volume"],
            )
            for strike, e in sorted(by_strike.items(), key=lambda kv: kv[0])
        )

        snapshot = ChainSnapshot(
            underlying=str(underlying),
            expiry=str(expiry),
            # Read just now, so the capture time is now. Epoch ms, matching the
            # QuestDB path's projection.
            snapshot_ts=int(datetime.now(timezone.utc).timestamp() * 1000),
            strikes=strikes,
        )
        return (snapshot, spot)
    except Exception as exc:  # noqa: BLE001 — totality guarantee
        print(f"[Options Warning] build_live_chain_snapshot failed: {exc}")
        return None


def read_chain_for_analytics(underlying: str, expiry: str) -> tuple:
    """The chain to analyse, from QuestDB if it is ingested and the exchange if not.

    Returns ``(latest, prior, live_spot)``. ``live_spot`` is non-``None`` only on the
    fallback path, where it carries the spot the ladder was priced against so the
    caller does not have to find a tick for an underlying nothing subscribes.

    Ingested chains are unaffected: the QuestDB read is tried first and returned
    untouched when it succeeds, prior snapshot included, so nothing about the ten
    configured underlyings changes.
    """
    latest, prior = read_latest_and_prior_snapshot(underlying, expiry)
    if latest is not None:
        return (latest, prior, None)

    live = build_live_chain_snapshot(underlying, expiry)
    if live is None:
        return (None, None, None)
    snapshot, spot = live
    # No prior: this is the first read of a chain nothing stores, so per-strike
    # `oi_buildup` is "neutral" rather than invented.
    return (snapshot, None, spot)
