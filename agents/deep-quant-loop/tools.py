import os
import json
import csv
import math
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
import httpx

# Local aliases used by the pure volume-profile helpers below.
math_isfinite = math.isfinite
math_floor = math.floor
from langchain_core.tools import tool
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

# Regime_Classifier — the single source of truth for the regime math. The
# get_market_regime tool delegates threshold resolution and classification to
# this pure module (AD-1, AD-2); the tool itself only fetches candles and
# re-validates the contract.
import regime

# Relative_Strength_Calculator — the single source of truth for the
# relative-strength math (AD-2). The get_relative_strength tool delegates
# benchmark/parameter resolution and classification to this pure module; the
# tool itself only fetches the symbol + benchmark candles and re-validates the
# contract.
import rs

# Order_Flow_Calculator — the single source of truth for the order-flow math
# (AD-1, AD-2). The get_order_flow tool delegates parameter resolution and
# classification to this pure module; the tool itself only fetches the symbol
# candles (proxy layer) and reads recent ticks (Tick_OFI layer) and re-validates
# the contract.
import order_flow

# Volatility_Forecaster — the single source of truth for the forecast math
# (AD-1, AD-2). The get_forecast tool delegates parameter resolution and the
# drift/volatility/regime-conditioned-blend classification to this pure module;
# the tool itself only fetches the symbol candles and re-validates the contract.
import forecaster

# Session_Classifier — the single source of truth for the session/expiry math
# (AD-1, AD-2, AD-3). The get_session_context tool delegates parameter
# resolution and classification to this pure module; the tool itself only
# fetches the most recent candle from the authoritative Rust Tool_Server, reads
# its timestamp, and re-validates the contract. The same module is reused by the
# Backtest_Seeder so the live path and the backtest path share one source of
# truth for the session math.
import session

# Options_Analytics_Engine (F2) — the single source of truth for the options
# analytics math (AD-2). The get_options_analytics tool delegates ALL option
# chain / spot / future I/O and every analytic (PCR, max pain, OI buildup, OI
# walls, IV skew, futures basis) to this pure module's compute_options_analytics;
# the tool never recomputes any of it and consumes the result verbatim.
import options

# Options_Bias_Classifier (F3) — the single source of truth for the options
# *bias* math (AD-2, AD-3). The get_options_analytics tool delegates threshold
# resolution and the threshold-vote classification to this pure module; the tool
# itself only resolves the analyzed chain, calls the F2 engine, threads the
# result through classify_options_bias, and re-validates the contract.
import options_bias
from options_bias import classify_options_bias

# Trade_Validator + Trade_Manager (trade-management). declare_trade gates a
# declared Management_Plan through the pure Python Trade_Validator
# (validator.validate_trade(plan=...)) BEFORE forwarding to the authoritative
# Rust Tool_Server, and reuses trade_manager.plan_from_json to parse the
# JSON-serializable plan dict into a ManagementPlan (AD-2, AD-5; Requirement 4).
import validator
import trade_manager

# Adaptive Opportunity Engine (adaptive-opportunity-engine). Used to pass the
# resolved heartbeat/cap configuration to the Rust watcher on registration and to
# classify + scope a resume's cheap Delta_Recheck. Pure module; no market-data source.
import opportunity

# Event_Classifier (earnings-event-risk-gate) — the single source of truth for the
# scheduled-event (earnings/results) proximity math (AD-1, AD-2). The
# get_event_risk tool delegates parameter resolution, nearest-future selection,
# and classification to this pure module; the tool itself performs the only I/O
# (reads the process clock for the reference "now" and gathers candidate event
# dates from the operator-configured Event_Source) and re-validates the contract.
import events

RUST_SERVER_URL = "http://localhost:8084"

# ── Price-watch registration retry policy (Requirement 14.3) ─────────────────
# How many times watch_price_condition attempts to register a watcher with the
# Rust Tool_Server before giving up, and the delay between attempts. Both are
# configurable via the environment so the retry budget is not hardcoded.
WATCH_REGISTRATION_MAX_ATTEMPTS = int(os.getenv("WATCH_REGISTRATION_MAX_ATTEMPTS", "3"))
WATCH_REGISTRATION_RETRY_DELAY_S = float(os.getenv("WATCH_REGISTRATION_RETRY_DELAY_S", "2"))

def calculate_ema(prices: list, period: int) -> float:
    """Helper function to calculate Exponential Moving Average (EMA)."""
    if not prices:
        return 0.0
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema


# ── Consumer-side Tool_Result_Contract revalidation (Requirements 4.1, 5.1) ──
# The Rust Tool_Server guarantees each tool's Tool_Result_Contract on emit
# (producer-side); we re-validate the same contract here on receipt
# (consumer-side, AD-3) so that malformed data never reaches the model. A
# contract failure is *data*, not an exception: validate_contract NEVER raises —
# it returns a structured ``{"error", "contract_violation"}`` dict that the
# ReAct loop treats as a non-fatal tool error (graph._tool_result_is_error),
# letting the run continue rather than abort.

# Indicator fields the Consensus_Report must carry (numeric-or-null per R4.2/4.3).
_CONSENSUS_REQUIRED_FIELDS = (
    "current_price", "rsi_14", "ema_9", "ema_21", "sma_50",
    "macd_line", "macd_signal", "macd_histogram",
    "bb_upper", "bb_mid", "bb_lower",
    "atr_14", "vwap", "obv", "cmf",
)
_SR_REQUIRED_FIELDS = ("pivot", "s1", "s2", "s3", "r1", "r2", "r3")
_MULTI_TF_REQUIRED_FIELDS = ("trend_1h", "trend_4h", "trend_1d")
_PATTERN_REQUIRED_FIELDS = ("pattern_type", "sentiment", "confidence", "description")
_VALID_PROJECTION_DIRECTIONS = {"Up", "Down", "Flat"}

# ── Market_Regime_Tool contract (regime-detection-gate) ──────────────────────
# The supported candle timeframes accepted by get_market_regime (and the wider
# tool surface). A get_market_regime Regime_Label must carry a trend_state, a
# volatility_state, and a favorability each drawn from its fixed enum, plus the
# named Regime_Measures each present as a finite number or null. An
# Unavailable_Marker ({"unavailable": true, ...}) is an honest non-fatal result
# handled by the existing _has_honest_marker pass-through.
SUPPORTED_TIMEFRAMES = {"1m", "5m", "10m", "15m", "1h", "4h", "1d"}
REGIME_TREND_STATES = {"trending", "ranging", "transitional"}
REGIME_VOLATILITY_STATES = {"low", "normal", "high"}
REGIME_FAVORABILITY = {"favorable", "unfavorable", "neutral"}
_REGIME_MEASURE_FIELDS = (
    "directional_strength",
    "choppiness",
    "efficiency_ratio",
    "atr_percentile",
    "bb_width",
)

# ── Relative_Strength_Tool contract (relative-strength-context) ──────────────
# A get_relative_strength Relative_Strength_Label must carry an index_direction,
# a relative_strength_state, and an alignment each drawn from its fixed enum, a
# `benchmark` string identifying the resolved Benchmark_Index, plus the named
# Relative_Strength_Measures (under a 'measures' object) each present as a finite
# number or null. An Unavailable_Marker ({"unavailable": true, ...}) is an honest
# non-fatal result handled by the existing _has_honest_marker pass-through.
INDEX_DIRECTIONS = {"up", "down", "flat"}
RELATIVE_STRENGTH_STATES = {"leader", "inline", "laggard"}
ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}
_RS_MEASURE_FIELDS = (
    "rs_ratio",
    "rs_ratio_slope",
    "relative_return",
    "correlation",
    "beta",
)

# ── Order_Flow_Tool contract (order-flow-context) ────────────────────────────
# A get_order_flow Order_Flow_Label must carry an order_flow_state and an
# alignment each drawn from its fixed enum (alignment reuses ALIGNMENT_VALUES
# above), each named Order_Flow_Proxy_Measure (under a 'measures' object) present
# as a finite number or null, a `tick_ofi` finite-number-or-null, and a boolean
# `live_tick_contributed` flag. An Unavailable_Marker ({"unavailable": true, ...})
# is an honest non-fatal result handled by the existing _has_honest_marker
# pass-through (Requirement 5.8).
ORDER_FLOW_STATES = {"buying", "selling", "balanced"}
_OF_MEASURE_FIELDS = (
    "candle_delta",
    "cvd_proxy",
    "up_volume",
    "down_volume",
    "buying_pressure_ratio",
)

# ── Forecast_Tool contract (volatility-aware-forecaster) ─────────────────────
# A get_forecast Forecast_Label must carry a projected_direction drawn from its
# fixed enum, an up_probability finite number in [0.0, 1.0], an expected_move_atr
# finite-number-or-null, a forecast_confidence finite number in [0.0, 1.0], a
# forecast_alignment drawn from the shared ALIGNMENT_VALUES enum, plus each named
# forecast measure (under a 'measures' object) present as a finite number or
# null. An Unavailable_Marker ({"unavailable": true, ...}) is an honest non-fatal
# result handled by the existing _has_honest_marker pass-through (R5.8).
FORECAST_DIRECTIONS = {"up", "down", "flat"}
# Forecast_Alignment reuses the existing ALIGNMENT_VALUES = {"aligned","misaligned","neutral"}.
_FORECAST_MEASURE_FIELDS = ("drift", "volatility", "standardized_drift", "atr")

# ── Session_Tool contract (session-expiry-awareness) ─────────────────────────
# A get_session_context Session_Label must carry a `session_phase` drawn from
# the fixed SESSION_PHASES enum, a `minutes_since_open` and a `minutes_until_close`
# each present as a finite number or null, an `expiry_context` object carrying a
# boolean `is_expiry_day` and a finite-number `days_until_expiry`, and a
# `time_favorability` drawn from the fixed TIME_FAVORABILITY enum. The session
# label is computed PURELY from the most recent candle's timestamp and the
# resolved configuration — no external data source (AD-1). An Unavailable_Marker
# ({"unavailable": true, ...}) is an honest non-fatal result handled by the
# existing _has_honest_marker pass-through (Requirements 4.8, 5.2).
SESSION_PHASES = {
    "pre_open", "opening", "morning", "midday", "afternoon", "closing", "post_close",
}
TIME_FAVORABILITY = {"favorable", "unfavorable", "neutral"}

# ── Event_Risk_Tool contract (earnings-event-risk-gate) ──────────────────────
# A get_event_risk Event_Assessment must carry an `event_risk` drawn from the
# fixed EVENT_RISK_STATES enum, an `event_recommendation` drawn from the fixed
# EVENT_RECOMMENDATIONS enum, a `days_until_event` present as a finite number or
# null, and an `event_date` string identifying the reference Scheduled_Event
# date. The assessment is computed PURELY by the events.py Event_Classifier from
# the process-clock reference "now", the nearest upcoming event date, and the
# resolved configuration — the tool performs the only I/O (AD-1). An
# Unavailable_Marker ({"unavailable": true, ...}) is an honest non-fatal result
# handled by the existing _has_honest_marker pass-through (Requirements 4.8, 5.1).
EVENT_RISK_STATES = {"clear", "imminent", "through_event"}
EVENT_RECOMMENDATIONS = {"proceed", "size_down", "shorten_horizon", "stand_aside"}

# ── Options_Analytics_Tool contract (options-agent-integration) ──────────────
# A get_options_analytics Options_Bias_Label must carry an `options_bias_state`
# and an `alignment` each drawn from its fixed enum (alignment reuses the shared
# ALIGNMENT_VALUES above), a `chain_context` drawn from OPTIONS_CHAIN_CONTEXTS,
# each named numeric-or-null analytic (`pcr_oi`, `pcr_volume`, `max_pain`,
# `futures_basis`) present as a finite number or null, an `oi_buildup` object
# carrying `call`/`put`, an `oi_walls` object carrying numeric-or-null
# `support`/`resistance`, and an `iv_skew` object-or-null. An Unavailable_Marker
# ({"unavailable": true, ...}, or the {"error": ...} from _options_unavailable)
# is an honest non-fatal result handled by the existing _has_honest_marker
# pass-through (Requirement 2.6).
OPTIONS_BIAS_STATES = {"bullish", "bearish", "neutral"}
OPTIONS_CHAIN_CONTEXTS = {"own-chain", "broad-market"}
_OPTIONS_NUMERIC_OR_NULL_FIELDS = ("pcr_oi", "pcr_volume", "max_pain", "futures_basis")

# The small set of index Underlyings whose OWN option chain is analyzed (labelled
# `own-chain`). A non-index symbol resolves to its Benchmark_Index chain via
# rs.resolve_benchmark instead, labelled `broad-market` (AD-4, Requirement 2.3).
# Matched case-insensitively against the upper-cased symbol.
INDEX_UNDERLYINGS = {"NIFTY 50", "NIFTY", "BANKNIFTY"}

# QuestDB HTTP query API for the Live_Ticks_Source (the same endpoint backtest.py
# uses for the historical archive). The Tick_OFI layer reads recent ticks for the
# symbol from the `live_ticks` table via this API; an unreachable server / empty
# result simply degrades the Tick_OFI to unavailable (R6.1).
QUESTDB_HTTP_URL = os.getenv("QUESTDB_HTTP_URL", "http://127.0.0.1:9000")
# How many recent ticks to read for the Tick_OFI (matches the Rust LIMIT 200).
OF_TICK_FETCH_LIMIT = int(os.getenv("OF_TICK_FETCH_LIMIT", "200"))

# Extra candles requested beyond the largest-lookback / min-candle gate so that
# excluding non-finite candles and intersecting the symbol & benchmark
# timestamps still leaves enough aligned candles to classify (Requirement 4.4).
RS_FETCH_MARGIN = int(os.getenv("RS_FETCH_MARGIN", "20"))


