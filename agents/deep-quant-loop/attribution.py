"""Feature_Attribution — pure-Python attribution & pruning over the Trade_Journal.

The Deep Quant agent now carries roughly a dozen analytical dimensions (``dir``,
``macro``, ``pred``, ``va``, ``regime``, ``rs``, ``fc``, ``tm``, ``sess``,
``db``, ``opt``, …), each recorded as a tag in the journal's ``setup_key``
fingerprint alongside the realized outcome of every committed trade. More inputs
is not the same as more edge: past a point, extra dimensions add noise rather
than expectancy. This module reads those rows and answers the only question that
matters — *which dimensions actually predict outcomes, and which add noise?*

It computes, per Fingerprint_Dimension and per Dimension_Value, the realized
win-rate and expectancy with sample sizes; derives a Contribution_Metric per
dimension (how strongly its values separate realized expectancy); ranks the
dimensions; and produces a per-dimension keep / down_weight / insufficient_sample
Recommendation in a structured Attribution_Report. It is decision support with a
human in the loop: it reads the journal READ-ONLY, never deletes a dimension,
and never recommends pruning on a sample too small to be meaningful. An optional,
opt-in Weight_Map can feed the recommendations back into the agent's conviction,
but only when an explicit flag is set; disabled (the default) the pass has zero
effect on the running agent.

The module deliberately mirrors the conventions already established across the
deep-quant-loop: a pure numeric core over in-memory rows (like
``calibration.conviction_calibration``), a thin defensive read-only I/O entry
point, config-from-env with documented defaults via the ``_resolve_int`` /
``_resolve_float`` helper convention (``regime.py`` / ``rs.py`` / ``forecaster``
/ ``options``), and an ``argparse`` CLI mirroring ``backtest.py``.

This file (task 1.1) provides the configuration foundation: the documented
default constants, the frozen ``AttributionConfig`` dataclass, and
``resolve_attribution_config()``. The parsing, statistics, Contribution_Metric,
ranking, report orchestration, Weight_Map, I/O layer, and CLI are added in
subsequent tasks.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Optional

# ── Documented default configuration ──────────────────────────────────────────
# Applied whenever a parameter's env var is unset / empty / whitespace /
# unparseable / out of range (Requirement 7.2). Single source of truth for the
# defaults on both the CLI path and the agent opt-in path.

DEFAULT_MIN_SAMPLE_DIMENSION = 30       # min scored trades for a DIMENSION to be rated
DEFAULT_MIN_SAMPLE_VALUE = 10           # below this, a VALUE's stats are a weak prior
DEFAULT_CONTRIBUTION_THRESHOLD = 0.15   # contribution >= this => keep, below => down_weight
DEFAULT_GLOBAL_MIN_SCORED = 50          # below this total scored, report is a weak prior
DEFAULT_DOWN_WEIGHT_FACTOR = 0.5        # conviction weight for a down_weight dimension
DEFAULT_WEIGHT_MAP_ENABLED = False      # opt-in: agent may consult the Weight_Map

# ── Environment variable names ────────────────────────────────────────────────
ENV_MIN_SAMPLE_DIMENSION = "ATTRIBUTION_MIN_SAMPLE_DIMENSION"
ENV_MIN_SAMPLE_VALUE = "ATTRIBUTION_MIN_SAMPLE_VALUE"
ENV_CONTRIBUTION_THRESHOLD = "ATTRIBUTION_CONTRIBUTION_THRESHOLD"
ENV_GLOBAL_MIN_SCORED = "ATTRIBUTION_GLOBAL_MIN_SCORED"
ENV_DOWN_WEIGHT_FACTOR = "ATTRIBUTION_DOWN_WEIGHT_FACTOR"
ENV_WEIGHT_MAP_ENABLED = "ATTRIBUTION_WEIGHT_MAP_ENABLED"

# ── Valid ranges ──────────────────────────────────────────────────────────────
# Sample sizes / counts are integers >= 1 with no upper bound; the contribution
# threshold is a non-negative R-multiple; the down-weight factor lies in the
# half-open interval (0.0, 1.0] (Requirement 7.1).
_SAMPLE_MIN = 1
_THRESHOLD_MIN = 0.0
_FACTOR_LOW = 0.0       # EXCLUSIVE lower bound (see resolve_attribution_config)
_FACTOR_HIGH = 1.0      # inclusive upper bound

# Truthy / falsy spellings accepted for the opt-in boolean flag. Anything else
# (including empty / whitespace / garbage) degrades to the documented default.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class AttributionConfig:
    """The resolved, validated configuration used to build an Attribution_Report.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the pure core's purity guarantee, Requirement 8.3). For
    identical environment-variable values the resolved configuration is identical
    on repeated runs (Requirement 7.3).
    """

    min_sample_dimension: int       # min scored trades for a DIMENSION to be rated
    min_sample_value: int           # below this, a VALUE's stats are a weak prior
    contribution_threshold: float   # contribution >= this => keep, below => down_weight
    global_min_scored: int          # below this total scored, report is a weak prior
    down_weight_factor: float       # conviction weight for a down_weight dimension, in (0,1]
    weight_map_enabled: bool        # opt-in: agent may consult the Weight_Map


def _resolve_float(env_name: str, default: float, low: float, high: float) -> float:
    """Resolve one float parameter from its own env var (Requirement 7.1-7.2).

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as a float, is non-finite (NaN/inf), or parses but falls outside the
    inclusive band ``[low, high]``. Never raises. Mirrors the ``_resolve_float``
    convention in ``regime.py`` / ``rs.py`` / ``order_flow.py``.
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
    """Resolve one integer parameter from its own env var (Requirement 7.1-7.2).

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as an int, or parses but is below ``low`` (the minimum valid value).
    Never raises. Mirrors the ``_resolve_int`` convention in ``regime.py`` /
    ``rs.py`` / ``order_flow.py``.
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


def _resolve_bool(env_name: str, default: bool) -> bool:
    """Resolve one boolean opt-in flag from its own env var (Requirement 7.1-7.2).

    Falls back to ``default`` when the var is unset/empty/whitespace or carries a
    token outside the recognized truthy/falsy spellings. Never raises. Parsing is
    case-insensitive and whitespace-tolerant.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return default


