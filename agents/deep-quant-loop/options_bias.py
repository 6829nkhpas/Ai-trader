"""Options_Bias_Classifier — pure-math options-positioning bias for Deep Quant.

The Deep Quant agent ("Alpha-Quant") reasons from candle-derived indicators and,
since the regime, relative-strength, and order-flow features, from market regime,
relative strength, and order flow too — but it has still been blind to *options
positioning*: where institutional money has written its open interest, where the
max-pain pin sits, how the PCR is leaning, and where the heavy OI walls are. On
NSE that positioning is the single biggest source of intraday edge. This module
reads the F2 ``Options_Analytics_Result`` and summarises it into a categorical
``Options_Bias_State`` (``bullish`` / ``bearish`` / ``neutral``) plus an
``Alignment`` of a proposed trade direction with that bias.

Scope discipline (Requirement 10): everything here is a *filter / context aid*,
never a trade generator. The classifier maps an analytics result and an optional
proposed direction to a structured ``Options_Bias_Label``; it never emits
BUY/SELL/HOLD, never blocks a trade, and never fabricates data.

Purity (Requirement 1): this module is pure Python. It performs zero network
calls, reads zero data sources other than its provided inputs (the analytics
dict + config + proposed direction), and touches no file/clock. Parameter
*resolution* (``resolve_options_bias_config``) is the only place the process
environment is read, and it does so once up front, deterministically, with
documented defaults.

Task 1.1 provided the parameter-resolution foundation: the documented default
constants, the enum constants, the frozen ``OptionsBiasConfig`` dataclass, and
``resolve_options_bias_config()``. Task 2.1 adds the pure classifier: the
per-signal directional vote helpers, ``classify_options_bias`` (which nets the
votes into an ``options_bias_state`` and assembles the ``Options_Bias_Label``),
and ``derive_alignment`` (a total function of bias-state × proposed-direction).
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass
from typing import Any, Optional

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / non-finite
# / out of range (Requirements 9.1, 9.2). These are the single source of truth for
# the defaults on both the live tool path and any downstream consumer.

DEFAULT_PCR_BULLISH_CUTOFF = 1.3      # PCR(OI) >= this => bullish vote (put-writing = support below)
DEFAULT_PCR_BEARISH_CUTOFF = 0.7      # PCR(OI) <= this => bearish vote (call-writing = resistance above)
DEFAULT_OI_WALL_PROXIMITY_PCT = 0.01  # |wall - spot| / spot <= this => wall is "near" spot
DEFAULT_IV_SKEW_THRESHOLD = 0.0       # put_minus_call > this => bearish skew; < -this => bullish skew
DEFAULT_FUTURES_BASIS_THRESHOLD = 0.0  # basis > this => bullish (premium); < -this => bearish (discount)

# ── Environment variable names ────────────────────────────────────────────────
ENV_PCR_BULLISH_CUTOFF = "OPTIONS_BIAS_PCR_BULLISH_CUTOFF"
ENV_PCR_BEARISH_CUTOFF = "OPTIONS_BIAS_PCR_BEARISH_CUTOFF"
ENV_OI_WALL_PROXIMITY_PCT = "OPTIONS_BIAS_OI_WALL_PROXIMITY_PCT"
ENV_IV_SKEW_THRESHOLD = "OPTIONS_BIAS_IV_SKEW_THRESHOLD"
ENV_FUTURES_BASIS_THRESHOLD = "OPTIONS_BIAS_FUTURES_BASIS_THRESHOLD"

# ── Enum constants ────────────────────────────────────────────────────────────
# The categorical outputs of the classifier. ``OPTIONS_BIAS_STATES`` is the set of
# possible ``options_bias_state`` values; ``ALIGNMENT_VALUES`` is the set of
# possible ``alignment`` values. ``MIN_SIGNALS_FOR_BIAS`` is the minimum number of
# non-null contributing signals required to form a directional bias; below this,
# the state is ``neutral`` rather than a fabricated directional bias (R1.5).
OPTIONS_BIAS_STATES = ("bullish", "bearish", "neutral")
ALIGNMENT_VALUES = ("aligned", "misaligned", "neutral")
MIN_SIGNALS_FOR_BIAS = 2

# ── Valid ranges ──────────────────────────────────────────────────────────────
# Per the design's OptionsBiasConfig range table:
#   * the PCR cutoffs are decimals in (0.0, inf) — strictly positive, unbounded;
#   * the OI-wall proximity is a decimal fraction in [0.0, 1.0];
#   * the IV-skew and futures-basis thresholds are decimals in [0.0, inf).
_PCR_MIN = 0.0          # exclusive lower bound for the PCR cutoffs
_OI_WALL_PROXIMITY_MIN = 0.0
_OI_WALL_PROXIMITY_MAX = 1.0
_THRESHOLD_MIN = 0.0    # inclusive lower bound for the IV-skew / basis thresholds


@dataclass(frozen=True)
class OptionsBiasConfig:
    """The resolved, validated threshold set used to classify the options bias.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the classifier's purity guarantee). For identical
    environment-variable values the resolved configuration is identical wherever
    it is resolved (Requirement 9).
    """

    pcr_bullish_cutoff: float
    pcr_bearish_cutoff: float
    oi_wall_proximity_pct: float
    iv_skew_threshold: float
    futures_basis_threshold: float


def _resolve_float(
    env_name: str,
    default: float,
    low: float,
    high: float,
    low_exclusive: bool = False,
) -> float:
    """Resolve one float parameter from its own env var (Requirements 9.1, 9.2).

    Mirrors the ``_resolve_float`` convention in ``rs.py`` / ``order_flow.py``:
    falls back to ``default`` when the var is unset/empty, cannot be parsed as a
    float, is non-finite (NaN/inf), or parses but falls outside the valid range.
    The range is ``[low, high]`` inclusive by default; pass ``low_exclusive=True``
    to make the lower bound exclusive (i.e. the open range ``(low, high]``), used
    for the strictly-positive PCR cutoffs. ``high`` may be ``math.inf`` for an
    unbounded upper limit. Never raises.
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
    if low_exclusive:
        if value <= low:
            return default
    elif value < low:
        return default
    if value > high:
        return default
    return value