def _is_number(v) -> bool:
    """True for a finite-capable real number (bools are excluded)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_number_or_null(v) -> bool:
    return v is None or _is_number(v)


def _has_honest_marker(payload) -> bool:
    """True when the payload already carries an honest, non-fatal marker.

    An ``error`` key, an ``unavailable: true`` flag, an ``Unavailable``
    sentiment, or an unavailable/failed ``status`` are legitimate
    graceful-degradation results (R5/R10/R12), NOT contract violations, so they
    are passed through validate_contract unchanged.
    """
    if isinstance(payload, dict):
        if "error" in payload:
            return True
        if payload.get("unavailable") is True:
            return True
        ss = payload.get("sentiment_summary")
        if isinstance(ss, str) and ss.strip().lower() == "unavailable":
            return True
        status = payload.get("status")
        if isinstance(status, str) and status.strip().lower() in (
            "unavailable", "watch_registration_failed"
        ):
            return True
        return False
    if isinstance(payload, list):
        # get_candles' error path returns ``[{"error": ...}]``.
        return any(isinstance(item, dict) and "error" in item for item in payload)
    return False


def _contract_error(detail: str) -> dict:
    """Build the structured contract-violation result returned to the model."""
    return {
        "error": f"Tool result failed contract validation: {detail}",
        "contract_violation": detail,
    }


def validate_contract(tool_name, payload):
    """Re-validate a tool result against its Tool_Result_Contract on receipt.

    Returns ``payload`` unchanged when it conforms (or already carries an honest
    error/unavailable marker); otherwise returns a structured
    ``{"error", "contract_violation"}`` dict. NEVER raises — contract failures
    are data, not exceptions (AD-3, Requirements 4.1, 5.1).
    """
    # Honest non-fatal markers are pass-through — they are not violations.
    if _has_honest_marker(payload):
        return payload

    try:
        if tool_name == "get_candles":
            if not isinstance(payload, list):
                return _contract_error(
                    f"get_candles expected a list of candles, got {type(payload).__name__}"
                )
            for i, candle in enumerate(payload):
                if not isinstance(candle, dict):
                    return _contract_error(f"candle[{i}] is not an object")
                for field in ("timestamp_ms", "open", "high", "low", "close", "volume"):
                    if field not in candle:
                        return _contract_error(f"candle[{i}] missing field '{field}'")
                    if not _is_number(candle[field]):
                        return _contract_error(f"candle[{i}].{field} is not numeric")
            return payload

        if tool_name == "get_consensus_report":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_consensus_report expected an object, got {type(payload).__name__}"
                )
            for field in _CONSENSUS_REQUIRED_FIELDS:
                if field not in payload:
                    return _contract_error(f"consensus report missing field '{field}'")
                if not _is_number_or_null(payload[field]):
                    return _contract_error(
                        f"consensus field '{field}' is neither numeric nor null"
                    )
            return payload

        if tool_name == "get_support_resistance":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_support_resistance expected an object, got {type(payload).__name__}"
                )
            for field in _SR_REQUIRED_FIELDS:
                if field not in payload:
                    return _contract_error(f"support/resistance missing field '{field}'")
                if not _is_number(payload[field]):
                    return _contract_error(f"support/resistance field '{field}' is not numeric")
            return payload

        if tool_name == "get_news_context":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_news_context expected an object, got {type(payload).__name__}"
                )
            if "sentiment_summary" not in payload:
                return _contract_error("news context missing 'sentiment_summary'")
            return payload

        if tool_name == "get_prediction":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_prediction expected an object, got {type(payload).__name__}"
                )
            direction = payload.get("projected_direction")
            if direction not in _VALID_PROJECTION_DIRECTIONS:
                return _contract_error(
                    f"projected_direction '{direction}' not in {{Up, Down, Flat}}"
                )
            if not _is_number(payload.get("projected_value")):
                return _contract_error("prediction missing numeric 'projected_value'")
            if not _is_number(payload.get("confidence")):
                return _contract_error("prediction missing numeric 'confidence'")
            return payload

        if tool_name == "get_volume_profile":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_volume_profile expected an object, got {type(payload).__name__}"
                )
            # poc/vah/val are numeric-or-null (null only on an empty profile,
            # i.e. zero traded volume over the range — a valid honest result).
            for field in ("poc", "vah", "val"):
                if field not in payload:
                    return _contract_error(f"volume profile missing field '{field}'")
                if not _is_number_or_null(payload[field]):
                    return _contract_error(
                        f"volume profile field '{field}' is neither numeric nor null"
                    )
            if not _is_number(payload.get("total_volume")):
                return _contract_error("volume profile missing numeric 'total_volume'")
            return payload

        if tool_name == "get_multi_tf_trend":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_multi_tf_trend expected an object, got {type(payload).__name__}"
                )
            for field in _MULTI_TF_REQUIRED_FIELDS:
                if field not in payload:
                    return _contract_error(f"multi-tf trend missing field '{field}'")
            return payload

        if tool_name == "get_chart_patterns":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_chart_patterns expected an object, got {type(payload).__name__}"
                )
            patterns = payload.get("patterns")
            if not isinstance(patterns, list):
                return _contract_error("chart patterns 'patterns' field is not a list")
            for i, p in enumerate(patterns):
                if not isinstance(p, dict):
                    return _contract_error(f"pattern[{i}] is not an object")
                for field in _PATTERN_REQUIRED_FIELDS:
                    if field not in p:
                        return _contract_error(f"pattern[{i}] missing field '{field}'")
                conf = p.get("confidence")
                if not _is_number(conf) or not (0.0 <= conf <= 1.0):
                    return _contract_error(
                        f"pattern[{i}].confidence is not a number in [0.0, 1.0]"
                    )
            return payload

        if tool_name == "get_market_regime":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_market_regime expected an object, got {type(payload).__name__}"
                )
            # A conforming Regime_Label carries the three categorical states in
            # their fixed enums plus each named measure as finite-number-or-null.
            # (An Unavailable_Marker was already passed through above by
            # _has_honest_marker, so anything reaching here must be a full label.)
            trend_state = payload.get("trend_state")
            if trend_state not in REGIME_TREND_STATES:
                return _contract_error(
                    f"trend_state '{trend_state}' not in "
                    "{trending, ranging, transitional}"
                )
            volatility_state = payload.get("volatility_state")
            if volatility_state not in REGIME_VOLATILITY_STATES:
                return _contract_error(
                    f"volatility_state '{volatility_state}' not in {{low, normal, high}}"
                )
            favorability = payload.get("favorability")
            if favorability not in REGIME_FAVORABILITY:
                return _contract_error(
                    f"favorability '{favorability}' not in "
                    "{favorable, unfavorable, neutral}"
                )
            # The named Regime_Measures live under a 'measures' object; each must
            # be present as a finite number or null.
            measures = payload.get("measures")
            if not isinstance(measures, dict):
                return _contract_error("regime 'measures' field is not an object")
            for field in _REGIME_MEASURE_FIELDS:
                if field not in measures:
                    return _contract_error(f"regime measures missing field '{field}'")
                if not _is_number_or_null(measures[field]):
                    return _contract_error(
                        f"regime measure '{field}' is neither numeric nor null"
                    )
            return payload

        if tool_name == "get_relative_strength":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_relative_strength expected an object, got {type(payload).__name__}"
                )
            # A conforming Relative_Strength_Label carries the three categorical
            # states in their fixed enums, a `benchmark` string, plus each named
            # measure as finite-number-or-null. (An Unavailable_Marker was
            # already passed through above by _has_honest_marker, so anything
            # reaching here must be a full label.)
            index_direction = payload.get("index_direction")
            if index_direction not in INDEX_DIRECTIONS:
                return _contract_error(
                    f"index_direction '{index_direction}' not in {{up, down, flat}}"
                )
            relative_strength_state = payload.get("relative_strength_state")
            if relative_strength_state not in RELATIVE_STRENGTH_STATES:
                return _contract_error(
                    f"relative_strength_state '{relative_strength_state}' not in "
                    "{leader, inline, laggard}"
                )
            alignment = payload.get("alignment")
            if alignment not in ALIGNMENT_VALUES:
                return _contract_error(
                    f"alignment '{alignment}' not in {{aligned, misaligned, neutral}}"
                )
            # The resolved Benchmark_Index must be present as a string.
            benchmark = payload.get("benchmark")
            if not isinstance(benchmark, str):
                return _contract_error("relative strength missing 'benchmark' string")
            # The named Relative_Strength_Measures live under a 'measures' object;
            # each must be present as a finite number or null.
            measures = payload.get("measures")
            if not isinstance(measures, dict):
                return _contract_error("relative strength 'measures' field is not an object")
            for field in _RS_MEASURE_FIELDS:
                if field not in measures:
                    return _contract_error(
                        f"relative strength measures missing field '{field}'"
                    )
                if not _is_number_or_null(measures[field]):
                    return _contract_error(
                        f"relative strength measure '{field}' is neither numeric nor null"
                    )
            return payload

        if tool_name == "get_order_flow":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_order_flow expected an object, got {type(payload).__name__}"
                )
            # A conforming Order_Flow_Label carries the two categorical states in
            # their fixed enums, each named proxy measure as finite-number-or-null
            # (under a 'measures' object), a finite-number-or-null Tick_OFI, and a
            # boolean live-tick-contributed flag. (An Unavailable_Marker was
            # already passed through above by _has_honest_marker, so anything
            # reaching here must be a full label.)
            order_flow_state = payload.get("order_flow_state")
            if order_flow_state not in ORDER_FLOW_STATES:
                return _contract_error(
                    f"order_flow_state '{order_flow_state}' not in "
                    "{buying, selling, balanced}"
                )
            alignment = payload.get("alignment")
            if alignment not in ALIGNMENT_VALUES:
                return _contract_error(
                    f"alignment '{alignment}' not in {{aligned, misaligned, neutral}}"
                )
            # The named Order_Flow_Proxy_Measures live under a 'measures' object;
            # each must be present as a finite number or null.
            measures = payload.get("measures")
            if not isinstance(measures, dict):
                return _contract_error("order flow 'measures' field is not an object")
            for field in _OF_MEASURE_FIELDS:
                if field not in measures:
                    return _contract_error(
                        f"order flow measures missing field '{field}'"
                    )
                if not _is_number_or_null(measures[field]):
                    return _contract_error(
                        f"order flow measure '{field}' is neither numeric nor null"
                    )
            # The Tick_OFI must be present as a finite number or null.
            if "tick_ofi" not in payload:
                return _contract_error("order flow missing field 'tick_ofi'")
            if not _is_number_or_null(payload["tick_ofi"]):
                return _contract_error(
                    "order flow 'tick_ofi' is neither numeric nor null"
                )
            # The live-tick-contributed flag must be a boolean.
            if not isinstance(payload.get("live_tick_contributed"), bool):
                return _contract_error(
                    "order flow 'live_tick_contributed' is not a boolean"
                )
            return payload

        if tool_name == "get_forecast":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_forecast expected an object, got {type(payload).__name__}"
                )
            # A conforming Forecast_Label carries a projected_direction in its
            # fixed enum, an up_probability finite number in [0.0, 1.0], an
            # expected_move_atr finite-number-or-null, a forecast_confidence
            # finite number in [0.0, 1.0], a forecast_alignment in the shared
            # ALIGNMENT_VALUES enum, plus each named measure (under a 'measures'
            # object) as finite-number-or-null. (An Unavailable_Marker was
            # already passed through above by _has_honest_marker, so anything
            # reaching here must be a full label.)
            projected_direction = payload.get("projected_direction")
            if projected_direction not in FORECAST_DIRECTIONS:
                return _contract_error(
                    f"projected_direction '{projected_direction}' not in "
                    "{up, down, flat}"
                )
            # up_probability: a finite number in [0.0, 1.0] (reusing the same
            # numeric-bounds pattern as the get_chart_patterns confidence check).
            up_probability = payload.get("up_probability")
            if not _is_number(up_probability) or not (0.0 <= up_probability <= 1.0):
                return _contract_error(
                    "forecast 'up_probability' is not a number in [0.0, 1.0]"
                )
            # expected_move_atr: a finite number or null (null when ATR is
            # zero/unavailable).
            if "expected_move_atr" not in payload:
                return _contract_error("forecast missing field 'expected_move_atr'")
            if not _is_number_or_null(payload["expected_move_atr"]):
                return _contract_error(
                    "forecast 'expected_move_atr' is neither numeric nor null"
                )
            # forecast_confidence: a finite number in [0.0, 1.0].
            forecast_confidence = payload.get("forecast_confidence")
            if not _is_number(forecast_confidence) or not (
                0.0 <= forecast_confidence <= 1.0
            ):
                return _contract_error(
                    "forecast 'forecast_confidence' is not a number in [0.0, 1.0]"
                )
            forecast_alignment = payload.get("forecast_alignment")
            if forecast_alignment not in ALIGNMENT_VALUES:
                return _contract_error(
                    f"forecast_alignment '{forecast_alignment}' not in "
                    "{aligned, misaligned, neutral}"
                )
            # The named forecast measures live under a 'measures' object; each
            # must be present as a finite number or null.
            measures = payload.get("measures")
            if not isinstance(measures, dict):
                return _contract_error("forecast 'measures' field is not an object")
            for field in _FORECAST_MEASURE_FIELDS:
                if field not in measures:
                    return _contract_error(
                        f"forecast measures missing field '{field}'"
                    )
                if not _is_number_or_null(measures[field]):
                    return _contract_error(
                        f"forecast measure '{field}' is neither numeric nor null"
                    )
            return payload

        if tool_name == "get_session_context":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_session_context expected an object, got {type(payload).__name__}"
                )
            # A conforming Session_Label carries a `session_phase` drawn from the
            # fixed SESSION_PHASES enum, a `minutes_since_open` and a
            # `minutes_until_close` each finite-number-or-null (null outside the
            # session), an `expiry_context` object with a boolean `is_expiry_day`
            # and a finite-number `days_until_expiry`, and a `time_favorability`
            # drawn from the fixed TIME_FAVORABILITY enum. (An Unavailable_Marker
            # was already passed through above by _has_honest_marker, so anything
            # reaching here must be a full label.)
            session_phase = payload.get("session_phase")
            if session_phase not in SESSION_PHASES:
                return _contract_error(
                    f"session_phase '{session_phase}' not in "
                    "{pre_open, opening, morning, midday, afternoon, closing, post_close}"
                )
            # minutes_since_open / minutes_until_close: finite number or null.
            if "minutes_since_open" not in payload:
                return _contract_error("session missing field 'minutes_since_open'")
            if not _is_number_or_null(payload["minutes_since_open"]):
                return _contract_error(
                    "session 'minutes_since_open' is neither numeric nor null"
                )
            if "minutes_until_close" not in payload:
                return _contract_error("session missing field 'minutes_until_close'")
            if not _is_number_or_null(payload["minutes_until_close"]):
                return _contract_error(
                    "session 'minutes_until_close' is neither numeric nor null"
                )
            # The Expiry_Context lives under an 'expiry_context' object carrying a
            # boolean `is_expiry_day` and a finite-number `days_until_expiry`.
            expiry_context = payload.get("expiry_context")
            if not isinstance(expiry_context, dict):
                return _contract_error("session 'expiry_context' field is not an object")
            if not isinstance(expiry_context.get("is_expiry_day"), bool):
                return _contract_error(
                    "session 'expiry_context.is_expiry_day' is not a boolean"
                )
            if "days_until_expiry" not in expiry_context:
                return _contract_error(
                    "session 'expiry_context' missing field 'days_until_expiry'"
                )
            if not _is_number(expiry_context["days_until_expiry"]):
                return _contract_error(
                    "session 'expiry_context.days_until_expiry' is not numeric"
                )
            time_favorability = payload.get("time_favorability")
            if time_favorability not in TIME_FAVORABILITY:
                return _contract_error(
                    f"time_favorability '{time_favorability}' not in "
                    "{favorable, unfavorable, neutral}"
                )
            return payload

        if tool_name == "get_event_risk":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_event_risk expected an object, got {type(payload).__name__}"
                )
            # A conforming Event_Assessment carries an `event_risk` drawn from the
            # fixed EVENT_RISK_STATES enum, an `event_recommendation` drawn from
            # the fixed EVENT_RECOMMENDATIONS enum, a `days_until_event` present as
            # a finite number or null (null when no day count is available), and an
            # `event_date` string identifying the reference Scheduled_Event date.
            # (An Unavailable_Marker carries `unavailable: true` and was already
            # passed through above by _has_honest_marker, so anything reaching here
            # must be a full assessment.)
            event_risk = payload.get("event_risk")
            if event_risk not in EVENT_RISK_STATES:
                return _contract_error(
                    f"event_risk '{event_risk}' not in "
                    "{clear, imminent, through_event}"
                )
            event_recommendation = payload.get("event_recommendation")
            if event_recommendation not in EVENT_RECOMMENDATIONS:
                return _contract_error(
                    f"event_recommendation '{event_recommendation}' not in "
                    "{proceed, size_down, shorten_horizon, stand_aside}"
                )
            # days_until_event: a finite number or null.
            if "days_until_event" not in payload:
                return _contract_error("event missing field 'days_until_event'")
            if not _is_number_or_null(payload["days_until_event"]):
                return _contract_error(
                    "event 'days_until_event' is neither numeric nor null"
                )
            # The reference Scheduled_Event date must be present as a string.
            if not isinstance(payload.get("event_date"), str):
                return _contract_error("event missing 'event_date' string")
            return payload

        if tool_name == "get_options_analytics":
            if not isinstance(payload, dict):
                return _contract_error(
                    f"get_options_analytics expected an object, got {type(payload).__name__}"
                )
            # A conforming Options_Bias_Label carries the two categorical labels
            # in their fixed enums (alignment reuses ALIGNMENT_VALUES), a
            # `chain_context` in OPTIONS_CHAIN_CONTEXTS, each named analytic as
            # finite-number-or-null, an `oi_buildup` object with call/put, an
            # `oi_walls` object with numeric-or-null support/resistance, and an
            # `iv_skew` object-or-null. (An Unavailable_Marker was already passed
            # through above by _has_honest_marker, so anything reaching here must
            # be a full label.)
            options_bias_state = payload.get("options_bias_state")
            if options_bias_state not in OPTIONS_BIAS_STATES:
                return _contract_error(
                    f"options_bias_state '{options_bias_state}' not in "
                    "{bullish, bearish, neutral}"
                )
            alignment = payload.get("alignment")
            if alignment not in ALIGNMENT_VALUES:
                return _contract_error(
                    f"alignment '{alignment}' not in {{aligned, misaligned, neutral}}"
                )
            chain_context = payload.get("chain_context")
            if chain_context not in OPTIONS_CHAIN_CONTEXTS:
                return _contract_error(
                    f"chain_context '{chain_context}' not in "
                    "{own-chain, broad-market}"
                )
            # The named analytics must each be present as a finite number or null.
            for field in _OPTIONS_NUMERIC_OR_NULL_FIELDS:
                if field not in payload:
                    return _contract_error(f"options analytics missing field '{field}'")
                if not _is_number_or_null(payload[field]):
                    return _contract_error(
                        f"options analytic '{field}' is neither numeric nor null"
                    )
            # The aggregate OI buildup lives under an 'oi_buildup' object carrying
            # a `call` and a `put`.
            oi_buildup = payload.get("oi_buildup")
            if not isinstance(oi_buildup, dict):
                return _contract_error("options 'oi_buildup' field is not an object")
            for side in ("call", "put"):
                if side not in oi_buildup:
                    return _contract_error(f"options 'oi_buildup' missing field '{side}'")
            # The nearest OI walls live under an 'oi_walls' object carrying a
            # numeric-or-null `support` and `resistance`.
            oi_walls = payload.get("oi_walls")
            if not isinstance(oi_walls, dict):
                return _contract_error("options 'oi_walls' field is not an object")
            for level in ("support", "resistance"):
                if level not in oi_walls:
                    return _contract_error(f"options 'oi_walls' missing field '{level}'")
                if not _is_number_or_null(oi_walls[level]):
                    return _contract_error(
                        f"options 'oi_walls.{level}' is neither numeric nor null"
                    )
            # The IV skew is an object or null.
            iv_skew = payload.get("iv_skew")
            if iv_skew is not None and not isinstance(iv_skew, dict):
                return _contract_error("options 'iv_skew' is neither an object nor null")
            return payload

        # Unknown / non-contract tool (e.g. declare_trade, watch_price_condition):
        # nothing to validate — pass through unchanged.
        return payload
    except Exception as e:  # defensive: never raise — contract failures are data
        return _contract_error(f"unexpected error validating {tool_name}: {e}")

@tool
def get_candles(symbol: str, timeframe: str, limit: int) -> list:
    """
    Fetch raw OHLCV candle data with timestamps. Valid timeframes: '1m', '5m', '10m', '15m', '1h', '4h', '1d'.
    
    Args:
        symbol (str): The trading symbol to fetch (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        limit (int): The number of recent candles to retrieve.
        
    Returns:
        list: A list of candles in ascending chronological order. Each candle is a dictionary with:
              - timestamp_ms (int): UNIX timestamp in milliseconds
              - open, high, low, close (float): OHLC prices
              - volume (float): Trade volume
    """
    print(f"\n[Tool Call] >>> get_candles: symbol={symbol}, timeframe={timeframe}, limit={limit}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "timeframe": timeframe, "limit": limit},
            timeout=10.0
        )
        # Classify the differentiated candle-endpoint outcomes (R2). The Rust
        # Tool_Server now distinguishes an Availability_Shortfall from an
        # Infrastructure_Fault, so we must NOT call raise_for_status() blindly
        # (which would mask both behind the same opaque failure).
        if response.status_code == 200:
            res = response.json()
            # Availability_Shortfall: a graceful, non-5xx "not enough data yet"
            # result. Surface it as the honest list-error Unavailable_Marker that
            # _has_honest_marker and the Data_Tools already tolerate.
            if isinstance(res, dict) and res.get("unavailable"):
                reason = res.get("reason", "candle data unavailable")
                print(f"[Tool Success] <<< get_candles: symbol={symbol}, timeframe={timeframe}, unavailable ({reason})")
                return [{"error": reason}]
            # Normal candle list.
            count = len(res) if isinstance(res, list) else 0
            print(f"[Tool Success] <<< get_candles: symbol={symbol}, timeframe={timeframe}, retrieved {count} candles.")
            return validate_contract("get_candles", res)
        # 5xx (and any other non-200): a genuine Infrastructure_Fault. Surface the
        # named cause the server provided — still non-fatal (list-error marker).
        detail = response.text.strip()[:200]
        print(f"[Tool Error] Server returned {response.status_code}: {detail}")
        return [{"error": f"candle store fault: {detail}"}]
    except Exception as e:
        print(f"[Tool Error] <<< get_candles FAIL: {str(e)}")
        return [{"error": f"Failed to retrieve candles from Rust server: {str(e)}"}]

@tool
def get_consensus_report(symbol: str, timeframe: str) -> dict:
    """
    Calculates live technical consensus with full raw indicator values for a specific timeframe.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        timeframe (str): The timeframe to analyze (e.g., "1m", "5m", "10m","15m", "1h", "4h", "1d").
        
    Returns:
        dict: Comprehensive consensus report containing:
              - Aggregate states: trend_score (-100 to +100), momentum_state, volatility_state, volume_flow_state
              - Pattern recognition: active_patterns (e.g. Doji, Engulfing), active_strategies
              - Raw indicators: current_price, rsi_14, stoch_k, ema_9, ema_21, sma_50, sma_200,
                macd_line, macd_signal, macd_histogram, bb_upper, bb_mid, bb_lower,
                atr_14, vwap, obv, cmf, parabolic_sar
              - Projections: vwepr_value, vwepr_slope, ols_value, ols_slope
    """
    print(f"\n[Tool Call] >>> get_consensus_report: symbol={symbol}, timeframe={timeframe}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_consensus",
            json={"symbol": symbol, "timeframe": timeframe, "limit": 200},
            timeout=10.0
        )
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        res = response.json()
        print(f"[Tool Success] <<< get_consensus_report: symbol={symbol}, trend_score={res.get('trend_score')}, momentum={res.get('momentum_state')}")
        return validate_contract("get_consensus_report", res)
    except Exception as e:
        print(f"[Tool Error] <<< get_consensus_report FAIL: {str(e)}")
        return {"error": f"Failed to compile consensus report: {str(e)}"}

@tool
def get_multi_tf_trend(symbol: str) -> dict:
    """
    Fetches the macro directional bias across 1H, 4H, and 1D simultaneously.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        
    Returns:
        dict: Directional trend bias ("Bullish" or "Bearish") across 1H, 4H, and 1D horizons.
    """
    print(f"\n[Tool Call] >>> get_multi_tf_trend: symbol={symbol}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_multi_tf_trend",
            json={"symbol": symbol},
            timeout=10.0
        )
        response.raise_for_status()
        res = response.json()
        print(f"[Tool Success] <<< get_multi_tf_trend: symbol={symbol}, response={res}")
        return validate_contract("get_multi_tf_trend", res)
    except Exception as e:
        print(f"[Tool Error] <<< get_multi_tf_trend FAIL: {str(e)}")
        return {"error": f"Failed to compute multi-tf trend: {str(e)}"}

@tool
def get_chart_patterns(symbol: str, timeframe: str, limit: int = 200) -> dict:
    """
    Identifies structural chart patterns (e.g. Head & Shoulders, Double Top/Bottom, 
    Triangles, Flags, Wedges, Cup & Handle) from historical candle data using the 
    high-performance Rust pattern detection engine.
    
    Use this tool to gain a structural edge by detecting formation-level setups across
    any timeframe. The engine detects 19 distinct patterns categorized as:
      - Reversal (8): Head & Shoulders, Inverse H&S, Double Top/Bottom, Triple Top/Bottom, Rising/Falling Wedge
      - Continuation (6): Bullish/Bearish Flag, Bullish/Bearish Pennant, Cup & Handle, Inverse Cup & Handle
      - Bilateral (4): Symmetrical Triangle, Ascending Triangle, Descending Triangle, Rectangle
    
    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "10m", "15m", "1h", "4h", "1d").
        limit (int): Number of recent candles to analyze (default 200, more candles = longer patterns detected).
        
    Returns:
        dict: Contains 'symbol', 'timeframe', and 'patterns' — a list of detected patterns,
              each with pattern_type, sentiment (Bullish/Bearish/Neutral), confidence (0.0-1.0),
              start_idx, end_idx, and a human-readable description.
    """
    print(f"\n[Tool Call] >>> get_chart_patterns: symbol={symbol}, timeframe={timeframe}, limit={limit}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_chart_patterns",
            json={"symbol": symbol, "timeframe": timeframe, "limit": limit},
            timeout=15.0
        )
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        res = response.json()
        patterns = res.get("patterns", [])
        print(f"[Tool Success] <<< get_chart_patterns: symbol={symbol}, timeframe={timeframe}, detected {len(patterns)} patterns")
        for p in patterns:
            print(f"  → {p.get('pattern_type')} ({p.get('sentiment')}, confidence={p.get('confidence', 0):.2f})")
        return validate_contract("get_chart_patterns", res)
    except Exception as e:
        print(f"[Tool Error] <<< get_chart_patterns FAIL: {str(e)}")
        return {"error": f"Failed to detect chart patterns: {str(e)}"}

# Relative width below which the seven pivot levels are treated as "collapsed"
# onto a single point: when (max_level - min_level) / |pivot| is under this, the
# levels span less than ~0.02% of price and cannot define a usable entry / stop /
# target geometry. This is the degenerate-input signature produced when the pivot
# period has too few (or too flat) candles — e.g. a sparsely-backfilled spot
# index intraday series — which the Rust SR_Engine also flags via
# ``ordering_exception``.
_SR_COLLAPSE_REL_WIDTH = 2e-4


def _sr_is_finite_number(v) -> bool:
    """True for a finite real number; ``bool`` is excluded (repo convention)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _sr_is_degenerate(res: dict) -> tuple[bool, str]:
    """Detect an unusable support/resistance result (returns (degenerate, reason)).

    A result is degenerate when either the Rust SR_Engine flagged an
    ``ordering_exception`` (the canonical S3<=S2<=S1<=pivot<=R1<=R2<=R3 ordering
    could not hold), or all seven levels are finite but collapsed into a
    negligible band around the pivot (the near-zero-range signature of a
    sparse/flat candle window). In both cases the levels cannot be used for clean
    entry / stop / target placement, so the tool degrades to an honest
    Unavailable_Marker rather than handing back collapsed numbers. Never raises.
    """
    if not isinstance(res, dict):
        return False, ""
    if res.get("ordering_exception"):
        return True, (
            "support/resistance ordering_exception: the computed levels could not "
            "satisfy the canonical S3<=S2<=S1<=pivot<=R1<=R2<=R3 ordering "
            "(degenerate/near-flat candle range) and are not usable for entry, "
            "stop, or target placement"
        )
    levels = []
    for field in _SR_REQUIRED_FIELDS:
        v = res.get(field)
        if not _sr_is_finite_number(v):
            # A missing/non-finite required level is a different failure mode that
            # the contract validator already surfaces; do not mislabel it collapsed.
            return False, ""
        levels.append(float(v))
    lo, hi = min(levels), max(levels)
    pivot = res.get("pivot")
    denom = abs(float(pivot)) if (_sr_is_finite_number(pivot) and pivot != 0) else max(abs(hi), abs(lo), 1.0)
    if (hi - lo) / denom < _SR_COLLAPSE_REL_WIDTH:
        return True, (
            "support/resistance levels collapsed onto a single point "
            f"(all seven levels span less than {_SR_COLLAPSE_REL_WIDTH:.2%} of price "
            "around the pivot), the degenerate signature of too few / too flat "
            "candles — not usable for entry, stop, or target placement"
        )
    return False, ""