def resolve_attribution_config() -> AttributionConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 7):
      * unset / empty / whitespace  -> documented default
      * unparseable as its type     -> documented default (never raises)
      * parses but out of range     -> documented default (never raises)

    ``down_weight_factor`` has an EXCLUSIVE lower bound of 0: a zero conviction
    weight is not a usable parameter. ``_resolve_float`` only enforces an
    inclusive band, so it is resolved with an inclusive low of 0.0 and the
    boundary value 0.0 (the only way a non-negative value can sit at the
    exclusive bound) is reverted to the documented default — clamping the result
    to ``(0.0, 1.0]`` (mirrors the ``trade_manager`` exclusive-bound convention).

    Identical environments resolve to identical configuration (Requirement 7.3).
    This function NEVER raises.
    """
    min_sample_dimension = _resolve_int(
        ENV_MIN_SAMPLE_DIMENSION, DEFAULT_MIN_SAMPLE_DIMENSION, _SAMPLE_MIN
    )
    min_sample_value = _resolve_int(
        ENV_MIN_SAMPLE_VALUE, DEFAULT_MIN_SAMPLE_VALUE, _SAMPLE_MIN
    )
    global_min_scored = _resolve_int(
        ENV_GLOBAL_MIN_SCORED, DEFAULT_GLOBAL_MIN_SCORED, _SAMPLE_MIN
    )

    contribution_threshold = _resolve_float(
        ENV_CONTRIBUTION_THRESHOLD,
        DEFAULT_CONTRIBUTION_THRESHOLD,
        _THRESHOLD_MIN,
        math.inf,
    )

    down_weight_factor = _resolve_float(
        ENV_DOWN_WEIGHT_FACTOR,
        DEFAULT_DOWN_WEIGHT_FACTOR,
        _FACTOR_LOW,
        _FACTOR_HIGH,
    )
    # Clamp to the half-open interval (0.0, 1.0]: a resolved 0.0 sits on the
    # exclusive lower bound and is not usable, so revert it to the default.
    if down_weight_factor <= _FACTOR_LOW:
        down_weight_factor = DEFAULT_DOWN_WEIGHT_FACTOR

    weight_map_enabled = _resolve_bool(
        ENV_WEIGHT_MAP_ENABLED, DEFAULT_WEIGHT_MAP_ENABLED
    )

    return AttributionConfig(
        min_sample_dimension=min_sample_dimension,
        min_sample_value=min_sample_value,
        contribution_threshold=contribution_threshold,
        global_min_scored=global_min_scored,
        down_weight_factor=down_weight_factor,
        weight_map_enabled=weight_map_enabled,
    )


# ── Pure core: fingerprint parsing & scored-trade filtering ───────────────────
# These functions are TOTAL and DETERMINISTIC over arbitrary inputs: they never
# raise, never mutate their argument, and hold no ambient state, so identical
# inputs always yield identical outputs. They are the first stage of the pure
# core that the statistics / Contribution_Metric / report orchestration build on.

# The literal sentinel value a Dimension_Value collapses to when the fingerprint
# carries no usable value (absent ``:``, empty value, or an explicit ``unknown``)
# or when a token's dimension is itself empty. The dimension is RETAINED (not
# dropped) so the row still contributes to that dimension under the catch-all
# value, mirroring how ``journal.derive_setup_tags`` emits ``<dim>:unknown``.
UNKNOWN_VALUE = "unknown"


def _is_num(v) -> bool:
    """True for a finite real number (``bool`` excluded), mirroring journal._is_num.

    ``bool`` is a subclass of ``int`` in Python, but a True/False is never a valid
    R-multiple, so it is rejected. NaN and ±inf are rejected as not usable.
    """
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_setup_key(setup_key) -> dict:
    """Parse a ``setup_key`` fingerprint into a ``{dimension: value}`` mapping.

    The journal records a low-cardinality fingerprint as a ``|``-joined string of
    ``<dimension>:<value>`` tokens (for example
    ``dir:BUY|regime:trend-favorable|rs:leader-aligned``). This splits on ``|``
    and then each token on its FIRST ``:`` into ``dimension`` and ``value``.

    Tolerance rules (Requirements 1.3, 5.4) — the parse is TOTAL over arbitrary
    strings and never raises:

      * A token with **no** ``:`` (for example ``regime``) keeps the token as the
        dimension and maps its value to the literal ``"unknown"``.
      * A token whose value is empty (``regime:``) or the explicit string
        ``unknown`` (case-insensitive) maps that dimension's value to the literal
        ``"unknown"`` — the dimension is RETAINED, not dropped.
      * A token with an **empty dimension** (``:trend`` or ``:``) is degenerate;
        its value collapses to the literal ``"unknown"`` so the malformed token
        still resolves deterministically rather than being silently lost.
      * A wholly empty token (from ``a||b`` or a trailing ``|``) contributes
        nothing.
      * A wholly empty / whitespace / non-string ``setup_key`` yields ``{}`` so
        the row contributes to NO dimension.

    Splitting on the first ``:`` only means a value may itself contain ``:``
    (for example ``fc:aligned:strong`` -> ``{"fc": "aligned:strong"}``). When the
    same dimension appears more than once in a key, the LAST occurrence wins
    (deterministic given a fixed input string).
    """
    result: dict = {}
    if not isinstance(setup_key, str) or not setup_key.strip():
        return result
    for token in setup_key.split("|"):
        token = token.strip()
        if not token:
            # Wholly empty token (e.g. from "a||b" or a trailing "|"): contributes
            # to no dimension.
            continue
        dimension, sep, value = token.partition(":")
        dimension = dimension.strip()
        value = value.strip()
        if not sep:
            # No ':' in the token — the whole token is the dimension name and the
            # value is absent, so it collapses to the literal "unknown".
            value = UNKNOWN_VALUE
        if not dimension:
            # Empty dimension (":trend" / ":") — degenerate token; collapse its
            # value to the literal "unknown" but retain the (empty-keyed) entry
            # rather than dropping it.
            value = UNKNOWN_VALUE
        if not value or value.lower() == UNKNOWN_VALUE:
            # Empty or explicit-unknown value collapses to the literal "unknown".
            value = UNKNOWN_VALUE
        result[dimension] = value
    return result


def is_scored_trade(row) -> bool:
    """True iff ``row`` is a Scored_Trade usable for attribution statistics.

    A Scored_Trade has a RESOLVED win/loss outcome (``status`` in
    ``{"win", "loss"}``, case-insensitive) AND a usable ``r_multiple`` (a finite
    real number). Open / expired / hold rows, and rows whose ``r_multiple`` is
    missing, non-numeric, or non-finite, are excluded from win-rate and
    expectancy (Requirement 1.2). TOTAL: never raises — a malformed or non-dict
    row simply returns ``False``.
    """
    if not isinstance(row, dict):
        return False
    try:
        status = str(row.get("status") or "").strip().lower()
        if status not in ("win", "loss"):
            return False
        return _is_num(row.get("r_multiple"))
    except Exception:
        return False


# ── Pure core: per-dimension, per-value statistics ────────────────────────────
# ``compute_dimension_stats`` aggregates the realized outcomes of Scored_Trades
# into per-Fingerprint_Dimension, per-Dimension_Value ``Dimension_Stats``. It is
# PURE and TOTAL: it reads only the rows it is handed, never mutates them, holds
# no ambient state, and never raises on arbitrary in-memory input.

# The source token marking a seeded/backtest trade. Any other source (including
# the live "live", a missing/``None`` source, or an empty string) is treated as
# a live trade for the seeded-vs-live split (Requirement 5.5).
BACKTEST_SOURCE = "backtest"


def _is_backtest_source(row) -> bool:
    """True iff a row's ``source`` marks it as a seeded/backtest trade.

    ``source == 'backtest'`` (case-insensitive, whitespace-tolerant) is seeded;
    everything else — ``'live'``, ``None``, ``''``, or any other value — counts as
    live (Requirement 5.5). Never raises.
    """
    try:
        return str(row.get("source") or "").strip().lower() == BACKTEST_SOURCE
    except Exception:
        return False


def compute_dimension_stats(rows, config: AttributionConfig) -> dict:
    """Aggregate Scored_Trades into per-dimension, per-value ``Dimension_Stats``.

    PURE and TOTAL over arbitrary in-memory rows (Requirements 1.1, 1.2, 1.4,
    5.2, 5.5, 8.2, 8.3): reads only the supplied ``rows``, never mutates them,
    holds no ambient state, and never raises.

    Only Scored_Trades contribute (``is_scored_trade`` — a resolved win/loss
    outcome with a usable finite ``r_multiple``); open / expired / hold rows and
    rows with a missing or non-finite ``r_multiple`` are excluded from every
    count, win-rate, and expectancy (Requirement 1.2). Each scored row's
    ``setup_key`` is parsed via ``parse_setup_key`` and contributes one
    observation to every ``{dimension: value}`` it carries; a row with an empty /
    malformed ``setup_key`` parses to ``{}`` and contributes to no dimension
    (Requirement 5.4).

    Returns a mapping ``{dimension: {value: Dimension_Stats}}`` where each
    ``Dimension_Stats`` is::

        {
            "value":          <str>,    # the Dimension_Value
            "count":          <int>,    # scored trades for this value
            "wins":           <int>,
            "losses":         <int>,
            "win_rate":       <float|None>,  # wins/(wins+losses) in [0,1]; None when count 0
            "expectancy_r":   <float|None>,  # mean r_multiple; None when count 0
            "weak_prior":     <bool>,        # True when count < min_sample_value (R5.2)
            "backtest_count": <int>,         # of count, how many are source='backtest'
            "live_count":     <int>,         # of count, how many are NOT source='backtest'
        }

    By construction ``count == wins + losses == backtest_count + live_count`` for
    every value (Requirements 1.1, 5.5). ``win_rate`` lies in ``[0.0, 1.0]`` and
    is ``None`` only for a degenerate zero-count value (never produced here, but
    documented for the consumers); ``expectancy_r`` is the mean R-multiple
    (Requirement 1.4). Both statistics are rounded to 4 decimals, mirroring the
    ``calibration`` convention.
    """
    stats: dict = {}
    if not isinstance(rows, list):
        return stats

    # ── Accumulate raw tallies per dimension/value over scored rows only ──────
    # acc[dimension][value] = {"wins", "losses", "r_sum", "backtest", "live"}
    acc: dict = {}
    for row in rows:
        if not is_scored_trade(row):
            continue
        try:
            status = str(row.get("status") or "").strip().lower()
            r_multiple = float(row.get("r_multiple"))
        except Exception:
            # Defensive: is_scored_trade already vetted these, but stay total.
            continue
        is_backtest = _is_backtest_source(row)
        parsed = parse_setup_key(row.get("setup_key"))
        for dimension, value in parsed.items():
            dim_acc = acc.setdefault(dimension, {})
            bucket = dim_acc.setdefault(
                value,
                {"wins": 0, "losses": 0, "r_sum": 0.0, "backtest": 0, "live": 0},
            )
            if status == "win":
                bucket["wins"] += 1
            else:  # is_scored_trade guarantees status is "win" or "loss"
                bucket["losses"] += 1
            bucket["r_sum"] += r_multiple
            if is_backtest:
                bucket["backtest"] += 1
            else:
                bucket["live"] += 1

    # ── Materialize the Dimension_Stats from the raw tallies ──────────────────
    for dimension, dim_acc in acc.items():
        value_stats: dict = {}
        for value, bucket in dim_acc.items():
            wins = bucket["wins"]
            losses = bucket["losses"]
            count = wins + losses
            win_rate = round(wins / count, 4) if count else None
            expectancy_r = round(bucket["r_sum"] / count, 4) if count else None
            value_stats[value] = {
                "value": value,
                "count": count,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "expectancy_r": expectancy_r,
                "weak_prior": count < config.min_sample_value,
                "backtest_count": bucket["backtest"],
                "live_count": bucket["live"],
            }
        stats[dimension] = value_stats

    return stats


# ── Pure core: the Contribution_Metric ────────────────────────────────────────
# ``compute_contribution`` collapses one dimension's per-value Dimension_Stats
# into a single scalar that measures HOW STRONGLY the dimension's values separate
# realized expectancy. It is PURE and TOTAL: it reads only the supplied stats,
# never mutates them, holds no ambient state, and never raises or divides by zero.


def compute_contribution(value_stats: dict, config: AttributionConfig):
    """Sample-weighted dispersion of per-value expectancy for one dimension.

    Implements the Contribution_Metric of design AD-4: the sample-weighted
    standard deviation of each value's expectancy (mean R-multiple) about the
    dimension's sample-weighted mean expectancy.

        μ_d        = Σ_v (n_v · meanR_v) / Σ_v n_v
        contrib_d  = sqrt( Σ_v n_v · (meanR_v − μ_d)² / Σ_v n_v )

    where ``n_v`` is value ``v``'s scored-trade ``count`` and ``meanR_v`` its
    ``expectancy_r`` (Requirement 2.1). The metric is exactly ``0.0`` when every
    value shares one expectancy and grows without bound as the values'
    expectancies diverge (Requirement 2.2). It uses only stdlib arithmetic — no
    ``numpy`` — matching the rest of the codebase.

    Arguments:
        value_stats: the inner ``{value: Dimension_Stats}`` mapping for ONE
            dimension, exactly as produced by ``compute_dimension_stats`` (each
            ``Dimension_Stats`` carries ``count`` and ``expectancy_r``).
        config: the resolved ``AttributionConfig``; only ``min_sample_dimension``
            is consulted here (the dimension-level meaningfulness gate).

    Returns:
        A non-negative ``float`` contribution, or ``None`` when the metric is
        **not meaningful** (Requirements 2.3, 2.4).

    Not-meaningful (returns ``None``) — reconciling design Property 7 with AD-4:
        Property 7 states that any dimension whose TOTAL scored-trade count is
        below ``min_sample_dimension`` — which it lists single-value, all-null,
        and zero-sample dimensions as instances of — is reported as
        not-meaningful. AD-4 separately states that a single value yields spread
        ``0`` while an empty / all-null dimension yields ``None``. These reconcile
        cleanly: the dimension-level SAMPLE gate is the authority on
        meaningfulness, so this function returns ``None`` when

          * ``value_stats`` is empty / not a usable mapping (zero-sample), or
          * no value carries a usable (finite, non-``None``) ``expectancy_r``
            with a positive ``count`` (all-null) — Σ n_v would be 0, so this also
            guards the division by zero, or
          * the usable total Σ n_v is below ``config.min_sample_dimension``
            (insufficient sample, which is exactly where a lone single value
            lands in practice).

        Only once a dimension clears that sample gate with usable values is the
        dispersion computed — and there a single surviving value (all mass on one
        expectancy) correctly yields ``0.0`` per AD-4, not ``None``, because its
        deviation about its own mean is zero. So "single value ⇒ 0" (AD-4) and
        "below-sample/single-value ⇒ None" (Property 7) are not in conflict: the
        sample gate decides, and a value sharing all the mass disperses to 0.

    TOTAL: never raises and never divides by zero (Σ n_v == 0 short-circuits to
    ``None`` before any division). PURE: does not mutate ``value_stats`` or
    ``config`` and holds no ambient state, so identical inputs always yield an
    identical result (Requirements 2.3, 8.2, 8.3).
    """
    if not isinstance(value_stats, dict) or not value_stats:
        # Zero-sample / degenerate dimension — nothing to disperse.
        return None

    # ── Collect (n_v, meanR_v) for values with a usable expectancy ────────────
    # A value contributes only when it has a positive scored count AND a finite,
    # non-None mean R-multiple; anything else (count 0, expectancy None/NaN/inf)
    # is skipped so the metric never sees an unusable term.
    weighted: list = []  # [(n_v, meanR_v), ...]
    total_n = 0
    for stats in value_stats.values():
        if not isinstance(stats, dict):
            continue
        count = stats.get("count")
        mean_r = stats.get("expectancy_r")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            continue
        if not _is_num(mean_r):
            continue
        weighted.append((count, float(mean_r)))
        total_n += count

    # All-null / zero-sample dimension: Σ n_v == 0. Returning None here also makes
    # the division below structurally impossible (never divide by zero, R2.3).
    if total_n <= 0:
        return None

    # Dimension-level sample gate (design Property 7 / Requirement 2.4): a usable
    # total below min_sample_dimension is reported as not-meaningful rather than a
    # spurious dispersion. A lone single value lands here in practice.
    if total_n < config.min_sample_dimension:
        return None

    # ── Sample-weighted mean expectancy μ_d, then weighted dispersion ─────────
    mu = sum(n * mean_r for n, mean_r in weighted) / total_n
    variance = sum(n * (mean_r - mu) ** 2 for n, mean_r in weighted) / total_n

    # Guard against a tiny negative from floating-point round-off so sqrt is safe;
    # mathematically variance >= 0, and a single value gives exactly 0.0 (AD-4).
    if variance < 0.0:
        variance = 0.0

    return round(math.sqrt(variance), 4)


# ── Pure core: ranking & per-dimension Recommendation ─────────────────────────
# ``rank_and_recommend`` collapses the ``{dimension: {value: Dimension_Stats}}``
# mapping into a ranked list of Dimension_Report entries, each carrying the
# dimension's per-value stats, its scored totals + seeded/live split, its
# Contribution_Metric (and meaningfulness flag), a 1-based rank, and exactly one
# Recommendation. It is PURE and TOTAL: it reads only the supplied stats + config,
# never mutates them, holds no ambient state, never raises, and never deletes or
# disables a dimension (design AD-2 / AD-3, Requirements 3.1-3.6, 5.1).

# The three (and only three) Recommendation labels. A dimension is assigned
# EXACTLY one of these (Requirement 3.2).
RECOMMENDATION_KEEP = "keep"
RECOMMENDATION_DOWN_WEIGHT = "down_weight"
RECOMMENDATION_INSUFFICIENT_SAMPLE = "insufficient_sample"


def _safe_int(value) -> int:
    """Return ``value`` when it is a real (non-bool) int, else 0. Never raises.

    Used to total per-value counts defensively so a malformed ``Dimension_Stats``
    (missing/non-int ``count``) contributes 0 rather than crashing the rank pass.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def rank_and_recommend(dimension_stats: dict, config: AttributionConfig) -> list[dict]:
    """Rank dimensions by contribution and assign one Recommendation each.

    Consumes the ``{dimension: {value: Dimension_Stats}}`` mapping produced by
    ``compute_dimension_stats`` and returns a ranked ``list`` of Dimension_Report
    entries (design "Data Models"). Each entry carries::

        {
            "dimension":               <str>,
            "values":                  [ <Dimension_Stats>, ... ],  # per-value (R4.1)
            "total_scored":            <int>,    # Σ count over the dimension's values
            "backtest_scored":         <int>,    # Σ backtest_count
            "live_scored":             <int>,    # Σ live_count
            "contribution":            <float|None>,   # None when not meaningful (R2.4)
            "contribution_meaningful": <bool>,
            "rank":                    <int>,    # 1-based, by contribution desc (R3.1)
            "recommendation":          "keep" | "down_weight" | "insufficient_sample",
        }

    Recommendation control flow (design AD-3, "statistical honesty enforced
    structurally") — evaluated in this exact order so ``insufficient_sample`` is
    the ONLY reachable outcome below the dimension sample gate:

      1. ``total_scored < config.min_sample_dimension`` -> ``insufficient_sample``
         — the only reachable branch in that case; ``down_weight`` / ``keep`` are
         structurally unreachable here, so a dimension is NEVER pruned on a sample
         too small to be meaningful (Requirements 3.3, 5.1).
      2. else ``contribution is None`` (not meaningful) -> ``insufficient_sample``
         — defensive: a dimension that clears the sample count but whose
         contribution is still not meaningful is reported honestly rather than
         down-weighted.
      3. else ``contribution < config.contribution_threshold`` -> ``down_weight``
         (Requirement 3.4).
      4. else (``contribution >= config.contribution_threshold``) -> ``keep``
         (Requirement 3.5).

    Ranking (Requirement 3.1): entries are ordered by contribution DESCENDING,
    with not-meaningful (``None``) contributions ordered LAST, and ties broken
    DETERMINISTICALLY by ascending dimension name. ``rank`` is the 1-based
    position in that order; because dimension names are unique keys the ranks are
    a contiguous ``1..N`` with no duplicates.

    PURE and TOTAL (Requirements 2.3, 3.6, 8.2, 8.3): never raises, never mutates
    ``dimension_stats`` / its nested stats / ``config``, holds no ambient state,
    and never deletes or disables a dimension (the Recommendation is advisory
    data; applying it is a human action — AD-2). The per-value ``Dimension_Stats``
    are referenced as-is (never copied-and-mutated) in a freshly built ``values``
    list, sorted by value name for a stable, deterministic ordering.
    """
    if not isinstance(dimension_stats, dict) or not dimension_stats:
        return []

    reports: list = []
    for dimension, value_stats in dimension_stats.items():
        # ── Per-value list + scored totals / seeded-vs-live split ─────────────
        values_list: list = []
        total_scored = 0
        backtest_scored = 0
        live_scored = 0
        if isinstance(value_stats, dict):
            # Sort by value name for a deterministic per-value ordering
            # independent of the insertion order of the source mapping.
            for value in sorted(value_stats.keys(), key=str):
                stats = value_stats[value]
                values_list.append(stats)
                if isinstance(stats, dict):
                    total_scored += _safe_int(stats.get("count"))
                    backtest_scored += _safe_int(stats.get("backtest_count"))
                    live_scored += _safe_int(stats.get("live_count"))

        # ── Contribution_Metric (None == not meaningful) ─────────────────────
        contribution = compute_contribution(value_stats, config)
        contribution_meaningful = contribution is not None

        # ── Recommendation control flow (order matters — see docstring) ──────
        if total_scored < config.min_sample_dimension:
            # The ONLY reachable branch below the dimension sample gate: never
            # down_weight / keep on a sample too small to be meaningful.
            recommendation = RECOMMENDATION_INSUFFICIENT_SAMPLE
        elif contribution is None:
            recommendation = RECOMMENDATION_INSUFFICIENT_SAMPLE
        elif contribution < config.contribution_threshold:
            recommendation = RECOMMENDATION_DOWN_WEIGHT
        else:
            recommendation = RECOMMENDATION_KEEP

        reports.append(
            {
                "dimension": dimension,
                "values": values_list,
                "total_scored": total_scored,
                "backtest_scored": backtest_scored,
                "live_scored": live_scored,
                "contribution": contribution,
                "contribution_meaningful": contribution_meaningful,
                "rank": None,  # assigned after the deterministic sort below
                "recommendation": recommendation,
            }
        )

    # ── Rank: contribution desc, not-meaningful last, tiebreak by name ────────
    # Sort key tuple: (not_meaningful_flag, -contribution, dimension_name).
    #   * not_meaningful_flag 0 sorts before 1 -> None contributions land last.
    #   * -contribution sorts meaningful entries by descending contribution.
    #   * dimension name (string) is the deterministic tiebreak.
    reports.sort(
        key=lambda e: (
            0 if e["contribution"] is not None else 1,
            -e["contribution"] if e["contribution"] is not None else 0.0,
            str(e["dimension"]),
        )
    )
    for position, entry in enumerate(reports, start=1):
        entry["rank"] = position

    return reports