def resolve_options_bias_config() -> OptionsBiasConfig:
    """Resolve every threshold from its own env var with documented defaults.

    Per-parameter rules (Requirement 9):
      * unset / empty / unparseable / non-finite / out of range -> documented
        default, without raising (R9.1, R9.2).
      * the PCR ordering rule requires ``pcr_bearish_cutoff < pcr_bullish_cutoff``;
        when that ordering does not hold after the per-parameter resolution above,
        BOTH PCR cutoffs revert to their documented defaults together (R9.3) —
        which restores the valid ``0.7 < 1.3`` ordering.

    Resolution is deterministic — for identical environment values it always
    returns an identical configuration — and NEVER raises.
    """
    pcr_bullish_cutoff = _resolve_float(
        ENV_PCR_BULLISH_CUTOFF,
        DEFAULT_PCR_BULLISH_CUTOFF,
        _PCR_MIN,
        math.inf,
        low_exclusive=True,
    )
    pcr_bearish_cutoff = _resolve_float(
        ENV_PCR_BEARISH_CUTOFF,
        DEFAULT_PCR_BEARISH_CUTOFF,
        _PCR_MIN,
        math.inf,
        low_exclusive=True,
    )
    oi_wall_proximity_pct = _resolve_float(
        ENV_OI_WALL_PROXIMITY_PCT,
        DEFAULT_OI_WALL_PROXIMITY_PCT,
        _OI_WALL_PROXIMITY_MIN,
        _OI_WALL_PROXIMITY_MAX,
    )
    iv_skew_threshold = _resolve_float(
        ENV_IV_SKEW_THRESHOLD,
        DEFAULT_IV_SKEW_THRESHOLD,
        _THRESHOLD_MIN,
        math.inf,
    )
    futures_basis_threshold = _resolve_float(
        ENV_FUTURES_BASIS_THRESHOLD,
        DEFAULT_FUTURES_BASIS_THRESHOLD,
        _THRESHOLD_MIN,
        math.inf,
    )

    # Enforce the PCR ordering rule (R9.3): the bearish cutoff must be strictly
    # below the bullish cutoff. If it does not hold (after the per-parameter
    # resolution above), BOTH PCR cutoffs revert to their documented defaults
    # together, restoring the valid 0.7 < 1.3 ordering.
    if not (pcr_bearish_cutoff < pcr_bullish_cutoff):
        pcr_bullish_cutoff = DEFAULT_PCR_BULLISH_CUTOFF
        pcr_bearish_cutoff = DEFAULT_PCR_BEARISH_CUTOFF

    return OptionsBiasConfig(
        pcr_bullish_cutoff=pcr_bullish_cutoff,
        pcr_bearish_cutoff=pcr_bearish_cutoff,
        oi_wall_proximity_pct=oi_wall_proximity_pct,
        iv_skew_threshold=iv_skew_threshold,
        futures_basis_threshold=futures_basis_threshold,
    )


