"""Volatility_Aware_Forecaster — pure-math probabilistic forward view for Deep Quant.

The Deep Quant agent ("Alpha-Quant") reasons from candle-derived indicators and,
since the regime / relative-strength / order-flow features, from market regime,
relative strength, and order flow too. Its only *forward* projection, though, is
the Rust Predictive_Engine's OLS line fit through recent closes (``get_prediction``).
On intraday timeframes that line is close to noise: no volatility awareness, no
probability, and no notion of whether the market is trending (momentum persists)
or ranging (moves mean-revert). A veteran trader's forward view is probabilistic
and volatility-scaled. This module implements that view.

The forecaster maps an OHLCV candle sequence plus a resolved configuration (and
an optional proposed trade direction) to a structured Forecast_Label: a
Projected_Direction (``up`` / ``down`` / ``flat``), a calibrated Up_Probability in
``[0.0, 1.0]``, an Expected_Move_ATR (the expected signed next-bar move sized in
ATR units), a Forecast_Confidence in ``[0.0, 1.0]``, and a Forecast_Alignment of a
proposed trade direction with the forecast — or an honest Unavailable_Marker.

Single source of truth for the regime math (AD-3): the forecaster does NOT
reimplement regime detection; it calls ``regime.classify_regime`` and reads the
resulting ``trend_state`` to condition the drift/volatility blend (trend-
continuation in trending regimes, mean-reversion in ranging regimes, neutral in
transitional / unavailable regimes).

Scope discipline (Requirement 15): everything here is a *predictive cross-check
and calibration aid*, never a trade generator. The forecaster emits only a
Forecast_Label or an Unavailable_Marker; it never emits BUY/SELL/HOLD, never
overrides a committed decision, never blocks a trade, and never fabricates data.

Purity (Requirement 1): this module is pure Python. It performs zero network
calls, reads zero data sources other than its provided inputs (candles + config),
and touches no file/clock. Parameter *resolution* (``resolve_forecaster_config``)
is the only place the process environment is read, and it does so once up front,
deterministically, with documented defaults.

This file (task 1.1) provides the parameter-resolution foundation: the documented
default constants, the frozen ``ForecasterConfig`` dataclass, and
``resolve_forecaster_config()``. The estimation, blend, and classification
functions are added in subsequent tasks. It reuses ``regime._resolve_int`` /
``regime._resolve_float`` (the parse-with-default-and-range helpers) so the
resolution semantics match the preceding context features exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

import regime

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / out of
# range (Requirement 14.2-14.4). These are the single source of truth for the
# defaults on the live tool path, the backtest path, AND the calibration path
# (Requirement 14.5).

DEFAULT_FORECAST_DRIFT_LOOKBACK = 20   # bars of log-returns the drift is measured over
DEFAULT_FORECAST_VOL_LOOKBACK = 20     # bars of log-returns the volatility is measured over
DEFAULT_FORECAST_ATR_PERIOD = 14       # ATR period for the Expected_Move_ATR denominator
DEFAULT_FORECAST_FLAT_BAND = 0.25      # |drift|/volatility below this band => flat direction
DEFAULT_FORECAST_MIN_CANDLES = 30      # minimum valid candles required to forecast
DEFAULT_FORECAST_PROB_BINS = 10        # probability bins used by the calibration measurement
DEFAULT_FORECAST_PROB_SCALE = 2.0      # logistic steepness mapping standardized drift -> probability

# ── Environment variable names ────────────────────────────────────────────────
ENV_FORECAST_DRIFT_LOOKBACK = "FORECAST_DRIFT_LOOKBACK"
ENV_FORECAST_VOL_LOOKBACK = "FORECAST_VOL_LOOKBACK"
ENV_FORECAST_ATR_PERIOD = "FORECAST_ATR_PERIOD"
ENV_FORECAST_FLAT_BAND = "FORECAST_FLAT_BAND"
ENV_FORECAST_MIN_CANDLES = "FORECAST_MIN_CANDLES"
ENV_FORECAST_PROB_BINS = "FORECAST_PROB_BINS"
ENV_FORECAST_PROB_SCALE = "FORECAST_PROB_SCALE"

# ── Valid ranges (inclusive) ──────────────────────────────────────────────────
# Periods/counts are integers with their own minimum and no upper bound, except
# the calibration bin count which is capped at 100; the flat band and logistic
# scale are decimals with explicit lower/upper bounds (Requirement 14.1).
_DRIFT_LOOKBACK_MIN = 2
_VOL_LOOKBACK_MIN = 2
_ATR_PERIOD_MIN = 1
_MIN_CANDLES_MIN = 2
_PROB_BINS_MIN = 1
_PROB_BINS_MAX = 100
_FLAT_BAND_MIN = 0.0
_FLAT_BAND_MAX = 5.0
_PROB_SCALE_MIN = 0.0
_PROB_SCALE_MAX = 50.0


@dataclass(frozen=True)
class ForecasterConfig:
    """The resolved, validated parameter set used to produce a forecast.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the forecaster's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on the
    tool path, the backtest path, and the calibration path (Requirement 14.5).
    """

    drift_lookback: int
    vol_lookback: int
    atr_period: int
    flat_band: float
    min_candles: int
    prob_bins: int
    prob_scale: float

    @property
    def largest_lookback(self) -> int:
        """Max valid candles any single estimate requires (drives the gate).

        The drift and volatility estimates each consume their own lookback of
        log-returns and the ATR consumes ``atr_period`` true-ranges; every one of
        these is a return / range over consecutive candles, so at least one extra
        base candle is needed beyond the largest lookback. The forecaster
        additionally requires at least ``min_candles``; ``forecast`` gates on the
        max of the two.
        """
        return max(self.drift_lookback, self.vol_lookback, self.atr_period) + 1


def resolve_forecaster_config() -> ForecasterConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 14):
      * unset / empty            -> documented default (R14.2)
      * unparseable as its type  -> documented default (never raises) (R14.3)
      * parses but out of range  -> documented default (never raises) (R14.4)

    Each parameter is read from its own independent environment variable, reusing
    the ``regime._resolve_int`` / ``regime._resolve_float`` parse-with-default-and-
    range helpers so the resolution semantics match the preceding context
    features exactly. The same function is called on the tool path, the backtest
    path, and the calibration path so the resolved values are identical for
    identical environment (Requirement 14.5). This function NEVER raises.
    """
    drift_lookback = regime._resolve_int(
        ENV_FORECAST_DRIFT_LOOKBACK, DEFAULT_FORECAST_DRIFT_LOOKBACK, _DRIFT_LOOKBACK_MIN
    )
    vol_lookback = regime._resolve_int(
        ENV_FORECAST_VOL_LOOKBACK, DEFAULT_FORECAST_VOL_LOOKBACK, _VOL_LOOKBACK_MIN
    )
    atr_period = regime._resolve_int(
        ENV_FORECAST_ATR_PERIOD, DEFAULT_FORECAST_ATR_PERIOD, _ATR_PERIOD_MIN
    )
    min_candles = regime._resolve_int(
        ENV_FORECAST_MIN_CANDLES, DEFAULT_FORECAST_MIN_CANDLES, _MIN_CANDLES_MIN
    )
    # The calibration bin count has BOTH a lower bound (>= 1) and an upper bound
    # (<= 100); ``_resolve_int`` enforces only the lower bound, so the upper bound
    # is applied here — an out-of-range (too-large) value reverts to the default
    # per Requirement 14.4.
    prob_bins = regime._resolve_int(
        ENV_FORECAST_PROB_BINS, DEFAULT_FORECAST_PROB_BINS, _PROB_BINS_MIN
    )
    if prob_bins > _PROB_BINS_MAX:
        prob_bins = DEFAULT_FORECAST_PROB_BINS

    flat_band = regime._resolve_float(
        ENV_FORECAST_FLAT_BAND, DEFAULT_FORECAST_FLAT_BAND, _FLAT_BAND_MIN, _FLAT_BAND_MAX
    )
    prob_scale = regime._resolve_float(
        ENV_FORECAST_PROB_SCALE, DEFAULT_FORECAST_PROB_SCALE, _PROB_SCALE_MIN, _PROB_SCALE_MAX
    )

    return ForecasterConfig(
        drift_lookback=drift_lookback,
        vol_lookback=vol_lookback,
        atr_period=atr_period,
        flat_band=flat_band,
        min_candles=min_candles,
        prob_bins=prob_bins,
        prob_scale=prob_scale,
    )