# ── Pure core: top-level Attribution_Report orchestration ─────────────────────
# ``build_attribution_report`` wires the pure stages together —
# ``parse_setup_key`` -> ``compute_dimension_stats`` -> ``compute_contribution``
# (inside ``rank_and_recommend``) -> ``rank_and_recommend`` — into the single
# structured Attribution_Report the CLI / I/O layer / agent opt-in consume. Like
# every other stage it is PURE and TOTAL: it reads only the supplied rows +
# config, never mutates them, holds no ambient state, and never raises on
# arbitrary in-memory input.


def build_attribution_report(rows: list[dict], config: AttributionConfig) -> dict:
    """Assemble the top-level Attribution_Report from in-memory journal rows.

    Wires the pure pipeline end to end (design "Data Models" / Requirement 4.1):

        parse_setup_key  ->  compute_dimension_stats  ->  compute_contribution
                                                      ->  rank_and_recommend

    and wraps the ranked per-dimension Dimension_Report list with report-level
    totals, the seeded-vs-live split, the resolved ``config`` echo, and the two
    sufficiency flags. Returns::

        {
            "dimensions":        [ <Dimension_Report>, ... ],  # ranked, contribution desc
            "total_scored":      <int>,    # number of Scored_Trades overall (R4.x)
            "backtest_scored":   <int>,    # of those, source == 'backtest' (R5.5)
            "live_scored":       <int>,    # of those, every other source (R5.5)
            "config": {                                        # so a reader can judge strength (R4.3)
                "min_sample_dimension":   <int>,
                "min_sample_value":       <int>,
                "contribution_threshold": <float>,
                "global_min_scored":      <int>,
                "down_weight_factor":     <float>,
            },
            "weak_prior":        <bool>,   # total_scored < global_min_scored (R4.4)
            "insufficient_data": <bool>,   # zero Scored_Trades (R5.3)
        }

    Report-level ``total_scored`` semantics — IMPORTANT: it is the number of
    Scored_Trades OVERALL (rows for which ``is_scored_trade`` is True), counted
    ONCE per row. This is distinct from a per-dimension ``total_scored`` (which
    sums per-value counts and so counts a single multi-dimension row once per
    dimension it carries). ``backtest_scored`` / ``live_scored`` are the
    seeded-vs-live split of those same Scored_Trades (any source other than
    ``'backtest'`` — including ``'live'``, ``None``, or ``''`` — counts as live,
    Requirement 5.5), so ``backtest_scored + live_scored == total_scored``.

    Sufficiency flags:
      * ``insufficient_data`` is True exactly when there are zero Scored_Trades;
        in that case ``dimensions`` is the empty list ``[]`` and ``total_scored``
        is ``0`` (Requirement 5.3). A row set that is non-empty but carries no
        resolved win/loss outcomes still yields ``insufficient_data == True``.
      * ``weak_prior`` is True when ``total_scored < config.global_min_scored`` —
        the whole report rests on a thin sample and should be read as a weak
        prior (Requirement 4.4). It is reported independently of
        ``insufficient_data`` (zero scored trades is also < ``global_min_scored``,
        so an empty report is both insufficient and a weak prior).

    PURE and TOTAL (Requirements 8.1, 8.2, 8.3): never raises on arbitrary
    in-memory rows, never mutates ``rows`` / their nested dicts / ``config``, and
    holds no ambient state, so identical ``rows`` + ``config`` yield a deep-equal
    report on every call (Determinism, Requirement 8.1). The ``config`` echo is a
    fresh plain dict built from the frozen ``AttributionConfig`` (never the
    dataclass itself), so a consumer cannot reach back and mutate the resolved
    configuration through the report.
    """
    # ── Report-level scored-trade tally + seeded/live split (count rows ONCE) ─
    # Distinct from the per-dimension totals: a single row is counted once here
    # regardless of how many dimensions its setup_key carries.
    total_scored = 0
    backtest_scored = 0
    live_scored = 0
    if isinstance(rows, list):
        for row in rows:
            if not is_scored_trade(row):
                continue
            total_scored += 1
            if _is_backtest_source(row):
                backtest_scored += 1
            else:
                live_scored += 1

    insufficient_data = total_scored == 0

    # ── Per-dimension/per-value stats -> ranked Dimension_Report list ─────────
    # When there are zero Scored_Trades the dimensions list is empty by
    # construction (compute_dimension_stats sees no scored rows), which we make
    # explicit so the contract (insufficient_data => dimensions == []) holds even
    # if a malformed config were ever threaded through.
    if insufficient_data:
        dimensions: list = []
    else:
        dimension_stats = compute_dimension_stats(rows, config)
        dimensions = rank_and_recommend(dimension_stats, config)

    # ── Resolved-config echo (fresh dict; five numeric fields per design) ─────
    config_echo = {
        "min_sample_dimension": config.min_sample_dimension,
        "min_sample_value": config.min_sample_value,
        "contribution_threshold": config.contribution_threshold,
        "global_min_scored": config.global_min_scored,
        "down_weight_factor": config.down_weight_factor,
    }

    return {
        "dimensions": dimensions,
        "total_scored": total_scored,
        "backtest_scored": backtest_scored,
        "live_scored": live_scored,
        "config": config_echo,
        "weak_prior": total_scored < config.global_min_scored,
        "insufficient_data": insufficient_data,
    }