# ── OI-buildup label vocabulary (mirrors the F2 ``options.py`` classification) ─
# The aggregate ``oi_buildup`` field of an Options_Analytics_Result is an object
# ``{"call": <label>, "put": <label>}`` where each label is one of the five F2
# OI-buildup categories below (or ``"neutral"`` when no direction is defined).
BUILDUP_LONG = "long_buildup"           # rising OI + rising price
BUILDUP_SHORT = "short_buildup"         # rising OI + falling price (fresh writing)
BUILDUP_SHORT_COVERING = "short_covering"  # falling OI + rising price
BUILDUP_LONG_UNWINDING = "long_unwinding"  # falling OI + falling price
BUILDUP_NEUTRAL = "neutral"             # dead-banded / undefined change

# Per-(side, label) directional membership for the aggregate OI-buildup vote,
# transcribed from the design's signal-to-vote table. A given (side, label) may
# appear in both sets (e.g. a side whose reading is genuinely ambiguous); the
# aggregate vote nets the bullish against the bearish memberships across the
# call and put sides, so an ambiguous side cancels itself and a mixed pair (one
# bullish side, one bearish side) nets to no vote.
_OI_BULLISH_COMBOS = frozenset({
    ("put", BUILDUP_LONG),            # put longs accumulating (support being bought)
    ("put", BUILDUP_SHORT_COVERING),  # put shorts covering
    ("call", BUILDUP_SHORT),          # call writing / unwinding overhead → upside room
    ("call", BUILDUP_LONG_UNWINDING),
})
_OI_BEARISH_COMBOS = frozenset({
    ("call", BUILDUP_LONG),           # call longs accumulating (resistance / hedging up)
    ("call", BUILDUP_SHORT),          # heavy call writing overhead (resistance)
    ("put", BUILDUP_LONG_UNWINDING),  # put longs exiting (support eroding)
    ("put", BUILDUP_SHORT),           # fresh put writing below (downside pressure)
})


def _is_number(x: Any) -> bool:
    """True iff ``x`` is a real, finite number (rejects ``None``/``bool``/NaN/inf).

    A non-numeric, ``None``, ``bool``, or non-finite value is *not* a usable
    signal value: per Requirement 1.4 it is excluded from the vote rather than
    treated as a value. Mirrors the ``_is_finite`` convention in ``options.py``.
    """
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
    )


# ── Per-signal directional votes ──────────────────────────────────────────────
# Each helper reads one signal from the F2 Options_Analytics_Result and returns a
# single directional vote: ``+1`` (bullish), ``-1`` (bearish), or ``0`` (no vote).
# A ``null`` / non-finite / structurally-absent signal returns ``0`` so it is
# excluded from the bias rather than treated as a value (Requirement 1.4). Each
# helper is pure, deterministic, and never raises.