# ── Candle-only estimation functions (pure) ──────────────────────────────────
# These map a candle sequence (plus a lookback / config) to the raw drift,
# volatility, and ATR estimates that the regime-conditioned blend (task 3.1) and
# the top-level ``forecast`` (task 4.1) consume. Every function here:
#
#   * reads candles ONLY through ``regime``'s validation helpers, so candles
#     carrying non-finite/non-numeric OHLCV fields are excluded from every
#     computation (Requirement 4.2),
#   * is pure — it never mutates its input candle sequence or configuration
#     (Requirement 1.5),
#   * is non-raising and performs zero network calls (Requirement 1.1),
#   * returns a finite ``float`` (or list of finite floats) when computable and
#     ``None`` / ``[]`` when there is no usable data (Requirements 1.2, 1.3).
#
# The drift / volatility estimates are built from *log-returns* of the closes
# (``ln(close_t / close_{t-1})``), and the ATR is built from the same Wilder
# true-range series the Regime_Classifier already uses — reusing
# ``regime._true_ranges`` so the true-range definition is identical across the
# two pure modules.


def _ewma_weights(n: int, alpha: float) -> List[float]:
    """Exponential weights for ``n`` ordered samples (oldest first).

    The most recent sample (index ``n-1``) carries the largest weight; each step
    further back is discounted by ``(1 - alpha)``. Returns ``[]`` for ``n <= 0``.
    Pure; never raises.
    """
    if n <= 0:
        return []
    one_minus = 1.0 - alpha
    return [one_minus ** (n - 1 - i) for i in range(n)]