# ── Pure core: optional Weight_Map derivation ─────────────────────────────────
# ``derive_weight_map`` collapses the Attribution_Report's per-dimension
# Recommendations into a flat ``{dimension: conviction_weight}`` mapping the
# agent can OPTIONALLY consult. Like every other stage of the pure core it is
# PURE and TOTAL: it reads only the supplied report + config, never mutates them,
# holds no ambient state, never raises, and is fully deterministic. The map is
# inert by default — it only ever SCALES how strongly a dimension informs
# conviction and is consulted solely when the agent opts in (design AD-5).

# The neutral conviction weight: a dimension that should inform conviction at
# full strength (a ``keep``) and the no-change weight for an ``insufficient_sample``
# dimension we deliberately refuse to act on (R6.1).
WEIGHT_KEEP = 1.0
WEIGHT_NEUTRAL = 1.0


def derive_weight_map(report: dict, config: AttributionConfig) -> dict[str, float]:
    """Derive a per-dimension conviction Weight_Map from an Attribution_Report.

    Pure mapping from each dimension's Recommendation to a conviction weight
    (design "Weight_Map" / AD-5, Requirement 6.1):

      * ``keep``                -> ``1.0``                        (full weight)
      * ``down_weight``         -> ``config.down_weight_factor``  (reduced, in (0,1])
      * ``insufficient_sample`` -> ``1.0``                        (neutral / no change)

    Iterates ``report["dimensions"]`` and reads each Dimension_Report's
    ``"recommendation"``, mapping it through the module Recommendation constants
    (``RECOMMENDATION_KEEP`` / ``RECOMMENDATION_DOWN_WEIGHT`` /
    ``RECOMMENDATION_INSUFFICIENT_SAMPLE``). The result is a flat
    ``{dimension: weight}`` dict, for example::

        { "rs": 1.0, "fc": 0.5, "opt": 1.0, ... }

    Every produced weight lies in the half-open interval ``(0.0, 1.0]``
    (Requirement 6.1): ``keep`` / ``insufficient_sample`` are ``1.0`` and
    ``down_weight`` is ``config.down_weight_factor``, which
    ``resolve_attribution_config`` already clamps to ``(0.0, 1.0]``. An
    unrecognized / missing recommendation degrades to the neutral ``1.0`` so the
    map can never silently zero out or amplify a dimension.

    The Weight_Map only ever SCALES a dimension's conviction contribution; it
    cannot, of itself, commit, block, override, or relax a hard risk rule
    (Requirement 6.4) — applying it is an opt-in agent action gated on
    ``config.weight_map_enabled`` (Requirement 6.2/6.3), not a property of this
    pure derivation.

    PURE and TOTAL (Requirements 6.1, 8.2, 8.3): never raises on an arbitrary /
    malformed report, never mutates ``report`` / its nested dicts / ``config``,
    and holds no ambient state, so identical inputs always yield an identical
    Weight_Map.
    """
    weight_map: dict[str, float] = {}
    if not isinstance(report, dict):
        return weight_map

    dimensions = report.get("dimensions")
    if not isinstance(dimensions, list):
        return weight_map

    # The resolved down-weight factor is already clamped to (0,1] at config
    # resolution; guard defensively so a hand-built config still yields a usable
    # weight in (0,1] rather than a 0 / negative / non-finite value.
    factor = config.down_weight_factor
    if not _is_num(factor) or factor <= 0.0 or factor > 1.0:
        factor = DEFAULT_DOWN_WEIGHT_FACTOR

    for entry in dimensions:
        if not isinstance(entry, dict):
            continue
        dimension = entry.get("dimension")
        if dimension is None:
            continue
        recommendation = entry.get("recommendation")
        if recommendation == RECOMMENDATION_DOWN_WEIGHT:
            weight = factor
        elif recommendation == RECOMMENDATION_KEEP:
            weight = WEIGHT_KEEP
        else:
            # insufficient_sample (and any unrecognized / missing label) is
            # neutral — full weight, no change to conviction (R6.1).
            weight = WEIGHT_NEUTRAL
        weight_map[dimension] = weight

    return weight_map