def _pcr_vote(pcr_oi: Any, config: OptionsBiasConfig) -> int:
    """PCR(OI) vote against the configured bullish/bearish cutoffs.

    ``pcr_oi >= pcr_bullish_cutoff`` (put-heavy = support below) → bullish; ``<=
    pcr_bearish_cutoff`` (call-heavy = resistance above) → bearish; between the
    cutoffs, or ``null``, → no vote.
    """
    if not _is_number(pcr_oi):
        return 0
    if pcr_oi >= config.pcr_bullish_cutoff:
        return 1
    if pcr_oi <= config.pcr_bearish_cutoff:
        return -1
    return 0


def _oi_buildup_vote(oi_buildup: Any) -> int:
    """Aggregate OI-buildup vote netting the call and put side readings.

    Reads ``{"call": <label>, "put": <label>}`` and nets the per-(side, label)
    bullish memberships against the bearish memberships from the design's table.
    Net positive → bullish, net negative → bearish, a tie (mixed / neutral /
    ambiguous / structurally absent) → no vote.
    """
    if not isinstance(oi_buildup, dict):
        return 0
    bullish = 0
    bearish = 0
    for side in ("call", "put"):
        label = oi_buildup.get(side)
        if not isinstance(label, str):
            continue
        if (side, label) in _OI_BULLISH_COMBOS:
            bullish += 1
        if (side, label) in _OI_BEARISH_COMBOS:
            bearish += 1
    if bullish > bearish:
        return 1
    if bearish > bullish:
        return -1
    return 0


def _max_pain_vote(max_pain: Any, spot: Any, config: OptionsBiasConfig) -> int:
    """Max-pain-versus-spot vote (pin direction).

    ``max_pain`` meaningfully above spot → bullish (pin pulls price up); below →
    bearish; within the proximity band ``|max_pain - spot| / |spot| <=
    oi_wall_proximity_pct`` (pinned at spot, no directional pull), or either side
    ``null`` / a zero spot, → no vote.
    """
    if not (_is_number(max_pain) and _is_number(spot)) or spot == 0:
        return 0
    if abs(max_pain - spot) / abs(spot) <= config.oi_wall_proximity_pct:
        return 0
    if max_pain > spot:
        return 1
    if max_pain < spot:
        return -1
    return 0


def _oi_walls_vote(oi_walls: Any, spot: Any, config: OptionsBiasConfig) -> int:
    """Nearest-OI-wall vote relative to spot within the proximity band.

    The nearest wall being a ``support`` within the proximity band (with the
    ``resistance`` far or absent) → bullish (price sitting on support); the
    nearest wall being a ``resistance`` within the band (heavy call wall just
    overhead, support far or absent) → bearish. Walls absent, symmetric (both
    near), both far, ``null``, or a zero spot → no vote.
    """
    if not isinstance(oi_walls, dict) or not _is_number(spot) or spot == 0:
        return 0
    support = oi_walls.get("support")
    resistance = oi_walls.get("resistance")
    prox = config.oi_wall_proximity_pct
    support_near = _is_number(support) and abs(support - spot) / abs(spot) <= prox
    resistance_near = (
        _is_number(resistance) and abs(resistance - spot) / abs(spot) <= prox
    )
    if support_near and not resistance_near:
        return 1
    if resistance_near and not support_near:
        return -1
    return 0


def _iv_skew_vote(iv_skew: Any, config: OptionsBiasConfig) -> int:
    """IV-skew vote on ``put_minus_call`` against the configured threshold.

    ``put_minus_call < -iv_skew_threshold`` (calls bid up) → bullish; ``>
    iv_skew_threshold`` (puts bid up = downside hedging) → bearish; within the
    band, a non-object ``iv_skew``, or a ``null`` ``put_minus_call`` → no vote.
    """
    if not isinstance(iv_skew, dict):
        return 0
    pmc = iv_skew.get("put_minus_call")
    if not _is_number(pmc):
        return 0
    threshold = config.iv_skew_threshold
    if pmc > threshold:
        return -1
    if pmc < -threshold:
        return 1
    return 0