def _ewma_mean(values: Sequence[float]) -> float:
    """Exponentially-weighted mean of ``values`` (oldest first, recent weighted).

    Uses a span-based smoothing factor ``alpha = 2 / (n + 1)`` so the half-life
    scales with the sample size, matching the conventional EWMA span convention.
    Assumes a non-empty sequence of finite numbers; the callers guarantee that.
    Pure; never raises.
    """
    n = len(values)
    alpha = 2.0 / (n + 1.0)
    weights = _ewma_weights(n, alpha)
    total_w = math.fsum(weights)
    if total_w == 0:
        # Degenerate (only possible for n == 0, which callers exclude); fall back
        # to a plain arithmetic mean rather than dividing by zero.
        return math.fsum(values) / n
    return math.fsum(w * v for w, v in zip(weights, values)) / total_w


def _ewma_std(values: Sequence[float]) -> float:
    """Exponentially-weighted standard deviation of ``values`` (oldest first).

    Computes the EWMA mean with the same weights, then the weighted mean squared
    deviation, and returns its square root — a STRICTLY NON-NEGATIVE dispersion
    measure (Requirement 1.3). Zero for a single sample or a zero-variance
    window. Pure; never raises.
    """
    n = len(values)
    if n == 0:
        return 0.0
    alpha = 2.0 / (n + 1.0)
    weights = _ewma_weights(n, alpha)
    total_w = math.fsum(weights)
    if total_w == 0:
        return 0.0
    mean = math.fsum(w * v for w, v in zip(weights, values)) / total_w
    variance = math.fsum(w * (v - mean) ** 2 for w, v in zip(weights, values)) / total_w
    if variance <= 0.0:
        return 0.0
    return math.sqrt(variance)


def compute_log_returns(candles: Any, lookback: Any) -> list:
    """Log-returns ``ln(close_t / close_{t-1})`` over the last ``lookback``+1 valid candles.

    Candles carrying non-finite/non-numeric OHLCV fields are excluded via
    ``regime._valid_ohlc_rows`` before any close is read (Requirement 4.2). The
    closes of the most recent ``lookback + 1`` valid candles form the window; the
    returned list has one fewer element than the window.

    Returns ``[]`` when:
      * ``lookback`` is not a usable positive integer,
      * fewer than two usable closes remain after validation,
      * a non-positive close is present in the window (``ln`` is undefined / the
        return is meaningless for a non-positive price), or
      * any consecutive-close ratio is non-finite or non-positive — e.g. two
        strictly-positive closes whose quotient underflows to ``0.0`` (``ln`` of
        ``0.0`` is undefined) or overflows to ``inf`` (``ln`` would be
        non-finite). The window must be fully usable, consistent with the
        non-positive-close contract above.

    Pure (no input mutation); never raises.
    """
    if not regime._valid_period(lookback):
        return []
    rows = regime._valid_ohlc_rows(candles)
    closes = [r[3] for r in rows]
    window = closes[-(lookback + 1):]
    if len(window) < 2:
        return []
    if any(c <= 0.0 for c in window):
        return []
    returns: List[float] = []
    for i in range(1, len(window)):
        ratio = window[i] / window[i - 1]
        # Even with two strictly-positive closes the ratio can underflow to 0.0
        # or overflow to inf; ``math.log`` raises on 0.0/negatives and yields a
        # non-finite value on inf. Guard the ratio so the function never raises
        # and the window is treated as unusable if any return is undefined.
        if not math.isfinite(ratio) or ratio <= 0.0:
            return []
        returns.append(math.log(ratio))
    return returns