def _sr_unavailable(symbol, timeframe, reason: str, raw: dict) -> dict:
    """Build a get_support_resistance Unavailable_Marker.

    Recognized as an honest, non-fatal marker by ``_has_honest_marker`` so
    ``validate_contract`` passes it through unchanged. The unusable raw levels are
    preserved under ``raw_levels`` for glass-box diagnostics but MUST NOT be used
    as tradeable levels — the ``unavailable`` flag is the operative signal.
    """
    marker = {
        "symbol": symbol,
        "timeframe": timeframe,
        "unavailable": True,
        "reason": reason,
    }
    if isinstance(raw, dict):
        marker["raw_levels"] = {
            k: raw.get(k)
            for k in (*_SR_REQUIRED_FIELDS, "ordering_exception", "recent_high", "recent_low")
            if k in raw
        }
    return marker


@tool
def get_support_resistance(symbol: str, timeframe: str = "1d") -> dict:
    """
    Identifies exact support and resistance liquidity zones for the specified trading symbol.
    Returns the Pivot Point, support levels (S1, S2, S3), and resistance levels (R1, R2, R3)
    computed by the authoritative Rust SR_Engine from the same candle source used for every
    other indicator, so the levels stay consistent across the system. Use this to determine
    valid placement for entry price, stop loss, and take profit targets.

    For intraday timeframes, the engine additionally returns the Opening Range high/low and a
    daily macro pivot — key micro-levels for day traders.

    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        timeframe (str): Timeframe for pivot calculation (e.g., "5m", "15m", "1h", "1d").
                         Default "1d" for macro levels. Use shorter timeframes for intraday S/R.

    Returns:
        dict: Authoritative support/resistance levels with keys: pivot, s1, s2, s3, r1, r2, r3,
              recent_high, recent_low. Intraday timeframes also include opening_range_high,
              opening_range_low, and daily_pivot. When the computed levels are unusable —
              either the Rust engine flags an ordering_exception (the canonical
              S3≤S2≤S1≤pivot≤R1≤R2≤R3 ordering could not hold) or the levels collapse onto a
              single point around the pivot (the degenerate signature of too few / too flat
              candles, common on a sparsely-backfilled spot index intraday series) — the tool
              instead returns an Unavailable_Marker {"unavailable": true, "reason": ...} with
              the unusable levels preserved under "raw_levels" for diagnostics only. Treat an
              unavailable result as a missing, non-blocking input; do NOT place a trade against
              the raw_levels.
    """
    print(f"\n[Tool Call] >>> get_support_resistance: symbol={symbol}, timeframe={timeframe}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_support_resistance",
            json={"symbol": symbol, "timeframe": timeframe},
            timeout=10.0
        )
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        res = response.json()
        # Degrade an unusable result to an honest Unavailable_Marker rather than
        # handing back collapsed levels the agent cannot place a trade against.
        # This covers a Rust-flagged ordering_exception AND the near-zero-range
        # collapse produced by a sparse/flat candle window (common on a spot index
        # intraday series that has not been backfilled). The agent then treats S/R
        # as a clean missing input — exactly like regime / relative strength — and
        # proceeds on the remaining evidence instead of reasoning against garbage.
        if isinstance(res, dict) and not _has_honest_marker(res):
            degenerate, reason = _sr_is_degenerate(res)
            if degenerate:
                print(f"[Tool Success] <<< get_support_resistance: symbol={symbol}, timeframe={timeframe}, unavailable ({reason})")
                return _sr_unavailable(symbol, timeframe, reason, res)
        print(f"[Tool Success] <<< get_support_resistance: symbol={symbol}, timeframe={timeframe}, pivot={res.get('pivot')}, S1={res.get('s1')}, R1={res.get('r1')}")
        return validate_contract("get_support_resistance", res)
    except Exception as e:
        print(f"[Tool Error] <<< get_support_resistance FAIL: {str(e)}")
        return {"error": f"Failed to compute support/resistance: {str(e)}"}

@tool
def get_news_context(symbol: str) -> dict:
    """
    Retrieves the latest news headlines and a directional sentiment classification for the
    specified trading symbol, produced by the dedicated Rust-proxied Sentiment_Service rather
    than naive keyword counting. Use this to evaluate catalyst sentiment and micro-news impact
    when volatility is high.

    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").

    Returns:
        dict: Recent headlines plus a directional sentiment label. When the Sentiment_Service
              is unavailable, returns {"sentiment_summary": "Unavailable", ...} with no
              fabricated classification — treat that as a missing input, not a blocker.
    """
    print(f"\n[Tool Call] >>> get_news_context: symbol={symbol}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_news_context",
            json={"symbol": symbol},
            timeout=15.0
        )
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        res = response.json()
        print(f"[Tool Success] <<< get_news_context: symbol={symbol}, sentiment={res.get('sentiment_summary')}")
        return validate_contract("get_news_context", res)
    except Exception as e:
        print(f"[Tool Warning] <<< get_news_context FAIL: {str(e)}")
        # Honest abstention over fabrication (R10.3/R10.4): return the explicit
        # unavailable marker so the agent treats sentiment as a missing input
        # and does not block a decision on its absence.
        return {
            "symbol": symbol,
            "headlines": [],
            "sentiment_summary": "Unavailable",
            "error": f"Failed to fetch news context from sentiment service: {str(e)}"
        }


@tool
def get_prediction(symbol: str, timeframe: str = "1d") -> dict:
    """
    Obtains a forward price projection for the specified symbol and timeframe from the Rust
    Predictive_Engine (linear-OLS forecast). Use this during directional analysis to inform
    and cross-check your directional bias; when the projection conflicts with your bias, state
    the conflict in setup_validation.

    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        timeframe (str): The timeframe to project (e.g., "5m", "15m", "1h", "4h", "1d").
                         Default "1d".

    Returns:
        dict: Forward projection with keys projected_direction ("Up" | "Down" | "Flat"),
              projected_value (float), and confidence (0.0-1.0). When the engine cannot
              produce a forecast, returns {"unavailable": true, "reason": ...} — treat that
              as a missing input and proceed with the remaining analysis.
    """
    print(f"\n[Tool Call] >>> get_prediction: symbol={symbol}, timeframe={timeframe}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_prediction",
            json={"symbol": symbol, "timeframe": timeframe},
            timeout=15.0
        )
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        res = response.json()
        if res.get("unavailable"):
            print(f"[Tool Success] <<< get_prediction: symbol={symbol}, projection unavailable ({res.get('reason')})")
        else:
            print(f"[Tool Success] <<< get_prediction: symbol={symbol}, direction={res.get('projected_direction')}, value={res.get('projected_value')}, confidence={res.get('confidence')}")
        return validate_contract("get_prediction", res)
    except Exception as e:
        print(f"[Tool Warning] <<< get_prediction FAIL: {str(e)}")
        # Graceful degradation (R12.4): return an explicit unavailable marker so
        # the agent proceeds with the remaining inputs and notes the projection
        # as unavailable rather than receiving a fabricated forecast.
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "unavailable": True,
            "reason": f"Failed to fetch prediction from predictive engine: {str(e)}"
        }


def _regime_unavailable(symbol, timeframe, reason: str) -> dict:
    """Build a get_market_regime Unavailable_Marker (regime's marker shape).

    Mirrors ``regime._unavailable``: it carries the symbol/timeframe context,
    the ``unavailable: true`` flag, and a ``reason`` citing the cause, and it
    *omits* trend_state / volatility_state / favorability entirely — an
    unavailable regime is a missing optional input, never a fabricated label
    (AD-4, Requirements 4.3, 4.6). Recognized as an honest, non-fatal marker by
    ``_has_honest_marker`` so ``validate_contract`` passes it through unchanged.
    """
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "unavailable": True,
        "reason": reason,
    }


@tool
def get_market_regime(symbol: str, timeframe: str) -> dict:
    """
    Classify the current market regime (trend + volatility) for a symbol/timeframe.

    Use this BEFORE committing a directional (BUY/SELL) setup to gauge whether the
    market currently favors trend/momentum trades. The regime is GUIDANCE only — it
    never generates a trade, never blocks one, and never overrides your decision.
    When the regime is unfavorable for the proposed setup type, bias toward HOLD,
    lower conviction, or waiting; when it is unavailable, proceed with the remaining
    analysis and note it as unavailable.

    The classifier is pure math over the same authoritative OHLCV candles every
    other tool uses (fetched from the Rust Tool_Server). Valid timeframes:
    '1m', '5m', '10m', '15m', '1h', '4h', '1d'.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").

    Returns:
        dict: A Regime_Label with:
              - trend_state ("trending" | "ranging" | "transitional")
              - volatility_state ("low" | "normal" | "high")
              - favorability ("favorable" | "unfavorable" | "neutral")
              - measures: directional_strength, choppiness, efficiency_ratio,
                atr_percentile, bb_width (each a finite number or null)
              When the regime cannot be computed (retrieval failure/timeout,
              insufficient data, or any processing error) it returns an
              Unavailable_Marker {"unavailable": true, "reason": ...} with NO
              trend/volatility/favorability — treat that as a missing, non-blocking
              input. Never raises.
    """
    print(f"\n[Tool Call] >>> get_market_regime: symbol={symbol}, timeframe={timeframe}")
    try:
        # 1. Validate arguments — empty/whitespace symbol or unsupported timeframe
        #    is a structured error result (NOT an exception, R3.3).
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_market_regime: empty/whitespace symbol")
            return {
                "error": "get_market_regime requires a non-empty symbol",
            }
        if timeframe not in SUPPORTED_TIMEFRAMES:
            print(f"[Tool Error] <<< get_market_regime: unsupported timeframe '{timeframe}'")
            return {
                "error": (
                    f"get_market_regime received unsupported timeframe '{timeframe}'; "
                    f"supported timeframes are {sorted(SUPPORTED_TIMEFRAMES)}"
                ),
            }

        # 2. Resolve thresholds (single source of truth; never raises).
        config = regime.resolve_regime_config()

        # 3. Fetch candles from the authoritative Rust Tool_Server, exactly like
        #    journal.py / backtest.py. Request enough candles to cover the largest
        #    lookback AND the minimum-candle gate, plus a margin (the percentile
        #    window) so excluding any non-finite candles still leaves enough.
        required = max(config.min_candles, config.largest_lookback)
        limit = required + config.vol_pctl_window
        try:
            response = httpx.post(
                f"{RUST_SERVER_URL}/tools/get_candles",
                json={"symbol": symbol, "timeframe": timeframe, "limit": limit},
                timeout=10.0,
            )
            response.raise_for_status()
            candles = response.json()
        except Exception as fetch_exc:
            # Retrieval timeout / failure -> Unavailable_Marker citing the cause
            # (R4.1). NEVER propagate the exception into the agent loop.
            print(f"[Tool Warning] <<< get_market_regime: candle retrieval failed: {fetch_exc}")
            return _regime_unavailable(
                symbol,
                timeframe,
                f"candle retrieval failed: {fetch_exc}",
            )

        # The candle payload may itself be an error list (get_candles' error path
        # returns ``[{"error": ...}]``); treat a non-list / error payload as a
        # retrieval failure -> Unavailable_Marker.
        if not isinstance(candles, list) or (
            candles and isinstance(candles[0], dict) and "error" in candles[0]
        ):
            reason = "candle retrieval returned no usable data"
            if isinstance(candles, list) and candles and isinstance(candles[0], dict):
                reason = f"candle retrieval failed: {candles[0].get('error')}"
            print(f"[Tool Warning] <<< get_market_regime: {reason}")
            return _regime_unavailable(symbol, timeframe, reason)

        # 4. Classify via the pure Regime_Classifier. It returns either a
        #    Regime_Label or an Unavailable_Marker, and never raises.
        result = regime.classify_regime(
            candles, config, symbol=symbol, timeframe=timeframe
        )

        # 5. Re-validate against the Tool_Result_Contract on receipt (AD-3) and
        #    return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_market_regime", result)
        if validated.get("unavailable"):
            print(f"[Tool Success] <<< get_market_regime: symbol={symbol}, unavailable ({validated.get('reason')})")
        else:
            print(
                f"[Tool Success] <<< get_market_regime: symbol={symbol}, "
                f"trend={validated.get('trend_state')}, vol={validated.get('volatility_state')}, "
                f"favorability={validated.get('favorability')}"
            )
        return validated
    except Exception as e:
        # Defensive catch-all: any processing error degrades to an honest
        # Unavailable_Marker rather than raising into the agent loop (R4.5).
        print(f"[Tool Warning] <<< get_market_regime FAIL: {str(e)}")
        return _regime_unavailable(
            symbol if isinstance(symbol, str) else None,
            timeframe if isinstance(timeframe, str) else None,
            f"regime processing error: {str(e)}",
        )


