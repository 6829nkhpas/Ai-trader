import httpx
from langchain_core.tools import tool
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

RUST_SERVER_URL = "http://localhost:8084"

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

@tool
def get_candles(symbol: str, timeframe: str, limit: int) -> list:
    """
    實Raw OHLCV data. Valid timeframes: '1m', '5m', '15m', '1h', '4h', '1d'.
    
    Args:
        symbol (str): The trading symbol to fetch (e.g. "RELIANCE").
        timeframe (str): The candle timeframe (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
        limit (int): The number of recent candles to retrieve.
        
    Returns:
        list: A list of candles, where each candle is a dictionary containing open, high, low, close, and volume.
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
        return res
    except Exception as e:
        print(f"[Tool Error] <<< get_candles FAIL: {str(e)}")
        return [{"error": f"Failed to retrieve candles from Rust server: {str(e)}"}]

@tool
def get_consensus_report(symbol: str, timeframe: str) -> dict:
    """
    Calculates live technical consensus (Trend, Momentum, Volatility, VWEPR/OLS curves) for a specific timeframe.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        timeframe (str): The timeframe to analyze (e.g., "1m", "5m", "15m", "1h", "4h", "1d").
        
    Returns:
        dict: The compiled consensus report with trend score, momentum state, active patterns, and curve parameters.
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
        return res
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
        return res
    except Exception as e:
        print(f"[Tool Error] <<< get_multi_tf_trend FAIL: {str(e)}")
        return {"error": f"Failed to compute multi-tf trend: {str(e)}"}

@tool
def get_support_resistance(symbol: str) -> dict:
    """
    Identifies exact support and resistance liquidity zones for the specified trading symbol.
    Calculates Pivot Points, support levels (S1, S2), and resistance levels (R1, R2) 
    using recent candle high, low, and close levels. Use this to determine valid placement for 
    entry price, stop loss, and take profit targets.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        
    Returns:
        dict: Key support and resistance levels (Pivot, S1, S2, R1, R2, high, low).
    """
    print(f"\n[Tool Call] >>> get_support_resistance: symbol={symbol}")
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "limit": 50},
            timeout=10.0
        )
        response.raise_for_status()
        candles = response.json()
        
        highs = [c["high"] for c in candles if "high" in c]
        lows = [c["low"] for c in candles if "low" in c]
        closes = [c["close"] for c in candles if "close" in c]
        
        if not highs or not lows or not closes:
            print("[Tool Warning] <<< get_support_resistance: Insufficient candle data.")
            return {"error": "Insufficient candle data to determine support/resistance."}
            
        h = max(highs[-20:])
        l = min(lows[-20:])
        c = closes[-1]
        
        pivot = (h + l + c) / 3.0
        r1 = 2 * pivot - l
        s1 = 2 * pivot - h
        r2 = pivot + (h - l)
        s2 = pivot - (h - l)
        
        res = {
            "symbol": symbol,
            "pivot_point": round(pivot, 2),
            "resistance_1": round(r1, 2),
            "support_1": round(s1, 2),
            "resistance_2": round(r2, 2),
            "support_2": round(s2, 2),
            "recent_high": round(h, 2),
            "recent_low": round(l, 2)
        }
        print(f"[Tool Success] <<< get_support_resistance: symbol={symbol}, pivot={res['pivot_point']}, S1={res['support_1']}, R1={res['resistance_1']}")
        return res
    except Exception as e:
        print(f"[Tool Error] <<< get_support_resistance FAIL: {str(e)}")
        return {"error": f"Failed to compute support/resistance: {str(e)}"}

@tool
def get_news_context(symbol: str) -> dict:
    """
    Retrieves the latest news headlines and sentiment context for the specified trading symbol.
    Queries the news aggregator feed to check for catalyst events. 
    Use this to evaluate sentiment and micro-news impact when volatility is high.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        
    Returns:
        dict: List of recent headlines and a basic rule-based sentiment summary.
    """
    print(f"\n[Tool Call] >>> get_news_context: symbol={symbol}")
    try:
        import xml.etree.ElementTree as ET
        query = f"{symbol} stock NSE India"
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10.0)
        response.raise_for_status()
        
        root = ET.fromstring(response.text)
        headlines = []
        for item in root.findall(".//item")[:5]:
            title = item.find("title").text
            if title:
                headlines.append(title)
                
        combined_text = " ".join(headlines).lower()
        positive_words = ["gain", "rise", "jump", "surge", "up", "bull", "buy", "profit", "grow"]
        negative_words = ["fall", "drop", "plunge", "down", "bear", "sell", "loss", "crash", "decline"]
        
        pos_count = sum(1 for w in positive_words if w in combined_text)
        neg_count = sum(1 for w in negative_words if w in combined_text)
        
        if pos_count > neg_count:
            sentiment = "Positive / Bullish Catalyst"
        elif neg_count > pos_count:
            sentiment = "Negative / Bearish Catalyst"
        else:
            sentiment = "Neutral Catalyst"
            
        res = {
            "symbol": symbol,
            "headlines": headlines,
            "sentiment_summary": sentiment
        }
        print(f"[Tool Success] <<< get_news_context: symbol={symbol}, sentiment={sentiment}")
        return res
    except Exception as e:
        print(f"[Tool Warning] <<< get_news_context Google RSS fail, trying local fallback: {str(e)}")
        try:
            fallback_response = httpx.get(f"http://localhost:8087/api/news?symbol={symbol}", timeout=5.0)
            if fallback_response.is_success:
                print(f"[Tool Success] <<< get_news_context: symbol={symbol}, retrieved from local aggregator.")
                return {"symbol": symbol, "news": fallback_response.text, "sentiment_summary": "Retrieved from local aggregator"}
        except Exception as fe:
            print(f"[Tool Error] <<< get_news_context local fallback also failed: {str(fe)}")
        return {"error": f"Failed to fetch news context: {str(e)}"}

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
        thread_id = config.get("configurable", {}).get("thread_id", "default_thread")
        payload = {
            "thread_id": thread_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "price_level": price_level,
            "direction": direction,
            "volume_multiplier": volume_multiplier
        }
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/watch_condition",
            json=payload,
            timeout=10.0
        )
        response.raise_for_status()
        print(f"[Tool Success] <<< watch_price_condition registered watcher for symbol={symbol} on Rust server.")
    except Exception as e:
        print(f"[Tool Error] <<< watch_price_condition FAIL: {str(e)}")
        return f"Error registering watcher on Rust server: {str(e)}"

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
    execution_plan: str
) -> str:
    """
    Declares the final trading decision for the current analysis session.
    Specify 'action' as 'BUY', 'SELL', or 'HOLD'. Provide the conviction score (0-100), 
    setup validation notes, and final entry/SL/TP execution parameters.
    Call this tool to commit the final plan before completing your run.
    
    Args:
        action (str): The final decision, one of: "BUY", "SELL", "HOLD".
        conviction_score (int): Score representing risk confidence (0 to 100).
        setup_validation (str): 2-sentence synthesis of findings or warnings.
        execution_plan (str): Actionable entry/stop-loss/take-profit plan.
        
    Returns:
        str: Confirmation message.
    """
    print(f"\n[Tool Call] >>> declare_trade: action={action}, conviction={conviction_score}%")
    print(f"[Tool Detail] Setup Validation: {setup_validation}")
    print(f"[Tool Detail] Execution Plan: {execution_plan}")
    return f"Trade declared successfully: {action} with {conviction_score}% conviction."