# ── Thin read-only I/O layer over the Trade_Journal ───────────────────────────
# These are the ONLY functions in the module that touch the database. They form
# a thin, defensive boundary around the pure core: the journal is opened READ
# ONLY and queried with a single ``SELECT`` (never an ``INSERT`` / ``UPDATE`` /
# ``DELETE`` / ``ALTER`` — and never ``journal.get_stats``, which scores open
# trades as a side effect), the rows are handed to ``build_attribution_report``,
# and ANY SQLite failure (missing / locked DB, schema drift) degrades to an
# ``insufficient_data`` report after a ``[Attribution]`` warning rather than
# raising into the caller. This mirrors ``calibration.conviction_calibration_from_journal``'s
# lazy ``import journal`` + defensive degradation contract (Requirements 1.5,
# 5.3, 9.1, 9.5).


def _read_scored_rows(
    symbol: Optional[str] = None, source: Optional[str] = None
) -> list[dict]:
    """Read raw journal rows for attribution via a single READ-ONLY ``SELECT``.

    Opens the journal store READ ONLY — a lazy ``import journal`` resolves the
    configured ``JOURNAL_DB_PATH`` and the connection is opened in SQLite's
    read-only URI mode (``file:...?mode=ro``) so the call CANNOT write: no
    ``INSERT`` / ``UPDATE`` / ``DELETE`` / ``ALTER``, no table/index creation, no
    WAL/rollback side files, and (importantly) it never calls ``journal.get_stats``
    which would score open trades as a side effect. It issues exactly ONE query::

        SELECT setup_key, status, r_multiple, source, symbol FROM trades [WHERE ...]

    selecting only the five columns the pure core consumes, and converts each
    ``sqlite3.Row`` to a plain ``dict`` (so the result is an ordinary in-memory
    row list with no live DB handle). Returns every row unfiltered by outcome —
    the pure ``build_attribution_report`` / ``is_scored_trade`` stage decides
    which rows are Scored_Trades; this layer only narrows by the optional
    ``symbol`` / ``source`` filters (Requirements 1.5, 9.1).

    Optional ``WHERE`` filtering:
      * ``symbol`` — when given, restricts to rows with that exact ``symbol``.
      * ``source`` — the seeded-vs-live split mirrors the pure core's
        ``_is_backtest_source`` (``source == 'backtest'`` case-insensitively /
        whitespace-tolerant is seeded; everything else — ``'live'``, ``NULL``,
        ``''``, any other value — is live). A ``source`` argument that normalizes
        to ``'backtest'`` selects ONLY backtest rows; any other ``source``
        argument (``'live'`` or otherwise) selects the complementary NON-backtest
        (live) rows. Expressed in SQL with ``LOWER(TRIM(COALESCE(source, '')))``
        so ``NULL`` / ``''`` / mixed-case sources split exactly as the pure core
        would.

    This function is the thin DB read; it does NOT swallow SQLite errors itself —
    a missing / locked DB or schema drift propagates to
    ``attribution_report_from_journal`` which owns the defensive degradation
    contract (Requirement 9.5).
    """
    import sqlite3  # lazy: keep the pure core import free of I/O deps
    import journal  # lazy: resolve JOURNAL_DB_PATH from the journal module

    where: list[str] = []
    params: list = []
    if symbol is not None:
        where.append("symbol = ?")
        params.append(symbol)
    if source is not None:
        # Mirror _is_backtest_source: normalize the stored source the same way
        # (lower + trim + NULL-as-empty) so the seeded/live split is identical.
        if str(source).strip().lower() == BACKTEST_SOURCE:
            where.append("LOWER(TRIM(COALESCE(source, ''))) = ?")
        else:
            where.append("LOWER(TRIM(COALESCE(source, ''))) != ?")
        params.append(BACKTEST_SOURCE)

    sql = "SELECT setup_key, status, r_multiple, source, symbol FROM trades"
    if where:
        sql += " WHERE " + " AND ".join(where)

    # Open READ ONLY via the URI mode so the connection is structurally incapable
    # of mutating the journal (no writes, no schema creation, no side files); a
    # missing DB raises here and is handled by the caller's degradation contract.
    db_path = journal.JOURNAL_DB_PATH
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def attribution_report_from_journal(
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    config: Optional[AttributionConfig] = None,
) -> dict:
    """Read the journal READ ONLY and build the Attribution_Report defensively.

    The single offline / agent entry point that turns persisted journal rows into
    a structured Attribution_Report (design "Data Models"). It:

      1. resolves ``config`` from the environment when none is supplied
         (``resolve_attribution_config`` never raises, Requirement 7);
      2. reads already-recorded rows via the read-only ``_read_scored_rows``
         (optionally narrowed by ``symbol`` / ``source``); and
      3. hands them to the pure ``build_attribution_report``.

    ALL SQLite access is wrapped in a ``try`` / ``except`` that, on ANY failure —
    a missing or locked database, a dropped/renamed ``trades`` table or schema
    drift, or any other ``sqlite3`` error — logs a single ``[Attribution]``
    warning and returns ``build_attribution_report([], config)``: a well-formed
    ``insufficient_data`` report over zero rows (Requirements 5.3, 9.5). It
    therefore NEVER raises into the caller (the CLI / agent opt-in), mirroring the
    journal-module convention that a read failure degrades to "no stats" rather
    than aborting the run.

    Read-only guarantee (Requirement 9.1): the only database statement issued is
    the single ``SELECT`` in ``_read_scored_rows`` over a read-only connection —
    no write, no schema mutation, and no implicit scoring of open trades.
    """
    if config is None:
        config = resolve_attribution_config()
    try:
        rows = _read_scored_rows(symbol=symbol, source=source)
    except Exception as e:
        print(f"[Attribution] WARN: read from journal failed: {e}")
        return build_attribution_report([], config)
    return build_attribution_report(rows, config)