def compute_drift(candles: Any, config: ForecasterConfig) -> Optional[float]:
    """Drift_Estimate over the drift lookback (Requirement 1.2).

    An exponentially-weighted mean of the log-returns over ``config.drift_lookback``
    — momentum that weights the most recent returns most heavily. Finite when not
    ``None``; ``None`` when there are no usable returns (insufficient or
    degenerate candles). Pure; never raises.
    """
    returns = compute_log_returns(candles, config.drift_lookback)
    if not returns:
        return None
    drift = _ewma_mean(returns)
    if not math.isfinite(drift):
        return None
    return drift


def compute_volatility(candles: Any, config: ForecasterConfig) -> Optional[float]:
    """Volatility_Estimate over the volatility lookback (Requirement 1.3).

    The primary measure is the exponentially-weighted standard deviation of the
    log-returns over ``config.vol_lookback``. It is corroborated, when available,
    by an ATR-based relative dispersion (``ATR / last_close``) computed over
    ``config.atr_period`` — the two are blended equally so a single noisy bar
    cannot dominate the estimate. The result is STRICTLY NON-NEGATIVE (both
    components are non-negative and the blend preserves that). Finite when not
    ``None``; ``None`` when there are no usable returns. Pure; never raises.
    """
    returns = compute_log_returns(candles, config.vol_lookback)
    if not returns:
        return None

    ewma_std = _ewma_std(returns)

    # Corroborate with an ATR-based relative dispersion when both the ATR and a
    # positive reference close are available. ATR is an absolute price move, so
    # it is normalised by the latest valid close to be comparable to the
    # log-return standard deviation.
    atr = compute_atr(candles, config.atr_period)
    atr_rel: Optional[float] = None
    if atr is not None:
        rows = regime._valid_ohlc_rows(candles)
        if rows:
            last_close = rows[-1][3]
            if last_close > 0.0:
                atr_rel = atr / last_close

    if atr_rel is not None and math.isfinite(atr_rel) and atr_rel >= 0.0:
        volatility = 0.5 * ewma_std + 0.5 * atr_rel
    else:
        volatility = ewma_std

    if not math.isfinite(volatility):
        return None
    # Strictly non-negative by construction; clamp the tiny floating-point
    # residual that could otherwise leave a tiny negative value.
    if volatility < 0.0:
        return 0.0
    return volatility


def compute_atr(candles: Any, period: Any) -> Optional[float]:
    """Average True Range over ``period`` — the Expected_Move_ATR denominator.

    Builds the Wilder true-range series over the valid candles (reusing
    ``regime._true_ranges`` so the definition matches the Regime_Classifier) and
    averages the most recent ``period`` true ranges. Returns ``None`` when:
      * ``period`` is not a usable positive integer,
      * there are fewer than ``period`` true ranges (insufficient candles), or
      * the resulting ATR is zero (a flat, zero-range window — a zero
        denominator for Expected_Move_ATR).

    Pure (no input mutation); never raises.
    """
    if not regime._valid_period(period):
        return None
    rows = regime._valid_ohlc_rows(candles)
    trs = regime._true_ranges(rows)
    if len(trs) < period:
        return None
    atr = math.fsum(trs[-period:]) / period
    if not math.isfinite(atr) or atr <= 0.0:
        return None
    return atr


# ── Regime conditioning weights ───────────────────────────────────────────────
# The regime-conditioned standardized drift ``z`` starts from the unweighted
# ``drift / volatility`` and is re-weighted by the trend state (design's "Regime
# conditioning" table):
#
#   * trending     -> trend-continuation: AMPLIFY ``z`` in the drift's own
#                     direction (momentum persists), weight >= 1.
#   * ranging      -> mean-reversion: DAMPEN ``z`` toward the recent mean (moves
#                     revert), 0 <= weight <= 1.
#   * transitional -> neutral: the unweighted standardized drift (weight == 1).
#   * unavailable  -> neutral: the unweighted standardized drift (weight == 1).
#
# Because the trending weight is >= 1 and the ranging weight is in [0, 1], the
# trending blend is always weighted at least as far toward the drift's own
# direction as the neutral blend, and the ranging blend never further than the
# neutral blend (Property 4 / Requirements 2.2, 2.3).
_TREND_CONTINUATION_WEIGHT = 1.5
_RANGE_REVERSION_WEIGHT = 0.5
_NEUTRAL_WEIGHT = 1.0