def _relative_strength_unavailable(symbol, timeframe, benchmark, reason: str) -> dict:
    """Build a get_relative_strength Unavailable_Marker (rs's marker shape).

    Mirrors ``_regime_unavailable`` / ``rs._rs_unavailable``: it carries the
    symbol / timeframe / benchmark context, the ``unavailable: true`` flag, and a
    ``reason`` citing the cause, and it *omits* index_direction /
    relative_strength_state / alignment entirely — an unavailable relative
    strength is a missing optional input, never a fabricated label (AD-4,
    Requirements 5.1, 5.3, 5.5). Recognized as an honest, non-fatal marker by
    ``_has_honest_marker`` so ``validate_contract`` passes it through unchanged.
    """
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "benchmark": benchmark,
        "unavailable": True,
        "reason": reason,
    }


def _fetch_candles_for_rs(symbol, timeframe, limit):
    """Fetch candles for a single series from the Rust Tool_Server for RS.

    Returns ``(candles, None)`` on success or ``(None, reason)`` when the
    retrieval timed out / failed or the payload was a non-list / error payload.
    Never raises — a failure is reported as a ``reason`` string the caller turns
    into an Unavailable_Marker (Requirements 4.4, 5.1).
    """
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "timeframe": timeframe, "limit": limit},
            timeout=10.0,
        )
        response.raise_for_status()
        candles = response.json()
    except Exception as fetch_exc:
        return None, f"candle retrieval failed: {fetch_exc}"

    # The candle payload may itself be an error list (get_candles' error path
    # returns ``[{"error": ...}]``); treat a non-list / error payload as a
    # retrieval failure.
    if not isinstance(candles, list) or (
        candles and isinstance(candles[0], dict) and "error" in candles[0]
    ):
        reason = "candle retrieval returned no usable data"
        if isinstance(candles, list) and candles and isinstance(candles[0], dict):
            reason = f"candle retrieval failed: {candles[0].get('error')}"
        return None, reason

    return candles, None


@tool
def get_relative_strength(symbol: str, timeframe: str, benchmark: str = "",
                          proposed_direction: str = "") -> dict:
    """
    Measure a symbol's relative strength versus its benchmark index and the
    alignment of a proposed trade direction with the index / relative-strength
    context.

    Use this BEFORE committing a directional (BUY/SELL) setup to check whether the
    trade goes WITH the market (a leader while the index is up, or a laggard while
    the index is down) or FIGHTS it. The relative strength is GUIDANCE only — it
    never generates a trade, never blocks one, and never overrides your decision.
    When the proposed trade is misaligned with the index, bias toward lower
    conviction, waiting, or HOLD; when it is unavailable, proceed with the
    remaining analysis and note it as unavailable.

    The calculator is pure math over the same authoritative OHLCV candles every
    other tool uses (both the symbol candles and the Benchmark_Index candles are
    fetched from the Rust Tool_Server). The benchmark is resolved from the symbol
    via the Benchmark_Map unless an explicit benchmark is supplied. Valid
    timeframes: '1m', '5m', '10m', '15m', '1h', '4h', '1d'.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        benchmark (str): Optional explicit Benchmark_Index; when empty the
                         benchmark is resolved from the symbol via the Benchmark_Map.
        proposed_direction (str): Optional proposed trade direction ("BUY" / "SELL");
                         when empty, no direction is assumed and alignment is neutral.

    Returns:
        dict: A Relative_Strength_Label with:
              - index_direction ("up" | "down" | "flat")
              - relative_strength_state ("leader" | "inline" | "laggard")
              - alignment ("aligned" | "misaligned" | "neutral")
              - benchmark (the resolved Benchmark_Index)
              - measures: rs_ratio, rs_ratio_slope, relative_return, correlation,
                beta (each a finite number or null)
              When relative strength cannot be computed (missing benchmark candles,
              retrieval failure/timeout, insufficient data, or any processing error)
              it returns an Unavailable_Marker {"unavailable": true, "reason": ...}
              with NO index_direction / relative_strength_state / alignment — treat
              that as a missing, non-blocking input. Never raises.
    """
    print(
        f"\n[Tool Call] >>> get_relative_strength: symbol={symbol}, "
        f"timeframe={timeframe}, benchmark={benchmark!r}, direction={proposed_direction!r}"
    )
    try:
        # 1. Validate arguments — empty/whitespace symbol or unsupported timeframe
        #    is a structured error result (NOT an exception, R4.3).
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_relative_strength: empty/whitespace symbol")
            return {
                "error": "get_relative_strength requires a non-empty symbol",
            }
        if timeframe not in SUPPORTED_TIMEFRAMES:
            print(
                f"[Tool Error] <<< get_relative_strength: unsupported timeframe '{timeframe}'"
            )
            return {
                "error": (
                    f"get_relative_strength received unsupported timeframe '{timeframe}'; "
                    f"supported timeframes are {sorted(SUPPORTED_TIMEFRAMES)}"
                ),
            }

        # 2. Resolve the Benchmark_Index. An explicit non-empty benchmark wins;
        #    otherwise the Benchmark_Map / documented default resolves it (R4.2,
        #    R2.x). resolve_benchmark never raises.
        resolved_benchmark = rs.resolve_benchmark(symbol, benchmark)

        # A symbol cannot be its own benchmark — fetching identical series would
        # fabricate a degenerate "relative" strength. Treat that as a missing
        # benchmark and degrade honestly (R2.4).
        if (
            isinstance(resolved_benchmark, str)
            and resolved_benchmark.strip().upper() == symbol.strip().upper()
        ):
            reason = (
                f"no distinct benchmark available for symbol '{symbol}' "
                f"(resolved benchmark '{resolved_benchmark}' equals the symbol)"
            )
            print(f"[Tool Warning] <<< get_relative_strength: {reason}")
            return _relative_strength_unavailable(
                symbol, timeframe, resolved_benchmark, reason
            )

        # 3. Resolve parameters (single source of truth; never raises).
        config = rs.resolve_rs_config()

        # 4. Fetch BOTH the symbol candles and the Benchmark_Index candles from
        #    the authoritative Rust Tool_Server (R4.4). Request enough candles to
        #    cover the largest single-measure lookback AND the minimum-candle
        #    gate, plus a margin so excluding any non-finite / non-common candles
        #    still leaves enough aligned candles.
        required = max(config.min_candles, config.largest_lookback)
        limit = required + RS_FETCH_MARGIN

        sym_candles, sym_reason = _fetch_candles_for_rs(symbol, timeframe, limit)
        if sym_candles is None:
            print(f"[Tool Warning] <<< get_relative_strength: symbol {sym_reason}")
            return _relative_strength_unavailable(
                symbol, timeframe, resolved_benchmark, f"symbol {sym_reason}"
            )

        bench_candles, bench_reason = _fetch_candles_for_rs(
            resolved_benchmark, timeframe, limit
        )
        if bench_candles is None:
            # Missing/unavailable benchmark candles -> Unavailable_Marker that
            # NAMES the benchmark whose candles could not be retrieved (R2.4, R5.1).
            reason = f"benchmark '{resolved_benchmark}' {bench_reason}"
            print(f"[Tool Warning] <<< get_relative_strength: {reason}")
            return _relative_strength_unavailable(
                symbol, timeframe, resolved_benchmark, reason
            )

        # 5. Classify via the pure Relative_Strength_Calculator. An empty
        #    proposed_direction means "no direction" -> pass None so alignment is
        #    neutral (R1.9). classify_relative_strength returns either a
        #    Relative_Strength_Label or an Unavailable_Marker and never raises.
        direction = (
            proposed_direction.strip()
            if isinstance(proposed_direction, str) and proposed_direction.strip()
            else None
        )
        result = rs.classify_relative_strength(
            sym_candles,
            bench_candles,
            config,
            proposed_direction=direction,
            symbol=symbol,
            benchmark=resolved_benchmark,
            timeframe=timeframe,
        )

        # 6. Re-validate against the Tool_Result_Contract on receipt (AD-3) and
        #    return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_relative_strength", result)
        if validated.get("unavailable"):
            print(
                f"[Tool Success] <<< get_relative_strength: symbol={symbol}, "
                f"benchmark={resolved_benchmark}, unavailable ({validated.get('reason')})"
            )
        else:
            print(
                f"[Tool Success] <<< get_relative_strength: symbol={symbol}, "
                f"benchmark={resolved_benchmark}, index={validated.get('index_direction')}, "
                f"state={validated.get('relative_strength_state')}, "
                f"alignment={validated.get('alignment')}"
            )
        return validated
    except Exception as e:
        # Defensive catch-all: any processing error degrades to an honest
        # Unavailable_Marker rather than raising into the agent loop (R5.5).
        print(f"[Tool Warning] <<< get_relative_strength FAIL: {str(e)}")
        return _relative_strength_unavailable(
            symbol if isinstance(symbol, str) else None,
            timeframe if isinstance(timeframe, str) else None,
            benchmark if isinstance(benchmark, str) and benchmark.strip() else None,
            f"relative-strength processing error: {str(e)}",
        )


def _order_flow_unavailable(symbol, timeframe, reason: str) -> dict:
    """Build a get_order_flow Unavailable_Marker (the order-flow marker shape).

    Mirrors ``_relative_strength_unavailable`` / ``_regime_unavailable`` /
    ``order_flow._order_flow_unavailable``: it carries the symbol / timeframe
    context, the ``unavailable: true`` flag, and a ``reason`` citing the cause,
    and it *omits* ``order_flow_state`` / ``alignment`` entirely — an unavailable
    order flow is a missing optional input, never a fabricated label (AD-5,
    Requirements 6.3, 14.6). Recognized as an honest, non-fatal marker by
    ``_has_honest_marker`` so ``validate_contract`` passes it through unchanged
    (Requirement 5.8).
    """
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "unavailable": True,
        "reason": reason,
    }


def _read_live_ticks(symbol, limit):
    """Read up to ``limit`` recent ticks for ``symbol`` from the Live_Ticks_Source.

    Queries the ``live_ticks`` table via the QuestDB HTTP ``/exec`` API (the same
    API ``backtest.py`` uses for the historical archive), most-recent-first, then
    REVERSES the rows to chronological oldest-first order — the order
    ``order_flow.compute_tick_ofi`` expects (mirroring the Rust
    ``compute_order_flow_imbalance`` ``ORDER BY timestamp DESC ... .rev()``).

    Each row maps to the tick dict shape ``order_flow._parse_tick`` expects:
    ``{last_price, volume, best_bid, best_ask}`` (``volume`` is the day's
    cumulative traded volume, matching the ``live_ticks.volume`` column).

    Returns a list of tick dicts on success, or ``[]`` on ANY failure
    (unreachable server, query error, no rows, malformed payload). The caller
    treats ``[]`` as "tick layer unavailable" (R6.1); this helper NEVER raises
    into the tool body (R6.5).
    """
    try:
        sym = str(symbol).replace("'", "''")  # escape for the SQL string literal
        query = (
            "SELECT last_traded_price, volume, best_bid, best_ask "
            f"FROM live_ticks WHERE symbol='{sym}' "
            f"ORDER BY timestamp DESC LIMIT {int(limit)}"
        )
        r = httpx.get(
            f"{QUESTDB_HTTP_URL}/exec", params={"query": query}, timeout=10.0
        )
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        print(f"[Tool Warning] _read_live_ticks: tick retrieval failed: {exc}")
        return []

    if not isinstance(body, dict) or body.get("error"):
        return []
    dataset = body.get("dataset")
    if not isinstance(dataset, list) or not dataset:
        return []

    ticks = []
    # The DESC query returns most-recent-first; reverse to oldest-first so the
    # cumulative-volume deltas the Tick_OFI consumes run forward in time.
    for row in reversed(dataset):
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        ticks.append({
            "last_price": row[0],
            "volume": row[1],
            "best_bid": row[2],
            "best_ask": row[3],
        })
    return ticks


@tool
def get_order_flow(symbol: str, timeframe: str, proposed_direction: str = "") -> dict:
    """
    Read the tape: classify net order-flow pressure (buying / selling / balanced)
    and the alignment of a proposed trade direction with that flow.

    Use this BEFORE committing a directional (BUY/SELL) setup to check WHO is
    pressing the trade — buyers or sellers — from candle-derived order-flow
    proxies (a per-candle delta proxy, a cumulative-volume-delta proxy, up/down
    volume, and a buying-pressure ratio) and, when the live tick stream is
    available, a true tick-based Order Flow Imbalance (Tick_OFI). Order flow is
    GUIDANCE only — it never generates a trade, never blocks one, and never
    overrides your decision. When the proposed trade is misaligned with the flow
    (a BUY into net selling, or a SELL into net buying), bias toward lower
    conviction, waiting, or HOLD; when it is unavailable, proceed with the
    remaining analysis and note it as unavailable.

    The calculator is pure math over the same authoritative OHLCV candles every
    other tool uses (the symbol candles are fetched from the Rust Tool_Server).
    The Tick_OFI is read from the `live_ticks` source; when that stream is absent
    (market closed, no rows, too few ticks) the Tick_OFI is honestly marked
    unavailable and only the candle-derived proxy layer is used — never a
    fabricated neutral value. Valid timeframes: '1m', '5m', '10m', '15m', '1h',
    '4h', '1d'.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        proposed_direction (str): Optional proposed trade direction ("BUY" / "SELL");
                         when empty, no direction is assumed and alignment is neutral.

    Returns:
        dict: An Order_Flow_Label with:
              - order_flow_state ("buying" | "selling" | "balanced")
              - alignment ("aligned" | "misaligned" | "neutral")
              - measures: candle_delta, cvd_proxy, up_volume, down_volume,
                buying_pressure_ratio (each a finite number or null)
              - tick_ofi (a finite number in [-1.0, 1.0] or null when unavailable)
              - live_tick_contributed (bool — whether live ticks contributed)
              When order flow cannot be computed (insufficient/failed candle
              retrieval, all-null proxies with no tick layer, or any processing
              error) it returns an Unavailable_Marker {"unavailable": true,
              "reason": ...} with NO order_flow_state / alignment — treat that as
              a missing, non-blocking input. Never raises.
    """
    print(
        f"\n[Tool Call] >>> get_order_flow: symbol={symbol}, "
        f"timeframe={timeframe}, direction={proposed_direction!r}"
    )
    try:
        # 1. Validate arguments — empty/whitespace symbol or unsupported timeframe
        #    is a structured error result (NOT an exception, R5.3).
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_order_flow: empty/whitespace symbol")
            return {"error": "get_order_flow requires a non-empty symbol"}
        if timeframe not in SUPPORTED_TIMEFRAMES:
            print(
                f"[Tool Error] <<< get_order_flow: unsupported timeframe '{timeframe}'"
            )
            return {
                "error": (
                    f"get_order_flow received unsupported timeframe '{timeframe}'; "
                    f"supported timeframes are {sorted(SUPPORTED_TIMEFRAMES)}"
                ),
            }

        # 2. Resolve parameters (single source of truth; never raises).
        config = order_flow.resolve_order_flow_config()

        # 3. Fetch the symbol candles from the authoritative Rust Tool_Server for
        #    the proxy layer. Request enough candles to cover the largest single-
        #    measure lookback AND the minimum-candle gate, plus a margin so
        #    excluding any non-finite candles still leaves enough to classify.
        required = max(config.min_candles, config.largest_lookback)
        limit = required + RS_FETCH_MARGIN
        candles, candle_reason = _fetch_candles_for_rs(symbol, timeframe, limit)
        if candles is None:
            # Candle retrieval failed/timed out -> Unavailable_Marker citing the
            # cause (R6.2). The proxy layer is the floor; without it there is no
            # usable order flow at all.
            print(f"[Tool Warning] <<< get_order_flow: {candle_reason}")
            return _order_flow_unavailable(symbol, timeframe, candle_reason)

        # 4. Attempt to read recent ticks for the symbol from the Live_Ticks_Source
        #    (Tick_OFI layer). Any failure / no rows yields [] so the Tick_OFI is
        #    simply unavailable and the proxy layer still classifies (R6.1, R6.6).
        ticks = _read_live_ticks(symbol, OF_TICK_FETCH_LIMIT)

        # 5. Classify via the pure Order_Flow_Calculator. An empty
        #    proposed_direction means "no direction" -> pass None so alignment is
        #    neutral (R3.4). classify_order_flow returns either an Order_Flow_Label
        #    or an Unavailable_Marker and never raises.
        direction = (
            proposed_direction.strip()
            if isinstance(proposed_direction, str) and proposed_direction.strip()
            else None
        )
        result = order_flow.classify_order_flow(
            candles,
            ticks,
            config,
            proposed_direction=direction,
            symbol=symbol,
            timeframe=timeframe,
        )

        # 6. Re-validate against the Tool_Result_Contract on receipt (AD-3) and
        #    return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_order_flow", result)
        if validated.get("unavailable"):
            print(
                f"[Tool Success] <<< get_order_flow: symbol={symbol}, "
                f"unavailable ({validated.get('reason')})"
            )
        elif "error" in validated:
            print(f"[Tool Warning] <<< get_order_flow: {validated.get('error')}")
        else:
            print(
                f"[Tool Success] <<< get_order_flow: symbol={symbol}, "
                f"state={validated.get('order_flow_state')}, "
                f"alignment={validated.get('alignment')}, "
                f"tick_ofi={validated.get('tick_ofi')}, "
                f"live_tick_contributed={validated.get('live_tick_contributed')}"
            )
        return validated
    except Exception as e:
        # Defensive catch-all: any processing error degrades to an honest
        # Unavailable_Marker rather than raising into the agent loop (R6.5).
        print(f"[Tool Warning] <<< get_order_flow FAIL: {str(e)}")
        return _order_flow_unavailable(
            symbol if isinstance(symbol, str) else None,
            timeframe if isinstance(timeframe, str) else None,
            f"order-flow processing error: {str(e)}",
        )