def weight_map_from_journal(symbol: Optional[str] = None) -> dict[str, float]:
    """Build the Attribution_Report from the journal and derive its Weight_Map.

    Convenience composition of ``attribution_report_from_journal`` ->
    ``derive_weight_map`` over a single resolved configuration (so the
    ``down_weight_factor`` used to weight a ``down_weight`` dimension matches the
    config echoed in the report). Reads the journal READ ONLY and inherits the
    same defensive degradation contract: on any SQLite failure the underlying
    report is an ``insufficient_data`` report with no dimensions, so the derived
    Weight_Map is simply ``{}`` (Requirements 5.3, 9.5).

    The returned ``{dimension: conviction_weight}`` map is inert decision-support
    data: every weight lies in ``(0.0, 1.0]`` and only ever SCALES how strongly a
    dimension informs conviction; the agent consults it solely when it opts in
    (``config.weight_map_enabled``), and it can never commit, block, override, or
    relax a hard risk rule (design AD-5, Requirement 6). Never raises.
    """
    config = resolve_attribution_config()
    report = attribution_report_from_journal(symbol=symbol, config=config)
    return derive_weight_map(report, config)


# ── CLI ───────────────────────────────────────────────────────────────────────
# A thin, READ-ONLY command-line front door over the journal entry points,
# mirroring ``backtest.py``'s ``main()`` conventions: an ``argparse`` parser, a
# ``print(json.dumps(report, indent=2))`` dump of the structured report, and the
# ``if __name__ == "__main__": main()`` guard. The CLI emits NO trade decision
# and NEVER writes to the journal (Requirement 9.3); it always exits ``0`` — even
# on an empty or insufficient journal, because the report itself carries
# ``insufficient_data`` rather than signalling emptiness through an error code
# (Requirement 4.2).