def conditioned_drift(
    drift: Optional[float],
    volatility: Optional[float],
    trend_state: Any,
    config: ForecasterConfig,
) -> float:
    """Regime-conditioned standardized drift ``z`` (Requirements 2.2, 2.3, 2.4).

    Starts from the raw standardized drift ``drift / volatility`` (drift relative
    to volatility) and re-weights it by the regime ``trend_state``:

      * ``'trending'``     -> trend-continuation (amplify toward the drift's own
        direction, weight ``_TREND_CONTINUATION_WEIGHT`` >= 1),
      * ``'ranging'``      -> mean-reversion (dampen toward the recent mean,
        weight ``_RANGE_REVERSION_WEIGHT`` in ``[0, 1]``),
      * ``'transitional'`` OR any other / missing / unavailable state -> neutral
        (the unweighted standardized drift).

    Total over the trend-state set: every ``trend_state`` value (including
    ``None`` or an unrecognized string standing in for an unavailable regime)
    maps to exactly one weighting and never raises. Returns ``0.0`` (a flat,
    zero standardized drift) whenever the drift or volatility is missing,
    non-finite, or the volatility is non-positive — so a degenerate or
    zero-variance window never divides by zero (Requirement 4.5). Pure.
    """
    if drift is None or volatility is None:
        return 0.0
    if not math.isfinite(drift) or not math.isfinite(volatility):
        return 0.0
    if volatility <= 0.0:
        return 0.0

    base = drift / volatility
    if not math.isfinite(base):
        return 0.0

    if trend_state == "trending":
        weight = _TREND_CONTINUATION_WEIGHT
    elif trend_state == "ranging":
        weight = _RANGE_REVERSION_WEIGHT
    else:
        # 'transitional', an unavailable regime (None / unrecognized), or any
        # other value -> neutral, unweighted standardized drift (Requirement 2.4).
        weight = _NEUTRAL_WEIGHT

    z = base * weight
    if not math.isfinite(z):
        return 0.0
    return z


def classify_direction(z: Any, config: ForecasterConfig) -> str:
    """Projected_Direction from the standardized drift ``z`` (Requirement 3.1).

    Returns exactly one of ``'up'`` / ``'down'`` / ``'flat'`` per the design's
    flat-band mapping table:

      * ``abs(z) <= flat_band`` -> ``'flat'``,
      * ``z > flat_band``       -> ``'up'``,
      * ``z < -flat_band``      -> ``'down'``.

    A non-finite or non-numeric ``z`` is treated as ``0`` (``'flat'``) so the
    function is total and never raises. Pure.
    """
    try:
        zf = float(z)
    except (TypeError, ValueError):
        return "flat"
    if not math.isfinite(zf):
        return "flat"
    flat_band = config.flat_band
    if abs(zf) <= flat_band:
        return "flat"
    return "up" if zf > 0.0 else "down"


def up_probability(z: Any, config: ForecasterConfig) -> float:
    """Up_Probability — logistic map of the standardized drift (Requirements 3.2, 3.5, 4.4).

    ``p = clamp(1 / (1 + exp(-prob_scale * z)), 0.0, 1.0)``. The logistic is
    monotone increasing through ``0.5`` at ``z == 0``, so ``z >= 0 => p >= 0.5``
    and ``z <= 0 => p <= 0.5`` — giving the direction/probability consistency of
    Requirement 3.5 (``up => p >= 0.5``, ``down => p <= 0.5``). Returns exactly
    ``0.5`` when ``z == 0`` (or a non-finite/non-numeric ``z``). Always finite and
    clamped to ``[0.0, 1.0]``. Pure; never raises.
    """
    try:
        zf = float(z)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(zf) or zf == 0.0:
        return 0.5

    exponent = -config.prob_scale * zf
    # Guard the logistic against overflow for large-magnitude exponents: a very
    # large positive exponent saturates ``exp`` toward +inf (p -> 0.0); a very
    # large negative exponent saturates toward 0 (p -> 1.0).
    try:
        p = 1.0 / (1.0 + math.exp(exponent))
    except OverflowError:
        p = 0.0 if exponent > 0.0 else 1.0

    if not math.isfinite(p):
        return 0.5
    return regime._clamp(p, 0.0, 1.0)