def _options_unavailable(symbol, underlying, chain_context, reason: str) -> dict:
    """Build a get_options_analytics Unavailable_Marker (the options marker shape).

    Mirrors ``_order_flow_unavailable`` / ``_relative_strength_unavailable`` /
    ``options._options_unavailable``: it carries the original ``symbol``, the
    resolved ``underlying`` and ``chain_context``, the ``unavailable: true`` flag,
    and a ``reason`` citing the cause, and it *omits* ``options_bias_state`` /
    ``alignment`` entirely — an unavailable options context is a missing optional
    input, never a fabricated label (AD-6, Requirements 3.2, 3.4). Recognized as
    an honest, non-fatal marker by ``_has_honest_marker`` so ``validate_contract``
    passes it through unchanged (Requirement 2.6).
    """
    return {
        "symbol": symbol,
        "underlying": underlying,
        "chain_context": chain_context,
        "unavailable": True,
        "reason": reason,
    }


@tool
def get_options_analytics(symbol: str, expiry: str = "",
                          proposed_direction: str = "",
                          own_chain: bool = False) -> dict:
    """
    Read the options-positioning picture for a symbol — PCR (OI and volume), max
    pain, aggregate OI buildup, OI-wall support/resistance, IV skew, futures basis
    — plus a derived Options_Bias (bullish / bearish / neutral) and its Alignment
    with a proposed trade direction.

    Use this BEFORE committing a directional (BUY/SELL) setup to check whether the
    trade goes WITH institutional positioning or FIGHTS heavy OI walls, max-pain
    pinning, or PCR extremes. Options context is GUIDANCE only — it never generates
    a trade, never blocks one, and never overrides your decision. When the proposed
    trade is misaligned with options positioning, bias toward lower conviction,
    waiting, or HOLD; when it is unavailable, proceed with the remaining analysis
    and note it as unavailable.

    Chain resolution:
      * An index Underlying (NIFTY 50, BANKNIFTY) always analyzes its OWN chain
        (chain_context="own-chain").
      * A non-index symbol (a stock) defaults to its benchmark index chain as
        broad-market options context (chain_context="broad-market"), clearly
        labelled as index-level rather than stock-specific.
      * When `own_chain=True` (set this in the F&O workspace when you want the
        STOCK's own options), a non-index symbol analyzes its OWN option chain
        instead of the benchmark proxy — falling back to the benchmark chain only
        if the stock has no chain snapshot, so you still get index-level context
        rather than nothing.

    The options analytics math lives entirely in the F2 engine and is consumed
    verbatim; the bias is a pure threshold vote over those analytics.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE", "BANKNIFTY").
        expiry (str): Optional expiry as an ISO date "YYYY-MM-DD" (e.g.
                      "2026-07-30"); when empty the engine's nearest available
                      expiry for the resolved chain is used. In the F&O workspace,
                      pass the exact expiry the user has selected.
        proposed_direction (str): Optional proposed trade direction ("BUY" / "SELL");
                      when empty, no direction is assumed and alignment is neutral.
        own_chain (bool): When True, analyze a non-index symbol's OWN option chain
                      (stock-specific) instead of the broad-market benchmark proxy.
                      Set this in the F&O workspace. Ignored for index underlyings
                      (which always use their own chain). Defaults to False.

    Returns:
        dict: An options result carrying pcr_oi, pcr_volume, max_pain, oi_buildup,
              oi_walls, iv_skew, futures_basis, underlying/expiry/spot, the derived
              options_bias_state ("bullish"/"bearish"/"neutral"), the alignment
              ("aligned"/"misaligned"/"neutral"), the driving signals, and the
              chain_context used — or an Unavailable_Marker {"unavailable": true,
              "reason": ...} with options_bias_state / alignment OMITTED. Treat an
              unavailable result as a missing, non-blocking input. Never raises.
    """
    print(
        f"\n[Tool Call] >>> get_options_analytics: symbol={symbol}, "
        f"expiry={expiry!r}, direction={proposed_direction!r}, own_chain={own_chain}"
    )
    # Resolve the analyzed chain + label up-front so the defensive catch-all can
    # still report the chain context it had resolved (these stay None until
    # resolution succeeds).
    underlying = None
    chain_context = None
    try:
        # 1. Validate arguments — an empty/whitespace symbol is a structured error
        #    result (NOT an exception, Requirement 3 / R2.x). expiry and
        #    proposed_direction are optional strings.
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_options_analytics: empty/whitespace symbol")
            return {"error": "get_options_analytics requires a non-empty symbol"}

        # 2. Resolve the analyzed chain + label (Requirement 2.3).
        #    - An index Underlying always uses its OWN chain ("own-chain").
        #    - When own_chain=True (the F&O workspace analyzing a specific stock),
        #      a non-index symbol uses its OWN option chain ("own-chain"), with a
        #      fallback to the benchmark chain below if the stock has no snapshot.
        #    - Otherwise a non-index symbol resolves to its Benchmark_Index chain
        #      via rs.resolve_benchmark as broad-market options context
        #      ("broad-market"). The result always records chain_context, the
        #      resolved underlying, and the original symbol.
        sym_up = symbol.strip().upper()
        if sym_up in INDEX_UNDERLYINGS:
            underlying = symbol.strip()
            chain_context = "own-chain"
        elif own_chain:
            underlying = symbol.strip()
            chain_context = "own-chain"
        else:
            underlying = rs.resolve_benchmark(symbol)
            chain_context = "broad-market"

        # 3. Obtain the Options_Analytics_Result from the F2 engine, which owns ALL
        #    QuestDB chain / spot / future I/O and never raises (Requirement 2.4).
        #    An empty expiry means "nearest available expiry" — the engine treats a
        #    falsy expiry as the nearest chain.
        expiry_or_none = expiry.strip() if isinstance(expiry, str) else ""
        analytics = options.compute_options_analytics(underlying, expiry_or_none)

        # 3b. Own-chain fallback: if an own-chain analysis of a NON-index stock
        #     comes back unavailable (the stock's own chain has no snapshot — e.g.
        #     the stock is not F&O-subscribed), fall back to the broad-market
        #     benchmark chain so the agent still gets index-level positioning
        #     context instead of nothing. The result then honestly reports the
        #     broad-market chain_context and benchmark underlying it fell back to.
        if (
            own_chain
            and sym_up not in INDEX_UNDERLYINGS
            and isinstance(analytics, dict)
            and analytics.get("unavailable")
        ):
            fallback_underlying = rs.resolve_benchmark(symbol)
            if (
                isinstance(fallback_underlying, str)
                and fallback_underlying.strip()
                and fallback_underlying.strip().upper() != sym_up
            ):
                fb = options.compute_options_analytics(fallback_underlying, expiry_or_none)
                if isinstance(fb, dict) and not fb.get("unavailable"):
                    print(
                        f"[Tool Info] <<< get_options_analytics: own-chain for "
                        f"{symbol} unavailable; falling back to broad-market "
                        f"{fallback_underlying}"
                    )
                    underlying = fallback_underlying
                    chain_context = "broad-market"
                    analytics = fb

        # 4. Unavailable gate (Requirements 3.1, 3.2). When the engine returns an
        #    Unavailable_Marker (or a non-dict), pass it through as an options
        #    Unavailable_Marker carrying the chain context and reason, with
        #    options_bias_state / alignment OMITTED — never a fabricated bias.
        if not isinstance(analytics, dict) or analytics.get("unavailable") is True:
            reason = (
                analytics.get("reason")
                if isinstance(analytics, dict) and analytics.get("reason")
                else f"options analytics unavailable for {underlying}"
            )
            print(
                f"[Tool Success] <<< get_options_analytics: symbol={symbol}, "
                f"underlying={underlying}, unavailable ({reason})"
            )
            return _options_unavailable(symbol, underlying, chain_context, reason)

        # 5. Classify the bias via the pure Options_Bias_Classifier (Requirement
        #    2.4). Resolve the bias config once (single source of truth; never
        #    raises). An empty proposed_direction means "no direction" -> pass None
        #    so alignment is neutral. classify_options_bias never raises.
        config = options_bias.resolve_options_bias_config()
        direction = (
            proposed_direction.strip()
            if isinstance(proposed_direction, str) and proposed_direction.strip()
            else None
        )
        label = classify_options_bias(analytics, config, proposed_direction=direction)

        # 6. Merge the analytics fields, the bias fields, and the chain context
        #    into a single result (Requirement 2.5). The analytics are consumed
        #    verbatim — never recomputed.
        result = {
            # Analytics (verbatim from the F2 engine).
            "underlying": analytics.get("underlying", underlying),
            "expiry": analytics.get("expiry"),
            "spot": analytics.get("spot"),
            "pcr_oi": analytics.get("pcr_oi"),
            "pcr_volume": analytics.get("pcr_volume"),
            "max_pain": analytics.get("max_pain"),
            "oi_buildup": analytics.get("oi_buildup"),
            "oi_walls": analytics.get("oi_walls"),
            "iv_skew": analytics.get("iv_skew"),
            "futures_basis": analytics.get("futures_basis"),
            # Bias (from the classifier).
            "options_bias_state": label.get("options_bias_state"),
            "alignment": label.get("alignment"),
            "signals": label.get("signals"),
            # Chain context used (Requirement 2.3).
            "chain_context": chain_context,
        }

        # 7. Re-validate against the Tool_Result_Contract on receipt (AD-3, R2.6)
        #    and return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_options_analytics", result)
        if "error" in validated:
            print(f"[Tool Warning] <<< get_options_analytics: {validated.get('error')}")
        else:
            print(
                f"[Tool Success] <<< get_options_analytics: symbol={symbol}, "
                f"underlying={underlying}, chain_context={chain_context}, "
                f"state={validated.get('options_bias_state')}, "
                f"alignment={validated.get('alignment')}"
            )
        return validated
    except Exception as e:
        # 8. Defensive catch-all: any processing error degrades to an honest
        #    Unavailable_Marker rather than raising into the agent loop (R3.4).
        print(f"[Tool Warning] <<< get_options_analytics FAIL: {str(e)}")
        return _options_unavailable(
            symbol if isinstance(symbol, str) else None,
            underlying,
            chain_context,
            f"options processing error: {str(e)}",
        )


def _forecast_unavailable(symbol, timeframe, reason: str) -> dict:
    """Build a get_forecast Unavailable_Marker (the forecaster marker shape).

    Mirrors ``_relative_strength_unavailable`` / ``_order_flow_unavailable`` /
    ``forecaster._forecast_unavailable``: it carries the symbol / timeframe
    context, the ``unavailable: true`` flag, and a ``reason`` citing the cause,
    and it *omits* ``projected_direction`` / ``up_probability`` /
    ``expected_move_atr`` / ``forecast_confidence`` / ``forecast_alignment``
    entirely — an unavailable forecast is a missing optional input, never a
    fabricated label (AD-5, Requirements 6.3, 6.5). Recognized as an honest,
    non-fatal marker by ``_has_honest_marker`` so ``validate_contract`` passes it
    through unchanged.
    """
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "unavailable": True,
        "reason": reason,
    }


@tool
def get_forecast(symbol: str, timeframe: str, proposed_direction: str = "") -> dict:
    """
    Produce a volatility-aware, regime-conditioned probabilistic forward view for
    a symbol/timeframe — the agent's PRIMARY predictive cross-check.

    Use this BEFORE committing a directional (BUY/SELL) setup to check WHERE the
    market is probably going next and HOW confident that view is. The forecast is
    a volatility-scaled, regime-conditioned standardized drift turned into a
    projected direction (up / down / flat), an up-probability in [0.0, 1.0], an
    expected next-bar move in ATR units, a confidence in [0.0, 1.0], and the
    alignment of a proposed trade direction with the forecast. The forecast is
    GUIDANCE only — it never generates a trade, never blocks one, and never
    overrides your decision. When the proposed trade is misaligned with the
    forecast, or the up-probability does not support the direction, bias toward
    lower conviction, waiting, or HOLD; when it is unavailable, proceed with the
    remaining analysis and note it as unavailable.

    The forecaster is pure math over the same authoritative OHLCV candles every
    other tool uses (the symbol candles are fetched from the Rust Tool_Server),
    and it obtains the trend state from the same regime classifier rather than
    reimplementing regime math. Valid timeframes: '1m', '5m', '10m', '15m', '1h',
    '4h', '1d'.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        proposed_direction (str): Optional proposed trade direction ("BUY" / "SELL");
                         when empty, no direction is assumed and alignment is neutral.

    Returns:
        dict: A Forecast_Label with:
              - projected_direction ("up" | "down" | "flat")
              - up_probability (a finite number in [0.0, 1.0])
              - expected_move_atr (a finite number or null when ATR is unavailable)
              - forecast_confidence (a finite number in [0.0, 1.0])
              - forecast_alignment ("aligned" | "misaligned" | "neutral")
              - measures: drift, volatility, standardized_drift, atr (each a
                finite number or null)
              When the forecast cannot be computed (insufficient/failed candle
              retrieval, insufficient data, or any processing error) it returns an
              Unavailable_Marker {"unavailable": true, "reason": ...} with NO
              projected_direction / up_probability / expected_move_atr /
              forecast_confidence / forecast_alignment — treat that as a missing,
              non-blocking input. Never raises.
    """
    print(
        f"\n[Tool Call] >>> get_forecast: symbol={symbol}, "
        f"timeframe={timeframe}, direction={proposed_direction!r}"
    )
    try:
        # 1. Validate arguments — empty/whitespace symbol or unsupported timeframe
        #    is a structured error result (NOT an exception, R5.3).
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_forecast: empty/whitespace symbol")
            return {"error": "get_forecast requires a non-empty symbol"}
        if timeframe not in SUPPORTED_TIMEFRAMES:
            print(
                f"[Tool Error] <<< get_forecast: unsupported timeframe '{timeframe}'"
            )
            return {
                "error": (
                    f"get_forecast received unsupported timeframe '{timeframe}'; "
                    f"supported timeframes are {sorted(SUPPORTED_TIMEFRAMES)}"
                ),
            }

        # 2. Resolve parameters (single source of truth; never raises).
        config = forecaster.resolve_forecaster_config()

        # 3. Fetch the symbol candles from the authoritative Rust Tool_Server.
        #    Request enough candles to cover the largest single-estimate lookback
        #    AND the minimum-candle gate, plus a margin so excluding any
        #    non-finite candles still leaves enough to forecast.
        required = max(config.min_candles, config.largest_lookback)
        # The forecaster conditions its drift/volatility blend on the market
        # regime (regime.classify_regime over these SAME candles). The regime
        # classifier needs a larger window (its min-candle gate + percentile
        # window) than the forecaster's own estimates, so fetch enough for BOTH.
        # Otherwise the internal regime reads 'unavailable' — recorded as
        # regime_trend_state="unavailable" inside the forecast — even when the
        # standalone get_market_regime (which fetches the regime-sized window)
        # succeeds on the same symbol/timeframe.
        try:
            _regime_cfg = regime.resolve_regime_config()
            _regime_required = (
                max(_regime_cfg.min_candles, _regime_cfg.largest_lookback)
                + _regime_cfg.vol_pctl_window
            )
        except Exception:
            _regime_required = 0
        limit = max(required, _regime_required) + RS_FETCH_MARGIN
        candles, candle_reason = _fetch_candles_for_rs(symbol, timeframe, limit)
        if candles is None:
            # Candle retrieval failed/timed out -> Unavailable_Marker citing the
            # cause (R6.1). Without candles there is no usable forecast at all.
            print(f"[Tool Warning] <<< get_forecast: {candle_reason}")
            return _forecast_unavailable(symbol, timeframe, candle_reason)

        # 4. Classify via the pure Volatility_Forecaster. An empty
        #    proposed_direction means "no direction" -> pass None so alignment is
        #    neutral. forecast returns either a Forecast_Label or an
        #    Unavailable_Marker and never raises.
        result = forecaster.forecast(
            candles,
            config,
            proposed_direction=proposed_direction or None,
            symbol=symbol,
            timeframe=timeframe,
        )

        # 5. Re-validate against the Tool_Result_Contract on receipt (AD-3) and
        #    return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_forecast", result)
        if validated.get("unavailable"):
            print(
                f"[Tool Success] <<< get_forecast: symbol={symbol}, "
                f"unavailable ({validated.get('reason')})"
            )
        elif "error" in validated:
            print(f"[Tool Warning] <<< get_forecast: {validated.get('error')}")
        else:
            print(
                f"[Tool Success] <<< get_forecast: symbol={symbol}, "
                f"direction={validated.get('projected_direction')}, "
                f"up_probability={validated.get('up_probability')}, "
                f"confidence={validated.get('forecast_confidence')}, "
                f"alignment={validated.get('forecast_alignment')}"
            )
        return validated
    except Exception as e:
        # Defensive catch-all: any processing error degrades to an honest
        # Unavailable_Marker rather than raising into the agent loop (R6.5).
        print(f"[Tool Warning] <<< get_forecast FAIL: {str(e)}")
        return _forecast_unavailable(
            symbol if isinstance(symbol, str) else None,
            timeframe if isinstance(timeframe, str) else None,
            f"forecast processing error: {str(e)}",
        )