def _futures_basis_vote(futures_basis: Any, config: OptionsBiasConfig) -> int:
    """Futures-basis vote against the configured threshold.

    ``futures_basis > futures_basis_threshold`` (futures premium) → bullish; ``<
    -futures_basis_threshold`` (futures discount) → bearish; within the band, or
    ``null``, → no vote.
    """
    if not _is_number(futures_basis):
        return 0
    threshold = config.futures_basis_threshold
    if futures_basis > threshold:
        return 1
    if futures_basis < -threshold:
        return -1
    return 0


# ── Alignment (total function of bias-state × proposed-direction) ─────────────


def derive_alignment(
    options_bias_state: Any,
    proposed_direction: Any,
) -> str:
    """Map (Options_Bias_State × proposed direction) to exactly one Alignment.

    A total function over the design's alignment table (Requirement 1.2):

      * ``bullish`` bias  → ``aligned`` with ``BUY``,  ``misaligned`` with ``SELL``
      * ``bearish`` bias  → ``misaligned`` with ``BUY``, ``aligned`` with ``SELL``
      * ``neutral`` bias  → always ``neutral``

    An absent / ``HOLD`` / unrecognized direction (and any unrecognized bias
    state) collapses to ``neutral``. The proposed direction is matched
    case-insensitively and whitespace-tolerantly. Never raises.
    """
    direction = (
        proposed_direction.strip().upper()
        if isinstance(proposed_direction, str)
        else ""
    )
    if options_bias_state == "bullish":
        if direction == "BUY":
            return "aligned"
        if direction == "SELL":
            return "misaligned"
        return "neutral"
    if options_bias_state == "bearish":
        if direction == "BUY":
            return "misaligned"
        if direction == "SELL":
            return "aligned"
        return "neutral"
    return "neutral"


# ── Driving-signal echo (verbatim copies of the values that produced the bias) ─


def _max_pain_vs_spot(max_pain: Any, spot: Any) -> Optional[str]:
    """Position of max pain relative to spot: ``"above"`` / ``"below"`` / ``"at"``.

    ``None`` when either value is ``null`` / non-finite — never inferred. This is
    a *position* descriptor computed from the copied values, not a fabricated
    signal value.
    """
    if not (_is_number(max_pain) and _is_number(spot)):
        return None
    if max_pain > spot:
        return "above"
    if max_pain < spot:
        return "below"
    return "at"


def _build_signals(analytics: dict) -> dict:
    """Echo the driving signals verbatim from the analytics (Requirement 1.3).

    Copies (deep-copies the nested OI-buildup / OI-wall objects so the returned
    label never shares a reference with — nor can mutate — the analytics input)
    the PCR value, the aggregate OI buildup, the nearest OI walls, the max-pain
    position relative to spot, the IV skew, and the futures basis. Values are
    taken straight from the analytics result and are never inferred.
    """
    oi_buildup = analytics.get("oi_buildup")
    oi_walls = analytics.get("oi_walls")
    iv_skew = analytics.get("iv_skew")
    max_pain = analytics.get("max_pain")
    spot = analytics.get("spot")
    return {
        "pcr_oi": analytics.get("pcr_oi"),
        "oi_buildup": copy.deepcopy(oi_buildup) if oi_buildup is not None else None,
        "max_pain": max_pain,
        "spot": spot,
        "max_pain_vs_spot": _max_pain_vs_spot(max_pain, spot),
        "oi_walls": copy.deepcopy(oi_walls) if oi_walls is not None else None,
        "iv_skew_put_minus_call": (
            iv_skew.get("put_minus_call") if isinstance(iv_skew, dict) else None
        ),
        "futures_basis": analytics.get("futures_basis"),
    }