def main() -> None:
    """Print the Attribution_Report (and optional Weight_Map) as JSON.

    Resolves the configuration from the environment (``resolve_attribution_config``
    never raises), reads the journal READ ONLY via ``attribution_report_from_journal``
    (optionally narrowed by ``--source`` / ``--symbol``), and prints the report as
    indented JSON. With ``--weight-map`` it also derives and prints the Weight_Map
    over the SAME resolved config (so a ``down_weight`` dimension's weight matches
    the ``down_weight_factor`` echoed in the report). With ``--json`` the
    human-readable header is suppressed so the output is machine-readable only.

    Read-only and emits no trade decision (Requirement 9.3). Exits ``0`` even on an
    empty / insufficient journal — the report carries ``insufficient_data`` rather
    than failing (Requirement 4.2).
    """
    p = argparse.ArgumentParser(
        description="Feature_Attribution — read-only attribution & pruning report over the Trade_Journal."
    )
    p.add_argument(
        "--source",
        default=None,
        help="Restrict to one trade source, e.g. 'backtest' (seeded prior) or 'live'. Default: whole journal.",
    )
    p.add_argument(
        "--symbol",
        default=None,
        help="Restrict to a single symbol, e.g. RELIANCE. Default: every symbol.",
    )
    p.add_argument(
        "--weight-map",
        action="store_true",
        help="Also derive and print the per-dimension conviction Weight_Map.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable only: suppress the human-readable header text.",
    )
    args = p.parse_args()

    # Resolve once so the report echo and any derived Weight_Map share a single
    # configuration (matching down_weight_factor).
    config = resolve_attribution_config()

    # READ-ONLY: build the report from already-recorded journal rows. This never
    # raises (a read failure degrades to an insufficient_data report) and emits
    # no trade decision (R9.3).
    report = attribution_report_from_journal(
        symbol=args.symbol, source=args.source, config=config
    )

    if not args.json:
        # Human-readable header (suppressed under --json). Plain context only —
        # never a trade decision.
        scope = []
        if args.symbol:
            scope.append(f"symbol={args.symbol}")
        if args.source:
            scope.append(f"source={args.source}")
        scope_str = (" [" + ", ".join(scope) + "]") if scope else " [whole journal]"
        print(f"[Attribution] Feature_Attribution report{scope_str}")
        if report.get("insufficient_data"):
            print("[Attribution] Insufficient data: no Scored_Trades in scope.")
        elif report.get("weak_prior"):
            print("[Attribution] Weak prior: total scored trades below global_min_scored.")

    print(json.dumps(report, indent=2))

    if args.weight_map:
        # Derive over the SAME resolved config so the down_weight weight matches
        # the report's echoed down_weight_factor.
        weight_map = derive_weight_map(report, config)
        if not args.json:
            print("\n[Attribution] Weight_Map:")
        print(json.dumps(weight_map, indent=2))


if __name__ == "__main__":
    main()