def _session_unavailable(symbol, timeframe, reason: str) -> dict:
    """Build a get_session_context Unavailable_Marker (the session marker shape).

    Mirrors ``_regime_unavailable`` / ``_relative_strength_unavailable`` /
    ``session._unavailable``: it carries the symbol / timeframe context, the
    ``unavailable: true`` flag, and a ``reason`` citing the cause, and it *omits*
    session_phase / time_favorability entirely — an unavailable session context
    is a missing optional input, never a fabricated label (AD-5, Requirements
    5.1, 5.2, 5.4). Recognized as an honest, non-fatal marker by
    ``_has_honest_marker`` so ``validate_contract`` passes it through unchanged.
    """
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "unavailable": True,
        "reason": reason,
    }


@tool
def get_session_context(symbol: str, timeframe: str) -> dict:
    """
    Classify the time-of-day context for a symbol/timeframe.

    Use this BEFORE committing a directional (BUY/SELL) setup to gauge whether
    the clock favors taking a new trade at all. The opening drive, the midday
    lull, and expiry-afternoon chop are the lowest-quality windows; a veteran
    trader sizes down or stands aside there. The session context is GUIDANCE
    only — it never generates a trade, never blocks one, and never overrides
    your decision. When the time window is unfavorable, bias toward lowering
    conviction, waiting for a better window, or HOLD; when it is unavailable,
    proceed with the remaining analysis and note it as unavailable.

    The classifier is pure date math over the timestamp of the most recent
    authoritative candle (fetched from the Rust Tool_Server) interpreted in the
    IST market session — no external data source. Valid timeframes: '1m', '5m',
    '10m', '15m', '1h', '4h', '1d'.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").

    Returns:
        dict: A Session_Label with:
              - session_phase ("pre_open" | "opening" | "morning" | "midday" |
                "afternoon" | "closing" | "post_close")
              - minutes_since_open (a finite number or null)
              - minutes_until_close (a finite number or null)
              - expiry_context: {is_expiry_day (bool), days_until_expiry (int)}
              - time_favorability ("favorable" | "unfavorable" | "neutral")
              When the session cannot be computed (retrieval failure/timeout,
              empty/missing candle, invalid timestamp, or any processing error)
              it returns an Unavailable_Marker {"unavailable": true, "reason": ...}
              with NO session_phase/time_favorability — treat that as a missing,
              non-blocking input. Never raises.
    """
    print(f"\n[Tool Call] >>> get_session_context: symbol={symbol}, timeframe={timeframe}")
    try:
        # 1. Validate arguments — empty/whitespace symbol or unsupported
        #    timeframe is a structured error result (NOT an exception, R4.3).
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_session_context: empty/whitespace symbol")
            return {
                "error": "get_session_context requires a non-empty symbol",
            }
        if timeframe not in SUPPORTED_TIMEFRAMES:
            print(f"[Tool Error] <<< get_session_context: unsupported timeframe '{timeframe}'")
            return {
                "error": (
                    f"get_session_context received unsupported timeframe '{timeframe}'; "
                    f"supported timeframes are {sorted(SUPPORTED_TIMEFRAMES)}"
                ),
            }

        # 2. Resolve parameters (single source of truth; never raises). The same
        #    resolver is reused by the Backtest_Seeder (AD-3, AD-6, R12.6).
        config = session.resolve_session_config()

        # 3. Fetch the most recent candle from the authoritative Rust Tool_Server,
        #    exactly like the regime / relative-strength / order-flow tools. On
        #    retrieval failure / timeout / error payload / empty result ->
        #    Unavailable_Marker citing the retrieval cause (R4.4, R5.1, R5.4).
        try:
            response = httpx.post(
                f"{RUST_SERVER_URL}/tools/get_candles",
                json={"symbol": symbol, "timeframe": timeframe, "limit": 1},
                timeout=10.0,
            )
            response.raise_for_status()
            candles = response.json()
        except Exception as fetch_exc:
            # Retrieval timeout / failure -> Unavailable_Marker citing the cause
            # (R5.1). NEVER propagate the exception into the agent loop.
            print(f"[Tool Warning] <<< get_session_context: candle retrieval failed: {fetch_exc}")
            return _session_unavailable(
                symbol,
                timeframe,
                f"candle retrieval failed: {fetch_exc}",
            )

        # The candle payload may itself be an error list (get_candles' error path
        # returns ``[{"error": ...}]``); treat a non-list / error / empty payload
        # as a retrieval failure -> Unavailable_Marker.
        if not isinstance(candles, list) or not candles or (
            isinstance(candles[0], dict) and "error" in candles[0]
        ):
            reason = "candle retrieval returned no usable data"
            if isinstance(candles, list) and candles and isinstance(candles[0], dict) and "error" in candles[0]:
                reason = f"candle retrieval failed: {candles[0].get('error')}"
            print(f"[Tool Warning] <<< get_session_context: {reason}")
            return _session_unavailable(symbol, timeframe, reason)

        # 4. Read the timestamp of the most recent candle (the last element of
        #    the chronologically-ordered candle list).
        last_candle = candles[-1]
        if not isinstance(last_candle, dict):
            return _session_unavailable(
                symbol, timeframe, "most recent candle is not an object"
            )
        timestamp_ms = last_candle.get("timestamp_ms")

        # 5. Classify via the pure Session_Classifier. It returns either a
        #    Session_Label or an Unavailable_Marker, and never raises.
        result = session.classify_session(
            timestamp_ms, config, symbol=symbol, timeframe=timeframe
        )

        # 6. Re-validate against the Tool_Result_Contract on receipt (AD-3) and
        #    return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_session_context", result)
        if validated.get("unavailable"):
            print(f"[Tool Success] <<< get_session_context: symbol={symbol}, unavailable ({validated.get('reason')})")
        else:
            print(
                f"[Tool Success] <<< get_session_context: symbol={symbol}, "
                f"phase={validated.get('session_phase')}, "
                f"favorability={validated.get('time_favorability')}"
            )
        return validated
    except Exception as e:
        # Defensive catch-all: any processing error degrades to an honest
        # Unavailable_Marker rather than raising into the agent loop (R5.4).
        print(f"[Tool Warning] <<< get_session_context FAIL: {str(e)}")
        return _session_unavailable(
            symbol if isinstance(symbol, str) else None,
            timeframe if isinstance(timeframe, str) else None,
            f"session processing error: {str(e)}",
        )


def _event_unavailable(symbol, holding_horizon, reason: str) -> dict:
    """Build a get_event_risk Unavailable_Marker (the event marker shape).

    Mirrors ``_session_unavailable`` / ``_options_unavailable``: it carries the
    ``symbol`` / ``holding_horizon`` context, the ``unavailable: true`` flag, and
    a ``reason`` citing the cause, and it *omits* ``event_risk`` /
    ``event_recommendation`` entirely — an unavailable event risk is a missing
    optional input, never a fabricated label (AD-3, Requirements 5.1, 5.3, 5.4).
    Recognized as an honest, non-fatal marker by ``_has_honest_marker`` so
    ``validate_contract`` passes it through unchanged (Requirement 4.8).
    """
    return {
        "symbol": symbol,
        "holding_horizon": holding_horizon,
        "unavailable": True,
        "reason": reason,
    }


# ── Event_Source date-parsing helpers (pure, never raise) ────────────────────
# These turn operator-provided date representations into epoch-millisecond
# candidates. A date-only string is anchored at midnight in the configured
# market timezone so the mapping is deterministic and host-timezone independent
# (never fabricates a date; never raises).

# Recognised symbol-column names in a list-of-records / CSV Event_Source.
_EVENT_SYMBOL_KEYS = ("symbol", "ticker", "scrip", "code")
# Recognised date-bearing field names in a record / API body.
_EVENT_DATE_KEYS = (
    "date",
    "dates",
    "event_date",
    "eventDate",
    "event_dates",
    "earnings_date",
    "earningsDate",
    "earnings_dates",
    "results_date",
    "resultsDate",
)