def forecast_confidence(z: Any, config: ForecasterConfig) -> float:
    """Forecast_Confidence from the standardized drift ``z`` (Requirements 3.4, 4.4, 4.5).

    ``confidence = clamp(2 * abs(up_probability(z) - 0.5), 0.0, 1.0)`` — a
    strictly increasing function of ``abs(z)`` because ``up_probability`` is
    monotone in ``z`` and symmetric about ``0.5``. Equals ``0.0`` when ``z == 0``
    (flat / zero drift). Always finite and clamped to ``[0.0, 1.0]``. Pure;
    never raises.
    """
    p = up_probability(z, config)
    confidence = 2.0 * abs(p - 0.5)
    if not math.isfinite(confidence):
        return 0.0
    return regime._clamp(confidence, 0.0, 1.0)


# ── Forecast_Alignment derivation ─────────────────────────────────────────────
# A proposed trade direction may be expressed as a forecast-style direction
# (``up`` / ``down``), an order side (``buy`` / ``sell``), or a position side
# (``long`` / ``short``). Map each to the projected-direction side it agrees with;
# anything else (``None`` / empty / ``hold`` / unrecognized) is "no proposed
# direction" and yields ``neutral`` (Requirement 3.6).
_PROPOSED_UP_SIDE = frozenset({"up", "buy", "long"})
_PROPOSED_DOWN_SIDE = frozenset({"down", "sell", "short"})


def derive_forecast_alignment(
    projected_direction: Any,
    proposed_direction: Any,
) -> str:
    """Forecast_Alignment over (Projected_Direction x proposed_direction) (Requirement 3.6).

    Returns exactly one of ``'aligned'`` / ``'misaligned'`` / ``'neutral'`` per
    the design's alignment tables. The proposed direction is normalized so a
    BUY/long/up proposal is the ``up`` side and a SELL/short/down proposal is the
    ``down`` side:

      * BUY side  : projected ``up`` -> aligned, ``down`` -> misaligned, ``flat`` -> neutral
      * SELL side : projected ``down`` -> aligned, ``up`` -> misaligned, ``flat`` -> neutral
      * no proposed direction (``None`` / empty / HOLD / unrecognized) -> ``neutral``
        for every Projected_Direction.

    Total function: every combination of the two inputs maps to exactly one
    Alignment value. Pure; never raises.
    """
    proposed_side = _normalize_proposed_direction(proposed_direction)
    if proposed_side is None:
        # No proposed direction (or HOLD) -> neutral for every projected dir.
        return "neutral"

    projected = projected_direction if isinstance(projected_direction, str) else ""
    projected = projected.strip().lower()

    if projected == "flat":
        return "neutral"
    if projected not in ("up", "down"):
        # An unrecognized / missing projected direction cannot agree or oppose.
        return "neutral"

    return "aligned" if projected == proposed_side else "misaligned"


def _normalize_proposed_direction(proposed_direction: Any) -> Optional[str]:
    """Normalize a proposed trade direction to ``'up'`` / ``'down'`` / ``None``.

    ``up`` / ``buy`` / ``long`` -> ``'up'``; ``down`` / ``sell`` / ``short`` ->
    ``'down'``; ``None`` / empty / whitespace / ``hold`` / any unrecognized value
    -> ``None`` (treated as "no proposed direction"). Pure; never raises.
    """
    if not isinstance(proposed_direction, str):
        return None
    token = proposed_direction.strip().lower()
    if not token:
        return None
    if token in _PROPOSED_UP_SIDE:
        return "up"
    if token in _PROPOSED_DOWN_SIDE:
        return "down"
    return None


# ── Unavailable_Marker / Forecast_Label helpers ───────────────────────────────
# The named measure fields surfaced under the label's ``measures`` object. Kept
# as the single source of truth so the top-level ``forecast`` and the tool-level
# contract (tools._FORECAST_MEASURE_FIELDS) agree on the shape.
_FORECAST_MEASURE_FIELDS = ("drift", "volatility", "standardized_drift", "atr")

