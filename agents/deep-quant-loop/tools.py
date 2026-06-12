import os
import math
from typing import Optional
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
        if response.status_code != 200:
            print(f"[Tool Error] Server returned {response.status_code}: {response.text}")
        response.raise_for_status()
        res = response.json()
        print(f"[Tool Success] <<< get_candles: symbol={symbol}, timeframe={timeframe}, retrieved {len(res)} candles.")
        return validate_contract("get_candles", res)
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
              opening_range_low, and daily_pivot. An ordering_exception field is set when the
              computed levels cannot satisfy the canonical S3≤S2≤S1≤pivot≤R1≤R2≤R3 ordering.
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

    if trigger_kind == "invalidation":
        return (
            "Setup INVALIDATED: price moved to the invalidation level AGAINST the "
            "setup before reaching the watched target. The target condition was NOT "
            "met — do NOT treat this as the level being reached. Re-analyze the "
            "current structure or HOLD. Invalidation candle details: "
            f"{candle}"
        )

    return f"Target condition met (price reached the watched level). Triggered candle: {candle}"

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

    Args:
        action (str): The final decision, one of: "BUY", "SELL", "HOLD".
        conviction_score (int): Score representing risk confidence (0 to 100).
        setup_validation (str): 2-sentence synthesis of findings or warnings.
        execution_plan (str): Actionable entry/stop-loss/take-profit plan (prose).
        entry (float, optional): Proposed entry price (REQUIRED for BUY/SELL).
        stop_loss (float, optional): Proposed stop-loss price (REQUIRED for BUY/SELL).
        take_profit (float, optional): Proposed take-profit price (REQUIRED for BUY/SELL).
        atr_14 (float, optional): Current ATR(14) used for the stop-distance check.

    Returns:
        str: Confirmation message, or a rejection message stating the reason when
             the Trade_Validator rejects the trade.
    """
    print(f"\n[Tool Call] >>> declare_trade: action={action}, conviction={conviction_score}%, "
          f"entry={entry}, stop_loss={stop_loss}, take_profit={take_profit}, atr_14={atr_14}")
    print(f"[Tool Detail] Setup Validation: {setup_validation}")
    print(f"[Tool Detail] Execution Plan: {execution_plan}")

    # Persist the final decision to the Rust tool server, which runs the
    # authoritative Trade_Validator and emits `final_analysis_ready` ONLY when
    # validation passes (R6.6/R6.7). The structured levels are forwarded so a
    # BUY/SELL can actually be validated and committed (without them every
    # directional trade is rejected as MissingLevels).
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/declare_trade",
            json={
                "action": action,
                "conviction_score": int(conviction_score),
                "setup_validation": setup_validation,
                "execution_plan": execution_plan,
                "entry": entry,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "atr_14": atr_14,
            },
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