def _parse_event_date_to_ms(value, config) -> Optional[float]:
    """Parse one operator-provided event-date value to an epoch-ms candidate.

    Accepts an ISO date (``YYYY-MM-DD``, anchored at midnight in the configured
    market timezone) or an ISO datetime (naive datetimes are interpreted in the
    market timezone; aware datetimes are honoured). A finite number is treated as
    an already-computed epoch-ms candidate. Returns ``None`` for anything
    unparseable. Deterministic; never raises.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        tz = ZoneInfo(config.timezone)
    except Exception:
        return None
    dt = None
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            # Date-only -> anchor at midnight in the market timezone.
            year, month, day = int(text[0:4]), int(text[5:7]), int(text[8:10])
            dt = datetime(year, month, day, 0, 0, 0, tzinfo=tz)
        else:
            iso = text[:-1] + "+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(iso)
            dt = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)
    except (ValueError, TypeError):
        return None
    try:
        return dt.timestamp() * 1000.0
    except (OverflowError, OSError, ValueError):
        return None


def _coerce_date_values(value) -> list:
    """Flatten a date-bearing value into a list of raw date representations.

    A single string/number becomes a one-element list; a list/tuple is flattened
    (one level) keeping its string/number members. Anything else yields an empty
    list. Never raises.
    """
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return [value]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                out.append(item)
        return out
    return []


def _collect_symbol_dates(data, symbol) -> list:
    """Collect raw date values for ``symbol`` from a mapping or list-of-records.

    Supports the two documented Event_Source shapes with a case-insensitive
    symbol match:
      * a mapping ``{"RELIANCE": "2025-01-15", "TCS": ["2025-02-01", ...]}``
      * a list of records ``[{"symbol": "RELIANCE", "date": "2025-01-15"}, ...]``
    Returns raw (unparsed) date representations; never raises.
    """
    target = symbol.strip().lower() if isinstance(symbol, str) else ""
    if not target:
        return []
    out = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.strip().lower() == target:
                out.extend(_coerce_date_values(value))
    elif isinstance(data, list):
        for record in data:
            if not isinstance(record, dict):
                continue
            sym = None
            for sk in _EVENT_SYMBOL_KEYS:
                candidate = record.get(sk)
                if isinstance(candidate, str):
                    sym = candidate
                    break
            if sym is None or sym.strip().lower() != target:
                continue
            for dk in _EVENT_DATE_KEYS:
                if dk in record:
                    out.extend(_coerce_date_values(record[dk]))
    return out


def _collect_api_dates(body, symbol) -> list:
    """Collect raw date values from common symbol-scoped calendar-API shapes.

    Handles a bare list of dates (``["2025-01-15", ...]``), a wrapped mapping
    (``{"dates": [...]}`` / ``{"event_dates": [...]}``), and an events list
    (``{"events": [{"date": ...}, ...]}``). Complements ``_collect_symbol_dates``
    (which handles the symbol-keyed mapping / list-of-records shapes). Never
    raises.
    """
    out = []
    if isinstance(body, list):
        for item in body:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for dk in _EVENT_DATE_KEYS:
                    if dk in item:
                        out.extend(_coerce_date_values(item[dk]))
    elif isinstance(body, dict):
        events_val = body.get("events")
        if isinstance(events_val, list):
            for item in events_val:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    for dk in _EVENT_DATE_KEYS:
                        if dk in item:
                            out.extend(_coerce_date_values(item[dk]))
        for dk in _EVENT_DATE_KEYS:
            if dk in body:
                out.extend(_coerce_date_values(body[dk]))
    return out


def _dates_to_ms(raw_values, config) -> list:
    """Parse a list of raw date representations to epoch-ms candidates, dropping
    any that cannot be parsed. Never raises."""
    candidates = []
    for value in raw_values:
        ms = _parse_event_date_to_ms(value, config)
        if ms is not None:
            candidates.append(ms)
    return candidates


def _extract_dates_from_csv(raw, symbol) -> list:
    """Extract raw date strings for ``symbol`` from CSV text (case-insensitive).

    Accepts a header row (a ``symbol``/``ticker`` column and one or more columns
    whose name contains ``date``) or, when no header is present, assumes column 0
    is the symbol and column 1 the date. Never raises.
    """
    target = symbol.strip().lower() if isinstance(symbol, str) else ""
    if not target:
        return []
    rows = [row for row in csv.reader(raw.splitlines()) if row and any(c.strip() for c in row)]
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in _EVENT_SYMBOL_KEYS for h in header) or any("date" in h for h in header)

    sym_idx = 0
    date_idxs = [1]
    data_rows = rows
    if has_header:
        data_rows = rows[1:]
        for i, h in enumerate(header):
            if h in _EVENT_SYMBOL_KEYS:
                sym_idx = i
                break
        date_idxs = [i for i, h in enumerate(header) if "date" in h] or (
            [1] if len(header) > 1 else []
        )

    out = []
    for row in data_rows:
        if len(row) <= sym_idx:
            continue
        if row[sym_idx].strip().lower() != target:
            continue
        for di in date_idxs:
            if di < len(row):
                value = row[di].strip()
                if value:
                    out.append(value)
    return out


def _read_event_file(symbol, file_path, config):
    """Read the operator local calendar file for ``symbol``'s event dates.

    Returns ``(candidates, failure_reason)``: on success, the parsed epoch-ms
    candidate list (possibly empty when the symbol is absent) with a ``None``
    reason; on a missing / unreadable / malformed file, ``([], reason)``. The
    file may be JSON or CSV (mapping symbol -> upcoming date(s), case-insensitive
    match). Never raises (Requirements 1.1, 1.3, 1.4).
    """
    try:
        if not os.path.isfile(file_path):
            return [], f"calendar file not found: {file_path}"
        with open(file_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        if not raw.strip():
            return [], "calendar file is empty"

        lower = file_path.lower()
        raw_dates = None
        if lower.endswith(".csv"):
            raw_dates = _extract_dates_from_csv(raw, symbol)
        elif lower.endswith(".json"):
            try:
                raw_dates = _collect_symbol_dates(json.loads(raw), symbol)
            except (ValueError, TypeError):
                return [], "calendar file is malformed (unparseable JSON)"
        else:
            # Unknown extension: try JSON first, fall back to CSV.
            try:
                raw_dates = _collect_symbol_dates(json.loads(raw), symbol)
            except (ValueError, TypeError):
                raw_dates = _extract_dates_from_csv(raw, symbol)

        if raw_dates is None:
            return [], "calendar file is malformed (unparseable)"
        return _dates_to_ms(raw_dates, config), None
    except Exception as exc:  # never raise into the loader (R1.4)
        return [], f"calendar file read error: {exc.__class__.__name__}"


def _read_event_api(symbol, api_url, config):
    """Read the operator calendar API for ``symbol``'s event dates.

    Returns ``(candidates, failure_reason)``: on a 2xx JSON response, the parsed
    epoch-ms candidate list (possibly empty) with a ``None`` reason; on a
    timeout / connection error / non-2xx / unparseable body, ``([], reason)``.
    Uses the operator-configured endpoint only (never a hardcoded vendor) with
    ``config.source_timeout_s``. Never raises (Requirements 1.1, 1.4).
    """
    try:
        response = httpx.get(
            api_url, params={"symbol": symbol}, timeout=config.source_timeout_s
        )
        if response.status_code < 200 or response.status_code >= 300:
            return [], f"calendar API returned HTTP {response.status_code}"
        try:
            body = response.json()
        except Exception:
            return [], "calendar API returned an unparseable body"
        raw_dates = _collect_symbol_dates(body, symbol) + _collect_api_dates(body, symbol)
        return _dates_to_ms(raw_dates, config), None
    except Exception as exc:  # timeout / connection error / etc. (R1.4)
        return [], f"calendar API request failed: {exc.__class__.__name__}"


def _load_event_candidates(symbol, config) -> dict:
    """Gather candidate Scheduled_Event datetimes (epoch ms) for ``symbol`` from
    the operator-configured Event_Source (AD-1, AD-2; the only I/O in the gate).

    The Event_Source is pluggable and operator-configured, following the
    ``fetch_news_context`` precedent: an operator local calendar file
    (``config.calendar_file_path``, JSON or CSV mapping symbol -> upcoming
    date(s), case-insensitive match) and/or an operator calendar API
    (``config.calendar_api_url``, queried with ``config.source_timeout_s``). Both
    may be configured, in which case their candidates are combined. It never
    scrapes or hardcodes a specific paid vendor (Requirement 1.1), never
    fabricates a date (Requirements 1.3, 5.1), and never raises (Requirements
    1.4, 5.3).

    Returns a structured result so the tool can distinguish the three
    Unavailable_Marker reasons (Requirements 1.2 vs 1.4 vs 1.3):
      * ``source_configured`` — ``False`` when NEITHER a file nor an API is set
        (-> "no event source configured", Requirement 1.2).
      * ``retrieval_failed`` / ``failure_reason`` — a configured source was
        missing / unreadable / malformed / unreachable / timed out / returned a
        non-2xx or unparseable body (-> retrieval-cause marker, Requirement 1.4).
      * ``candidates`` — the combined epoch-ms candidate list; an empty list from
        a source that read cleanly means "no upcoming event for the symbol"
        (Requirement 1.3).
    """
    result = {
        "candidates": [],
        "source_configured": False,
        "retrieval_failed": False,
        "failure_reason": None,
    }

    file_path = getattr(config, "calendar_file_path", None)
    api_url = getattr(config, "calendar_api_url", None)

    if isinstance(file_path, str) and file_path.strip():
        result["source_configured"] = True
        cands, reason = _read_event_file(symbol, file_path, config)
        if reason is not None:
            result["retrieval_failed"] = True
            result["failure_reason"] = reason
        else:
            result["candidates"].extend(cands)

    if isinstance(api_url, str) and api_url.strip():
        result["source_configured"] = True
        cands, reason = _read_event_api(symbol, api_url, config)
        if reason is not None:
            result["retrieval_failed"] = True
            result["failure_reason"] = reason
        else:
            result["candidates"].extend(cands)

    return result


@tool
def get_event_risk(symbol: str, holding_horizon: str = "") -> dict:
    """
    Classify scheduled-event (earnings/results) proximity risk for a symbol
    given the intended holding horizon.

    Use this BEFORE committing a directional (BUY/SELL) setup to gauge whether
    the trade would be held THROUGH a scheduled binary event (earnings/results
    date), which carries uncompensated overnight gap risk. A veteran trader
    flattens or sizes down before a scheduled event, or takes the trade only if
    it closes intraday BEFORE the event. This gate is a RISK FILTER ONLY — it
    never generates a trade, never blocks one, never overrides your decision, and
    never fabricates an event date; it only ever tightens (size down, shorten
    horizon, or stand aside). When the event risk is unavailable, proceed with
    the remaining analysis and note it as unavailable.

    The proximity classification is pure date math (module ``events``) over a
    reference "now" and the nearest upcoming Scheduled_Event drawn from the
    operator-configured Event_Source (a calendar file and/or calendar API); with
    no source configured, an unreachable source, or no upcoming event for the
    symbol, it returns an honest Unavailable_Marker.

    Args:
        symbol (str): The trading symbol (e.g. "RELIANCE").
        holding_horizon (str): The intended maximum holding duration of the
            setup under consideration — "intraday" (closes same session) or
            "multi_session" (held overnight or longer). Absent/empty/unrecognized
            applies the documented default holding horizon.

    Returns:
        dict: An Event_Assessment with:
              - days_until_event (a finite non-negative number or null)
              - event_risk ("clear" | "imminent" | "through_event")
              - event_recommendation ("proceed" | "size_down" |
                "shorten_horizon" | "stand_aside")
              - event_date (the reference Scheduled_Event date used)
              When the event risk cannot be determined (gate disabled, no source
              configured, source unreachable/malformed, no upcoming event, or any
              processing error) it returns an Unavailable_Marker
              {"unavailable": true, "reason": ...} with NO event_risk /
              event_recommendation — treat that as a missing, non-blocking input.
              An empty/whitespace symbol returns a structured error. Never raises.
    """
    print(f"\n[Tool Call] >>> get_event_risk: symbol={symbol}, holding_horizon={holding_horizon!r}")
    try:
        # 1. Resolve parameters (single source of truth; never raises). Check the
        #    master enable flag FIRST — a disabled gate returns a gate-disabled
        #    Unavailable_Marker immediately, performing NO source retrieval
        #    (Requirements 5.4, 11.5).
        config = events.resolve_event_config()
        if not config.enabled:
            print("[Tool Success] <<< get_event_risk: gate disabled")
            return _event_unavailable(
                symbol if isinstance(symbol, str) else None,
                holding_horizon if isinstance(holding_horizon, str) else None,
                "event risk gate disabled by configuration",
            )

        # 2. Validate arguments — empty/whitespace symbol is a structured error
        #    result (NOT an exception, Requirement 4.3).
        if not isinstance(symbol, str) or not symbol.strip():
            print("[Tool Error] <<< get_event_risk: empty/whitespace symbol")
            return {
                "error": "get_event_risk requires a non-empty symbol",
            }

        # 3. Normalize the intended Holding_Horizon (absent/empty/unrecognized ->
        #    documented default, Requirement 4.4).
        horizon = events.normalize_holding_horizon(holding_horizon, config)

        # 4. Read the process clock for the reference "now" (epoch ms). This is
        #    the tool-side I/O; the pure classifier never reads the host clock.
        reference_ms = time.time() * 1000.0

        # 5. Gather candidate Scheduled_Event datetimes from the configured
        #    Event_Source. The loader returns a structured result so the three
        #    Unavailable reasons stay distinguishable (Requirements 1.2-1.4).
        source = _load_event_candidates(symbol, config)

        # 5a. NEITHER a file nor an API is configured -> honest "no event source
        #     configured" marker (Requirement 1.2).
        if not source["source_configured"]:
            print("[Tool Success] <<< get_event_risk: no event source configured")
            return _event_unavailable(
                symbol,
                horizon,
                "no event source configured",
            )

        # 6. Select the nearest UPCOMING event (pure; excludes past/at-reference).
        event_ms = events.select_next_event(source["candidates"], reference_ms, config)
        if event_ms is None:
            # A configured source that could not be read (missing/unreadable/
            # malformed file, unreachable/timeout/non-2xx/unparseable API) and
            # yielded NO candidates -> retrieval-cause marker (Requirement 1.4).
            if source["retrieval_failed"] and not source["candidates"]:
                reason = f"event source retrieval failed: {source['failure_reason']}"
                print(f"[Tool Success] <<< get_event_risk: symbol={symbol}, {reason}")
                return _event_unavailable(symbol, horizon, reason)
            # A configured source that read cleanly but has no upcoming event for
            # the symbol (or only past-dated events) -> no-upcoming-event marker
            # (Requirement 1.3). Never fabricate a date.
            print(f"[Tool Success] <<< get_event_risk: symbol={symbol}, no upcoming event")
            return _event_unavailable(
                symbol,
                horizon,
                "no upcoming scheduled event known for symbol",
            )

        # 7. Classify via the pure Event_Classifier. It returns either an
        #    Event_Assessment or an Unavailable_Marker, and never raises. The
        #    reference event_date is the ISO date of the selected event in the
        #    configured market timezone.
        try:
            event_date = datetime.fromtimestamp(
                event_ms / 1000.0, tz=ZoneInfo(config.timezone)
            ).date().isoformat()
        except Exception:
            event_date = None
        result = events.assess_event_risk(
            reference_ms, event_ms, horizon, config, symbol=symbol, event_date=event_date
        )

        # 8. Re-validate against the Tool_Result_Contract on receipt (AD-3) and
        #    return. validate_contract passes an Unavailable_Marker through
        #    unchanged and never raises.
        validated = validate_contract("get_event_risk", result)
        if validated.get("unavailable"):
            print(f"[Tool Success] <<< get_event_risk: symbol={symbol}, unavailable ({validated.get('reason')})")
        else:
            print(
                f"[Tool Success] <<< get_event_risk: symbol={symbol}, "
                f"risk={validated.get('event_risk')}, "
                f"recommendation={validated.get('event_recommendation')}"
            )
        return validated
    except Exception as e:
        # Defensive catch-all: any processing error degrades to an honest
        # Unavailable_Marker rather than raising into the agent loop (R5.3).
        print(f"[Tool Warning] <<< get_event_risk FAIL: {str(e)}")
        return _event_unavailable(
            symbol if isinstance(symbol, str) else None,
            holding_horizon if isinstance(holding_horizon, str) else None,
            f"event processing error: {str(e)}",
        )


@tool
def watch_price_condition(
    symbol: str,
    timeframe: str,
    price_level: float,
    direction: str,
    volume_multiplier: float,
    invalidation_level: Optional[float] = None,
    config: RunnableConfig = None,
) -> str:
    """
    Suspends the agent to wait for a specific condition to trigger on the live ticker.

    The `price_level` MUST be strictly BEYOND the current price in the chosen
    `direction` (above the current price for "above"/"up", below it for
    "below"/"down"); the server rejects a level that is already satisfied so the
    watcher cannot instantly false-trigger. Optionally provide an
    `invalidation_level` on the OPPOSITE side — the price at which the setup is
    proven wrong — so the system also wakes you to re-analyze (rather than wait
    forever) if price moves against your thesis.

    Args:
        symbol (str): The symbol to watch (e.g. "RELIANCE").
        timeframe (str): The timeframe to watch (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        price_level (float): The target price level to watch. MUST be beyond the
            current price in the chosen direction.
        direction (str): Trigger direction, either "above" (or "up") or "below" (or "down").
        volume_multiplier (float): Volume threshold multiplier relative to the 20-period average.
        invalidation_level (float, optional): Opposite-side price level where the
            setup is invalidated. If price reaches it (on price alone, no volume
            requirement) you are woken to re-analyze instead of treating it as the
            target being met.

    Returns:
        str: Description of the triggered event once resumed — either the target
             being reached or the setup being invalidated.
    """
    print(f"\n[Tool Call] >>> watch_price_condition: symbol={symbol}, timeframe={timeframe}, level={price_level}, direction={direction}, vol_mult={volume_multiplier}, invalidation_level={invalidation_level}")
    try:
        import time as _time
        thread_id = config.get("configurable", {}).get("thread_id", "default_thread")
        payload = {
            "thread_id": thread_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "price_level": price_level,
            "direction": direction,
            "volume_multiplier": volume_multiplier,
            "invalidation_level": invalidation_level
        }
        # ── Adaptive Opportunity Engine heartbeat configuration (R5.1) ────────
        # Pass the resolved heartbeat fields so the Rust watcher emits bounded,
        # cadence-driven `trigger_kind="heartbeat"` resumes in addition to the
        # target/invalidation triggers. Omitted-when-disabled semantics on the Rust
        # side mean a default (heartbeat off) run registers exactly as before, so
        # this is inert unless OPPORTUNITY_HEARTBEAT_ENABLED is set.
        try:
            _opp_cfg = opportunity.resolve_opportunity_config()
            payload["heartbeat_enabled"] = bool(_opp_cfg.heartbeat_enabled)
            payload["heartbeat_cadence_secs"] = float(_opp_cfg.heartbeat_cadence_secs)
            payload["heartbeat_max"] = int(_opp_cfg.heartbeat_max)
        except Exception as _cfg_err:  # noqa: BLE001 - never block registration on config
            print(f"[Tool Warning] watch_price_condition: heartbeat config unavailable ({_cfg_err}); registering without heartbeat.")
        # Retry registration up to the configured number of attempts in case the
        # Rust tool server is still starting up.
        max_attempts = max(1, WATCH_REGISTRATION_MAX_ATTEMPTS)
        last_error = None
        # Set when the server deterministically REJECTS the requested level
        # (HTTP 400 from the watcher validator — e.g. price_level already
        # satisfied / on the wrong side, or invalidation_level on the wrong
        # side). This is NOT retryable and NOT a "server down" condition: the
        # agent simply chose a bad level and must pick a valid one.
        rejection_msg = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = httpx.post(
                    f"{RUST_SERVER_URL}/tools/watch_condition",
                    json=payload,
                    timeout=10.0
                )
                if response.status_code == 400:
                    # Extract the validator's specific reason from the JSON body
                    # ({"error": "..."}) so the model can self-correct the level.
                    try:
                        body = response.json()
                        rejection_msg = body.get("error") if isinstance(body, dict) else None
                    except Exception:
                        rejection_msg = None
                    if not rejection_msg:
                        rejection_msg = (response.text or "").strip() or "Watcher level rejected by validator."
                    last_error = None
                    break
                response.raise_for_status()
                last_error = None
                break
            except Exception as retry_err:
                last_error = retry_err
                print(f"[Tool Warning] watch_price_condition attempt {attempt}/{max_attempts} failed: {str(retry_err)}")
                if attempt < max_attempts:
                    _time.sleep(WATCH_REGISTRATION_RETRY_DELAY_S)

        # Recoverable validation rejection: surface the exact reason and tell the
        # model to re-call watch_price_condition with a corrected level. This is
        # deliberately NOT a HOLD — the setup may still be valid, the agent just
        # picked a level on the wrong side of the current price.
        if rejection_msg is not None:
            print(f"[Tool Rejected] <<< watch_price_condition level rejected: {rejection_msg}")
            return {
                "status": "watch_level_rejected",
                "error": rejection_msg,
                "message": (
                    "Your watch condition was REJECTED: " + rejection_msg + " "
                    "Do NOT HOLD or output a trade because of this. Re-call "
                    "watch_price_condition with a corrected price_level (strictly "
                    "BEYOND the current price in your chosen direction) and, if you "
                    "supply one, an invalidation_level on the OPPOSITE side of the "
                    "current price."
                ),
            }

        if last_error is not None:
            raise last_error

        print(f"[Tool Success] <<< watch_price_condition registered watcher for symbol={symbol} on Rust server.")
    except Exception as e:
        # Registration failed after exhausting the configured retry budget
        # (R14.3). Return a STRUCTURED failure result carrying an explicit
        # ``error`` marker so the ReAct loop treats it as a non-fatal tool error
        # (recognized by graph._tool_result_is_error): the run is not aborted,
        # no watcher is suspended, and the bounded loop ultimately yields a HOLD
        # with no trade committed. The ``action: HOLD`` / ``trade: None`` fields
        # make the intended outcome explicit to the agent.
        print(f"[Tool Error] <<< watch_price_condition FAIL after {WATCH_REGISTRATION_MAX_ATTEMPTS} attempts: {str(e)}")
        return {
            "status": "watch_registration_failed",
            "action": "HOLD",
            "trade": None,
            "error": (
                f"Failed to register price watcher after {WATCH_REGISTRATION_MAX_ATTEMPTS} "
                f"attempts: {str(e)}."
            ),
            "message": (
                "The desktop application (Tauri) must be running for the live price "
                "watcher to work. Falling back to HOLD. Do NOT output a trade — the "
                "condition has not been met."
            ),
        }

    print(f"[Tool Pause] watch_price_condition: Interrupting graph, waiting for user resume...")
    resumed = interrupt(
        {
            "status": "watching_registered",
            "thread_id": thread_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "price_level": price_level,
            "direction": direction,
            "volume_multiplier": volume_multiplier,
            "invalidation_level": invalidation_level
        }
    )

    print(f"[Tool Resumed] <<< watch_price_condition resumed with: {resumed}")

    # The resume value is now a dict {"candle": ..., "trigger_kind": ...}. Stay
    # backward-compatible: if a bare value (or a dict without "trigger_kind")
    # arrives, treat it as the target being reached.
    if isinstance(resumed, dict) and "trigger_kind" in resumed:
        candle = resumed.get("candle")
        trigger_kind = resumed.get("trigger_kind") or "target"
    else:
        candle = resumed
        trigger_kind = "target"

    # Classify the resume trigger to one canonical kind and scope a cheap,
    # trigger-relevant Delta_Recheck instead of a full re-scan (R6.1-6.3).
    kind = opportunity.classify_resume(trigger_kind)
    recheck = opportunity.delta_recheck_plan(kind)
    recheck_str = ", ".join(recheck)

    if kind == opportunity.RESUME_INVALIDATION:
        return (
            "Setup INVALIDATED: price moved to the invalidation level AGAINST the "
            "setup before reaching the watched target. The target condition was NOT "
            "met — do NOT treat this as the level being reached. Run a brief "
            f"post-mortem, then re-check ONLY: {recheck_str}. Do NOT blindly re-arm "
            "the SAME thesis (an unchanged re-arm is rejected) — change the "
            "structure / timeframe / tier, or stand aside. Invalidation candle "
            f"details: {candle}"
        )

    if kind == opportunity.RESUME_HEARTBEAT:
        # A bounded mid-wait pulse: NOT the target being reached. Cheaply re-check
        # whether the setup is developing or decaying, then re-arm the same watch
        # to keep waiting, adapt to a different level/tier, or stand aside (R5.3).
        return (
            "Heartbeat check (mid-wait pulse): the watched target was NOT reached — "
            "do NOT treat this as the level being met. Cheaply re-check ONLY: "
            f"{recheck_str}, then decide: keep waiting (the watch is still armed), "
            "adapt to a different level/tier, or stand aside. Current candle "
            f"details: {candle}"
        )

    return (
        "Target condition met (price reached the watched level). Confirm the entry "
        f"is still valid by re-checking ONLY: {recheck_str}. Triggered candle: {candle}"
    )

def _coerce_management_plan(management_plan, action, entry, stop_loss, atr_14):
    """Build a ``trade_manager.ManagementPlan`` from the optional declare_trade
    ``management_plan`` dict (Requirement 4.1).

    The dict carries the multi-leg detail (``legs`` / ``breakeven`` / ``trailing``);
    the base bracket fields (``action`` / ``entry`` / ``initial_stop`` / ``atr_14``)
    default to the declare_trade arguments when the dict omits them. The plan is
    parsed by reusing ``trade_manager.plan_from_json`` (the single round-trip
    boundary), so a malformed / out-of-shape dict yields ``None`` rather than
    raising. Returns ``None`` when no usable plan dict is supplied.
    """
    if not isinstance(management_plan, dict):
        return None
    # Merge the declared bracket as defaults so the plan always carries the
    # action / entry / initial_stop the Trade_Validator needs, while still
    # allowing the dict to override them explicitly.
    merged = dict(management_plan)
    if merged.get("action") is None:
        merged["action"] = action
    if merged.get("entry") is None:
        merged["entry"] = entry
    if merged.get("initial_stop") is None:
        merged["initial_stop"] = stop_loss
    if merged.get("atr_14") is None:
        merged["atr_14"] = atr_14
    try:
        return trade_manager.plan_from_json(json.dumps(merged))
    except (TypeError, ValueError):
        # Not JSON-serializable (e.g. an exotic value in the dict) -> no plan.
        return None


@tool
def declare_trade(
    action: str,
    conviction_score: int,
    setup_validation: str,
    execution_plan: str,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    atr_14: Optional[float] = None,
    management_plan: Optional[dict] = None,
) -> str:
    """
    Declares the final trading decision for the current analysis session and
    commits it through the authoritative Trade_Validator on the Rust Tool Server.

    For a BUY or SELL you MUST provide the numeric `entry`, `stop_loss`, and
    `take_profit` levels (and `atr_14` from the consensus report). The server
    validates the trade and only commits it when ALL risk rules pass:
      - all three levels present and finite,
      - direction consistency (BUY: stop_loss < entry < take_profit;
        SELL: take_profit < entry < stop_loss),
      - Risk:Reward >= 1:2,
      - stop distance >= 1.5 x ATR (when atr_14 is supplied).
    If validation fails the trade is REJECTED (not committed) and you MUST revise
    the levels and call declare_trade again. A HOLD may omit the numeric levels.

    Optionally you may attach a multi-leg `management_plan` describing how the
    position is scaled out and protected (partial exits, a move to breakeven, and
    a trailing stop). When omitted the entry/stop_loss/take_profit are committed
    exactly as today — a single-target trade. When present the plan is validated
    on the Python side (Trade_Validator) BEFORE the trade is forwarded to the Rust
    Tool Server, and is committed only when validation passes; a failing plan is
    REJECTED with the reason so you can revise and re-declare.

    Args:
        action (str): The final decision, one of: "BUY", "SELL", "HOLD".
        conviction_score (int): Score representing risk confidence (0 to 100).
        setup_validation (str): 2-sentence synthesis of findings or warnings.
        execution_plan (str): Actionable entry/stop-loss/take-profit plan (prose).
        entry (float, optional): Proposed entry price (REQUIRED for BUY/SELL).
        stop_loss (float, optional): Proposed stop-loss price (REQUIRED for BUY/SELL).
        take_profit (float, optional): Proposed take-profit price (REQUIRED for BUY/SELL).
        atr_14 (float, optional): Current ATR(14) used for the stop-distance check.
        management_plan (dict, optional): A JSON-serializable multi-leg exit plan
            with `legs` (each a `{"target": float, "fraction": float}` in
            chronological/profit order), an optional `breakeven`
            (`{"price": float}` or `{"r_multiple": float}`), and an optional
            `trailing` (`{"atr_multiple": float}` or `{"r_increment": float}`).
            The base bracket fields (action / entry / initial_stop / atr_14)
            default to the arguments above when the dict omits them. Omit this
            argument entirely for a single-target trade.

    Returns:
        str: Confirmation message, or a rejection message stating the reason when
             the Trade_Validator rejects the trade.
    """
    print(f"\n[Tool Call] >>> declare_trade: action={action}, conviction={conviction_score}%, "
          f"entry={entry}, stop_loss={stop_loss}, take_profit={take_profit}, atr_14={atr_14}, "
          f"management_plan={'present' if management_plan else 'none'}")
    print(f"[Tool Detail] Setup Validation: {setup_validation}")
    print(f"[Tool Detail] Execution Plan: {execution_plan}")

    # ── Management_Plan gate (Requirement 4) ─────────────────────────────────
    # When a management_plan is supplied, parse it into a Trade_Manager
    # ManagementPlan (reusing trade_manager.plan_from_json) and run the pure
    # Python Trade_Validator BEFORE forwarding to the Rust server. The trade is
    # committed only when this Python-side validation passes (R4.3); a malformed
    # or risk-violating plan is REJECTED with the reason so the agent can revise
    # and re-declare, and is NOT forwarded/committed (R4.4). When management_plan
    # is absent the trade is a Single_Target_Trade and behavior is unchanged
    # (R4.2) — no Python-side gate is added so the legacy path is byte-for-byte
    # identical to before this feature.
    if management_plan is not None:
        plan = _coerce_management_plan(management_plan, action, entry, stop_loss, atr_14)
        if plan is None:
            # The plan dict was supplied but could not be parsed into a
            # well-formed ManagementPlan (e.g. missing/malformed legs) — treat as
            # an invalid plan rather than silently committing single-target.
            return (
                f"TRADE_REJECTED: the {action} management_plan could not be parsed into a "
                f"valid multi-leg plan. Provide `legs` as a list of "
                f"{{\"target\": float, \"fraction\": float}} entries (and optional "
                f"`breakeven`/`trailing`), then call declare_trade again."
            )
        levels = validator.ExecutionLevels(
            entry=entry, stop_loss=stop_loss, take_profit=take_profit
        )
        outcome = validator.validate_trade(
            validator.Action.from_str_lenient(action),
            levels,
            atr_14,
            plan=plan,
        )
        if not outcome.is_pass():
            reason = outcome.reason
            print(f"[Tool Detail] declare_trade management plan REJECTED: {reason.tag}")
            # Match the existing TRADE_REJECTED result format so the graph treats
            # this as a non-finalizing turn and the agent revises and re-declares.
            return (
                f"TRADE_REJECTED: the Trade_Validator rejected this {action} management plan "
                f"because '{reason.message}'. Revise the management plan (leg fractions in "
                f"(0.0, 1.0] summing to <= 1.0, scale-out targets ordered on the profit side, "
                f"breakeven strictly between entry and the first target, and the blended "
                f"Risk:Reward at/above the minimum), then call declare_trade again."
            )

    # Persist the final decision to the Rust tool server, which runs the
    # authoritative Trade_Validator and emits `final_analysis_ready` ONLY when
    # validation passes (R6.6/R6.7). The structured levels are forwarded so a
    # BUY/SELL can actually be validated and committed (without them every
    # directional trade is rejected as MissingLevels).
    try:
        request_body = {
            "action": action,
            "conviction_score": int(conviction_score),
            "setup_validation": setup_validation,
            "execution_plan": execution_plan,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "atr_14": atr_14,
        }
        # Forward the management plan alongside the base bracket; the Rust server
        # ignores fields it does not consume, so this is safe and keeps the
        # authoritative path aware of the declared plan (Requirement 4.3).
        if management_plan is not None:
            request_body["management_plan"] = management_plan
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/declare_trade",
            json=request_body,
            timeout=10.0
        )
        response.raise_for_status()
        body = response.json()
        print(f"[Tool Success] <<< declare_trade server response: {body}")
        if isinstance(body, dict) and str(body.get("status")).lower() == "rejected":
            reason = body.get("reason", "unknown")
            # Surface the rejection so the agent revises the levels and re-declares.
            # The graph treats a TRADE_REJECTED result as a non-finalizing turn.
            return (
                f"TRADE_REJECTED: the Trade_Validator rejected this {action} because "
                f"'{reason}'. Revise the entry/stop_loss/take_profit so Risk:Reward "
                f">= 1:2 and the stop is >= 1.5x ATR, then call declare_trade again."
            )
    except Exception as e:
        # Don't fail the agent run if persistence fails — the JSON is still
        # surfaced via the SSE stream — but make the failure visible.
        print(f"[Tool Warning] <<< declare_trade could not be persisted to Rust server: {str(e)}")

    return f"Trade declared successfully: {action} with {conviction_score}% conviction."


# ── Volume Profile (Market Auction Structure) ────────────────────────────────
# Phase 1 high-impact addition: expose the volume-by-price auction structure to
# the agent so it can reason about WHERE volume actually traded — the Point of
# Control (POC, the fair-value magnet), the Value_Area edges (VAH/VAL, the
# mean-reversion / breakout boundaries), high-volume nodes (HVN, acceptance
# shelves that act as support/resistance) and low-volume nodes (LVN, rejection
# gaps price tends to move through quickly).
#
# The binning math MIRRORS the frontend's authoritative
# `frontend/src/charting/engines/volumeProfileEngine.ts` so the levels the agent
# reasons about are identical to what the trader sees rendered on the chart:
#   * volume is distributed across each candle's high–low span, split evenly
#     across the rows it touches (full volume conserved, remainder to the top row),
#   * POC is the centre price of the single greatest-volume row (lowest index on
#     a tie),
#   * the Value_Area grows outward from the POC, always absorbing the larger of
#     the two adjacent rows (preferring the lower row on a tie) until it reaches
#     the target percentage of total volume (default 70%).

DEFAULT_PROFILE_ROWS = 24
MIN_PROFILE_ROWS = 1
MAX_PROFILE_ROWS = 1000
DEFAULT_VALUE_AREA_PERCENT = 70.0


def _normalize_rows(rows) -> int:
    """Default to 24, round to int, clamp into [1, 1000] (mirrors the TS engine)."""
    try:
        if rows is None or not math_isfinite(float(rows)):
            return DEFAULT_PROFILE_ROWS
        rounded = int(round(float(rows)))
    except (TypeError, ValueError):
        return DEFAULT_PROFILE_ROWS
    return max(MIN_PROFILE_ROWS, min(MAX_PROFILE_ROWS, rounded))


def _normalize_value_percent(pct) -> float:
    """Default to 70, clamp into [1, 100] (mirrors the TS engine)."""
    try:
        if pct is None or not math_isfinite(float(pct)):
            return DEFAULT_VALUE_AREA_PERCENT
        v = float(pct)
    except (TypeError, ValueError):
        return DEFAULT_VALUE_AREA_PERCENT
    return max(1.0, min(100.0, v))


def _value_area_bounds(row_volumes, poc_index, value_percent):
    """Grow the Value_Area outward from the POC (mirrors TS ``valueArea``).

    Returns inclusive (lo_index, hi_index) into ``row_volumes``.
    """
    n = len(row_volumes)
    if n == 0:
        return 0, 0
    poc = max(0, min(poc_index, n - 1))
    lo = hi = poc
    total = sum(row_volumes)
    if total <= 0:
        return lo, hi
    target = total * (_normalize_value_percent(value_percent) / 100.0)
    acc = row_volumes[poc]
    neg_inf = float("-inf")
    while acc < target and (lo > 0 or hi < n - 1):
        below = row_volumes[lo - 1] if lo > 0 else neg_inf
        above = row_volumes[hi + 1] if hi < n - 1 else neg_inf
        if below == neg_inf and above == neg_inf:
            break
        # Absorb the larger adjacent row; on a tie prefer the lower (below) row.
        if below >= above:
            lo -= 1
            acc += row_volumes[lo]
        else:
            hi += 1
            acc += row_volumes[hi]
    return lo, hi


def _compute_volume_profile(candles, rows=DEFAULT_PROFILE_ROWS, value_area_percent=DEFAULT_VALUE_AREA_PERCENT):
    """Pure volume-profile computation from OHLCV candles (mirrors the TS engine).

    ``candles`` is a list of dicts with at least high/low/volume (and, for the
    interpretive ``current_price``, close). Returns a structured dict with the
    POC, VAH, VAL, value-area %, total volume, current price, where price sits
    relative to the value area, and the high/low-volume nodes. The function is
    pure and never raises on ordinary numeric data.
    """
    rows = _normalize_rows(rows)
    value_area_percent = _normalize_value_percent(value_area_percent)

    def _num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math_isfinite(v) else None

    cleaned = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        hi = _num(c.get("high"))
        lo = _num(c.get("low"))
        vol = _num(c.get("volume"))
        if hi is None or lo is None:
            continue
        cleaned.append((lo, hi, vol if vol is not None else 0.0, _num(c.get("close"))))

    current_price = None
    for entry in reversed(cleaned):
        if entry[3] is not None:
            current_price = entry[3]
            break

    empty = {
        "rows": rows,
        "poc": None,
        "vah": None,
        "val": None,
        "value_area_percent": value_area_percent,
        "total_volume": 0.0,
        "current_price": current_price,
        "price_vs_value_area": "unknown",
        "hvn_levels": [],
        "lvn_levels": [],
    }

    if not cleaned:
        return empty

    min_p = min(c[0] for c in cleaned)
    max_p = max(c[1] for c in cleaned)
    if not (math_isfinite(min_p) and math_isfinite(max_p)):
        return empty

    bin_size = (max_p - min_p) / rows

    def bin_index(price):
        if bin_size <= 0:
            return 0
        idx = int(math_floor((price - min_p) / bin_size))
        return max(0, min(rows - 1, idx))

    bin_volumes = [0.0] * rows
    for lo, hi, vol, _close in cleaned:
        if vol <= 0:
            continue
        lo_i = bin_index(lo)
        hi_i = bin_index(hi)
        if lo_i == hi_i:
            bin_volumes[lo_i] += vol
        else:
            span = hi_i - lo_i + 1
            share = vol / span
            distributed = 0.0
            for i in range(lo_i, hi_i):
                bin_volumes[i] += share
                distributed += share
            # Remainder to the top row so each candle's volume is conserved.
            bin_volumes[hi_i] += vol - distributed

    total_volume = sum(bin_volumes)
    if total_volume <= 0:
        return {**empty, "total_volume": 0.0}

    # POC: single greatest-volume row (lowest index on a tie).
    poc_index = 0
    poc_vol = bin_volumes[0]
    for i in range(1, rows):
        if bin_volumes[i] > poc_vol:
            poc_vol = bin_volumes[i]
            poc_index = i

    lo_index, hi_index = _value_area_bounds(bin_volumes, poc_index, value_area_percent)

    def row_low(i):
        return min_p + i * bin_size

    def row_high(i):
        return min_p + (i + 1) * bin_size

    def row_center(i):
        return (row_low(i) + row_high(i)) / 2.0

    poc = row_center(poc_index)
    val = row_low(lo_index)
    vah = row_high(hi_index)

    # Where does the latest price sit relative to the value area?
    if current_price is None:
        price_loc = "unknown"
    elif current_price > vah:
        price_loc = "above_value_area"
    elif current_price < val:
        price_loc = "below_value_area"
    else:
        price_loc = "inside_value_area"

    # High-volume nodes (acceptance shelves → S/R) and low-volume nodes
    # (rejection gaps → fast-move zones). Report representative centre prices.
    indexed = [(i, bin_volumes[i]) for i in range(rows)]
    hvn = sorted(indexed, key=lambda t: t[1], reverse=True)[:3]
    nonzero = [t for t in indexed if t[1] > 0]
    lvn = sorted(nonzero, key=lambda t: t[1])[:3]

    hvn_levels = [{"price": round(row_center(i), 4), "volume": round(v, 4)} for i, v in hvn]
    lvn_levels = [{"price": round(row_center(i), 4), "volume": round(v, 4)} for i, v in lvn]

    return {
        "rows": rows,
        "poc": round(poc, 4),
        "vah": round(vah, 4),
        "val": round(val, 4),
        "value_area_percent": value_area_percent,
        "total_volume": round(total_volume, 4),
        "current_price": round(current_price, 4) if current_price is not None else None,
        "price_vs_value_area": price_loc,
        "hvn_levels": hvn_levels,
        "lvn_levels": lvn_levels,
    }


@tool
def get_volume_profile(
    symbol: str,
    timeframe: str,
    limit: int = 200,
    rows: int = DEFAULT_PROFILE_ROWS,
    value_area_percent: float = DEFAULT_VALUE_AREA_PERCENT,
) -> dict:
    """
    Computes the Volume Profile (market auction structure) for a symbol/timeframe:
    where traded volume actually concentrated by price. Use this to locate
    institutional acceptance and rejection zones for precise entry, stop-loss,
    and target placement — these levels are often stronger than pivot-based S/R.

    Returns:
      - poc (float): Point of Control — the highest-volume price (fair-value
        magnet). Price tends to gravitate back toward the POC.
      - vah / val (float): Value_Area High / Low — the edges of the range that
        held ~70% of volume. Inside = balance/mean-reversion; a decisive break
        of VAH/VAL signals imbalance/trend continuation.
      - price_vs_value_area (str): "above_value_area" | "inside_value_area" |
        "below_value_area" — where the latest price sits (key context).
      - hvn_levels (list): High-Volume Nodes — acceptance shelves that act as
        support/resistance.
      - lvn_levels (list): Low-Volume Nodes — rejection gaps price moves through
        quickly (good for momentum targets, poor for resting orders).
      - current_price, total_volume, value_area_percent, rows.

    The bins are computed from the SAME authoritative candle source used by every
    other tool, so the levels stay consistent across the system.

    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        timeframe (str): The candle timeframe (e.g., "1m", "5m", "10m", "15m", "1h", "4h", "1d").
        limit (int): Number of recent candles to profile (default 200).
        rows (int): Number of price-level bins (default 24, clamped 1–1000).
        value_area_percent (float): Value_Area target percentage (default 70, clamped 1–100).
    """
    print(f"\n[Tool Call] >>> get_volume_profile: symbol={symbol}, timeframe={timeframe}, limit={limit}, rows={rows}, va%={value_area_percent}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "timeframe": timeframe, "limit": limit},
            timeout=10.0
        )
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        candles = response.json()
        if isinstance(candles, dict) and "error" in candles:
            return {"error": f"Failed to retrieve candles for volume profile: {candles.get('error')}"}
        if not isinstance(candles, list):
            return {"error": f"Unexpected candle payload for volume profile: {type(candles).__name__}"}
        profile = _compute_volume_profile(candles, rows=rows, value_area_percent=value_area_percent)
        profile["symbol"] = symbol
        profile["timeframe"] = timeframe
        print(f"[Tool Success] <<< get_volume_profile: symbol={symbol}, poc={profile.get('poc')}, vah={profile.get('vah')}, val={profile.get('val')}, price_loc={profile.get('price_vs_value_area')}")
        return validate_contract("get_volume_profile", profile)
    except Exception as e:
        print(f"[Tool Error] <<< get_volume_profile FAIL: {str(e)}")
        return {"error": f"Failed to compute volume profile: {str(e)}"}


# ── Trade Performance / Track Record (measurement feedback loop) ─────────────
# Phase 2: lets the agent consult its OWN realized track record before
# committing, so conviction is calibrated against what has actually worked
# rather than the model's in-the-moment confidence. Backed by journal.py, which
# records every committed decision and scores open trades against later candles.
import journal as _journal


@tool
def get_trade_performance(symbol: str) -> dict:
    """
    Returns the agent's OWN realized trading track record for calibrating
    conviction — win rate and expectancy (in R multiples) overall and broken
    down by setup type. Open trades are scored against the latest candle data
    before the stats are returned, so the numbers are current.

    Use this during analysis (after establishing your bias and the setup type)
    to sanity-check your edge: if a comparable setup historically has NEGATIVE
    expectancy or a win rate that does not support its Risk:Reward, you MUST
    reduce conviction, tighten the criteria, or HOLD. Treat the stats as a weak
    prior when ``low_sample`` is true (too few trades to be significant).

    Returns:
      - overall: {trades_scored, wins, losses, open, expired, win_rate, expectancy_r}
      - by_setup: list of the same stats grouped by a coarse setup fingerprint
        (``setup_key`` encodes direction, macro alignment, predictive agreement,
        and value-area location), most-traded first.
      - low_sample (bool): true when too few scored trades exist to rely on.

    Args:
        symbol (str): The trading symbol to report on (e.g., "RELIANCE").
    """
    print(f"\n[Tool Call] >>> get_trade_performance: symbol={symbol}")
    try:
        stats = _journal.get_stats(symbol)
        stats["symbol"] = symbol
        ov = stats.get("overall", {})
        print(f"[Tool Success] <<< get_trade_performance: symbol={symbol}, scored={ov.get('trades_scored')}, win_rate={ov.get('win_rate')}, expectancy_r={ov.get('expectancy_r')}, low_sample={stats.get('low_sample')}")
        return stats
    except Exception as e:
        print(f"[Tool Warning] <<< get_trade_performance FAIL: {str(e)}")
        # Non-blocking: the track record is a calibration aid, not a hard input.
        return {
            "symbol": symbol,
            "overall": {"trades_scored": 0, "win_rate": None, "expectancy_r": None},
            "by_setup": [],
            "low_sample": True,
            "unavailable": True,
            "error": f"Failed to load trade performance: {str(e)}",
        }