# Trend-state value recorded on the label when the regime classifier returns an
# Unavailable_Marker (no usable ``trend_state``): the forecast is still produced
# under a neutral blend, but the auditability field records that no regime
# conditioning was applied (Requirement 2.4, 2.5).
_REGIME_UNAVAILABLE_TREND_STATE = "unavailable"


def _forecast_unavailable(
    reason: str,
    symbol: Optional[str],
    timeframe: Optional[str],
) -> dict:
    """Build an honest Unavailable_Marker (Requirements 6.2, 6.3).

    Mirrors ``regime._unavailable`` / the order-flow / relative-strength marker
    conventions: it carries ``{"unavailable": true, "reason": ...}`` plus the
    caller-supplied ``symbol`` / ``timeframe`` context, and *omits*
    ``projected_direction`` / ``up_probability`` / ``expected_move_atr`` /
    ``forecast_confidence`` / ``forecast_alignment`` entirely — they are never
    defaulted or fabricated (AD-5, Requirement 6.3). Pure; never raises.
    """
    marker: dict = {}
    if symbol is not None:
        marker["symbol"] = symbol
    if timeframe is not None:
        marker["timeframe"] = timeframe
    marker["unavailable"] = True
    marker["reason"] = reason
    return marker


def _regime_trend_state(candles: Any) -> Optional[str]:
    """Resolve the regime ``trend_state`` used to condition the blend (AD-3).

    Single source of truth for the regime math: calls
    ``regime.classify_regime(candles, regime.resolve_regime_config())`` and reads
    the resulting ``trend_state``. Returns the ``trending`` / ``ranging`` /
    ``transitional`` string when the classifier produced a usable Regime_Label,
    or ``None`` when it returned an Unavailable_Marker (or any non-label) — which
    ``conditioned_drift`` treats as a neutral blend (Requirements 2.1, 2.4, 2.5).
    ``classify_regime`` is itself pure and non-raising. Pure.
    """
    regime_result = regime.classify_regime(candles, regime.resolve_regime_config())
    if not isinstance(regime_result, dict):
        return None
    if regime_result.get("unavailable"):
        return None
    trend_state = regime_result.get("trend_state")
    if isinstance(trend_state, str):
        return trend_state
    return None


def _expected_move_atr(
    candles: Any,
    drift: Optional[float],
    atr: Optional[float],
) -> Optional[float]:
    """Expected signed next-bar move expressed in ATR units (Requirement 3.3).

    The drift is the expected next-bar log-return, so the expected signed price
    move applied to the latest valid close is ``last_close * (exp(drift) - 1)``
    (the exact price change implied by a log-return; ``~= last_close * drift`` for
    the small drifts seen on a single bar). Dividing by the ATR re-expresses that
    move in ATR units, so an Expected_Move_ATR of ``0.5`` means "about half an
    average bar's range, upward".

    Returns ``None`` (``null``) when the ATR is zero or unavailable (a zero /
    missing denominator), when the drift is missing/non-finite, when no positive
    reference close is available, or when the result is non-finite — never
    dividing by zero. Pure; never raises.
    """
    if atr is None or not math.isfinite(atr) or atr <= 0.0:
        return None
    if drift is None or not math.isfinite(drift):
        return None
    rows = regime._valid_ohlc_rows(candles)
    if not rows:
        return None
    last_close = rows[-1][3]
    if not math.isfinite(last_close) or last_close <= 0.0:
        return None
    try:
        expected_price_move = last_close * (math.exp(drift) - 1.0)
    except OverflowError:
        return None
    if not math.isfinite(expected_price_move):
        return None
    expected_move_atr = expected_price_move / atr
    if not math.isfinite(expected_move_atr):
        return None
    return expected_move_atr