def _neutral_label(analytics: Any, proposed_direction: Any) -> dict:
    """A neutral Options_Bias_Label, echoing whatever driving signals are present.

    Used for the degenerate / malformed-input path so the classifier is total: a
    non-dict (or otherwise unusable) analytics input degrades to a ``neutral``
    bias rather than raising. The ``signals`` object echoes the available signals
    when ``analytics`` is a dict, and is all-``null`` otherwise.
    """
    if isinstance(analytics, dict):
        signals = _build_signals(analytics)
    else:
        signals = {
            "pcr_oi": None,
            "oi_buildup": None,
            "max_pain": None,
            "spot": None,
            "max_pain_vs_spot": None,
            "oi_walls": None,
            "iv_skew_put_minus_call": None,
            "futures_basis": None,
        }
    return {
        "options_bias_state": "neutral",
        "alignment": derive_alignment("neutral", proposed_direction),
        "signals": signals,
    }


# ── Public entry point ────────────────────────────────────────────────────────


def classify_options_bias(
    analytics: dict,
    config: OptionsBiasConfig,
    proposed_direction: Optional[str] = None,
) -> dict:
    """Classify an Options_Analytics_Result into an ``Options_Bias_Label``.

    Nets the per-signal directional votes — PCR, aggregate OI buildup, max-pain
    position, the nearest OI walls, IV skew, and futures basis — into an
    ``options_bias_state``:

      * ``bullish``  when the bullish votes strictly exceed the bearish votes AND
        the count of contributing (directional) signals is ``>=
        MIN_SIGNALS_FOR_BIAS``;
      * ``bearish``  for the strict reverse;
      * ``neutral``  otherwise — including when fewer than ``MIN_SIGNALS_FOR_BIAS``
        signals cast a directional vote (too few signals to form a bias,
        Requirement 1.5).

    A ``null`` / non-finite / structurally-absent signal casts no vote and is
    excluded from the count (Requirement 1.4). The returned label carries a
    ``signals`` object echoing the driving signals verbatim (Requirement 1.3) and
    an ``alignment`` derived from the bias state and ``proposed_direction``
    (Requirement 1.2).

    Scope (Requirement 10.1): the result is **only** a label —
    ``options_bias_state`` / ``alignment`` / ``signals`` — never a BUY/SELL/HOLD
    action, recommendation, conviction, or score.

    Purity (Requirement 1.6): pure, deterministic, and total. It copies — never
    mutates — its analytics input, and any malformed analytics dict (or config)
    degrades to a ``neutral`` label rather than raising.
    """
    try:
        if not isinstance(analytics, dict):
            return _neutral_label(analytics, proposed_direction)

        # A malformed / missing config degrades to the documented defaults rather
        # than raising (keeps the classifier total).
        if not isinstance(config, OptionsBiasConfig):
            config = resolve_options_bias_config()

        votes = (
            _pcr_vote(analytics.get("pcr_oi"), config),
            _oi_buildup_vote(analytics.get("oi_buildup")),
            _max_pain_vote(analytics.get("max_pain"), analytics.get("spot"), config),
            _oi_walls_vote(analytics.get("oi_walls"), analytics.get("spot"), config),
            _iv_skew_vote(analytics.get("iv_skew"), config),
            _futures_basis_vote(analytics.get("futures_basis"), config),
        )

        bullish_votes = sum(1 for v in votes if v > 0)
        bearish_votes = sum(1 for v in votes if v < 0)
        contributing = bullish_votes + bearish_votes

        if contributing >= MIN_SIGNALS_FOR_BIAS and bullish_votes > bearish_votes:
            state = "bullish"
        elif contributing >= MIN_SIGNALS_FOR_BIAS and bearish_votes > bullish_votes:
            state = "bearish"
        else:
            state = "neutral"

        return {
            "options_bias_state": state,
            "alignment": derive_alignment(state, proposed_direction),
            "signals": _build_signals(analytics),
        }
    except Exception:  # noqa: BLE001 — totality guarantee (Requirement 1.6)
        return _neutral_label(analytics, proposed_direction)
