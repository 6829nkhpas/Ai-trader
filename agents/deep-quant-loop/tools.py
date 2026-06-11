import os
from typing import Optional
import httpx
from langchain_core.tools import tool
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

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

@tool
def watch_price_condition(
    symbol: str,
    timeframe: str,
    price_level: float,
    direction: str,
    volume_multiplier: float,
    config: RunnableConfig
) -> str:
    """
    Suspends the agent to wait for a specific condition to trigger on the live ticker.
    
    Args:
        symbol (str): The symbol to watch (e.g. "RELIANCE").
        timeframe (str): The timeframe to watch (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        price_level (float): The price level to watch.
        direction (str): Trigger direction, either "above" (or "up") or "below" (or "down").
        volume_multiplier (float): Volume threshold multiplier relative to the 20-period average.
        
    Returns:
        str: Description of the triggered event once resumed.
    """
    print(f"\n[Tool Call] >>> watch_price_condition: symbol={symbol}, timeframe={timeframe}, level={price_level}, direction={direction}, vol_mult={volume_multiplier}")
    try:
        import time as _time
        thread_id = config.get("configurable", {}).get("thread_id", "default_thread")
        payload = {
            "thread_id": thread_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "price_level": price_level,
            "direction": direction,
            "volume_multiplier": volume_multiplier
        }
        # Retry registration up to the configured number of attempts in case the
        # Rust tool server is still starting up.
        max_attempts = max(1, WATCH_REGISTRATION_MAX_ATTEMPTS)
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = httpx.post(
                    f"{RUST_SERVER_URL}/tools/watch_condition",
                    json=payload,
                    timeout=10.0
                )
                response.raise_for_status()
                last_error = None
                break
            except Exception as retry_err:
                last_error = retry_err
                print(f"[Tool Warning] watch_price_condition attempt {attempt}/{max_attempts} failed: {str(retry_err)}")
                if attempt < max_attempts:
                    _time.sleep(WATCH_REGISTRATION_RETRY_DELAY_S)

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
    triggered_candle = interrupt(
        {
            "status": "watching_registered",
            "thread_id": thread_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "price_level": price_level,
            "direction": direction,
            "volume_multiplier": volume_multiplier
        }
    )
    
    print(f"[Tool Resumed] <<< watch_price_condition triggered with candle: {triggered_candle}")
    return f"Condition met! Triggered candle details: {triggered_candle}"

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