def forecast(
    candles: Any,
    config: ForecasterConfig,
    proposed_direction: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Top-level entry point: map candles + config to a Forecast_Label or marker.

    Returns either a Forecast_Label dict (``projected_direction`` /
    ``up_probability`` / ``expected_move_atr`` / ``forecast_confidence`` /
    ``forecast_alignment`` / ``measures`` / ``regime_trend_state`` — plus
    ``symbol`` / ``timeframe`` when supplied — and ``candles_used``) or an honest
    Unavailable_Marker dict.

    Behaviour (Requirements 1, 2, 3, 4, 6, 15):
      * Computes the drift, volatility, and ATR from the *valid* candles only —
        candles carrying non-finite/non-numeric OHLCV fields are excluded
        (Requirement 4.2).
      * Obtains the trend state from ``regime.classify_regime`` and conditions the
        standardized drift accordingly; a ``transitional`` state or an
        Unavailable_Marker maps to a neutral blend and never blocks the forecast
        (Requirements 2.1, 2.4, 2.5).
      * Insufficient candles -> Unavailable_Marker citing the received and
        required counts; the marker omits the projection fields (Requirements
        4.1, 6.2, 6.3).
      * Zero-variance window (Volatility_Estimate ``0`` / unavailable) ->
        short-circuits to ``projected_direction = "flat"``, ``up_probability =
        0.5``, ``forecast_confidence = 0.0``, never dividing by zero (Requirement
        4.5).
      * Expected_Move_ATR is ``null`` when the ATR is zero / unavailable
        (Requirement 3.3).
      * Pure (no input mutation — Requirement 1.5), deterministic (Requirements
        1.4, 4.6), and non-raising (Requirement 4). Emits ONLY a label or marker
        — never a BUY/SELL/HOLD action, conviction, or decision field
        (Requirements 15.1, 15.2, 15.3).
    """
    try:
        valid_rows = regime._valid_ohlc_rows(candles)
        received = len(valid_rows)

        # Sufficiency gate: the forecaster requires at least ``min_candles`` and
        # at least the largest single-estimate lookback (Requirements 4.1, 6.2).
        required = max(config.min_candles, config.largest_lookback)
        if received < required:
            return _forecast_unavailable(
                f"insufficient data: {received} valid candles received, "
                f"{required} required",
                symbol,
                timeframe,
            )

        # Candle-only estimates from the valid candles (each excludes invalid
        # rows itself and returns ``None`` on a degenerate / zero-denominator
        # window).
        drift = compute_drift(candles, config)
        volatility = compute_volatility(candles, config)
        atr = compute_atr(candles, config.atr_period)

        # Single source of truth for the regime math (AD-3): condition the
        # standardized drift on the classifier's trend state. ``None`` (an
        # Unavailable_Marker / non-label) is recorded as ``"unavailable"`` and
        # blended neutrally.
        trend_state = _regime_trend_state(candles)
        regime_trend_state = (
            trend_state if trend_state is not None else _REGIME_UNAVAILABLE_TREND_STATE
        )

        # Expected_Move_ATR is independent of the zero-volatility short-circuit:
        # it is ``null`` exactly when the ATR is zero / unavailable (Requirement
        # 3.3).
        expected_move_atr = _expected_move_atr(candles, drift, atr)

        if volatility is None or not math.isfinite(volatility) or volatility <= 0.0:
            # Zero-variance window: short-circuit to a flat, maximally-uncertain
            # forecast rather than dividing by zero (Requirement 4.5). The
            # standardized drift ``z`` is exactly ``0``.
            z = 0.0
            projected_direction = "flat"
            up_prob = 0.5
            confidence = 0.0
        else:
            z = conditioned_drift(drift, volatility, trend_state, config)
            projected_direction = classify_direction(z, config)
            up_prob = up_probability(z, config)
            confidence = forecast_confidence(z, config)

        forecast_alignment = derive_forecast_alignment(
            projected_direction, proposed_direction
        )

        # Each measure is a finite number or ``null`` (Requirement 4.3). The
        # regime-conditioned standardized drift ``z`` is always a finite float.
        measures = {
            "drift": drift,
            "volatility": volatility,
            "standardized_drift": z,
            "atr": atr,
        }

        label: dict = {
            "projected_direction": projected_direction,
            "up_probability": up_prob,
            "expected_move_atr": expected_move_atr,
            "forecast_confidence": confidence,
            "forecast_alignment": forecast_alignment,
            "measures": measures,
            "regime_trend_state": regime_trend_state,
        }
        if symbol is not None:
            label["symbol"] = symbol
        if timeframe is not None:
            label["timeframe"] = timeframe
        label["candles_used"] = received
        return label
    except Exception as exc:  # pragma: no cover - defensive; forecaster is pure
        # The forecaster must never raise into its callers (Requirement 4). Any
        # unexpected failure degrades to an honest Unavailable_Marker rather than
        # an exception or a fabricated label.
        return _forecast_unavailable(
            f"forecast error: {exc.__class__.__name__}",
            symbol,
            timeframe,
        )
