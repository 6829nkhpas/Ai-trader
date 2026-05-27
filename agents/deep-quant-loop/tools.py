import httpx
from langchain_core.tools import tool
from langgraph.types import interrupt

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
def get_candles(symbol: str, limit: int = 200) -> list:
    """
    Fetches the latest N (up to 200) OHLCV historical candles from the QuestDB database. 
    Use this to analyze granular price action, historical trends, and apply custom mathematical 
    regression models or indicators on the primary execution timeframe.
    
    Args:
        symbol (str): The trading symbol to fetch (e.g. "RELIANCE").
        limit (int, optional): The number of recent candles to retrieve. Defaults to 200.
        
    Returns:
        list: A list of candles, where each candle is a dictionary containing open, high, low, close, and volume.
    """
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "limit": limit},
            timeout=10.0
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return [{"error": f"Failed to retrieve candles from Rust server: {str(e)}"}]

@tool
def get_consensus_report(symbol: str, limit: int = 200) -> dict:
    """
    Retrieves the compiled multi-signal technical consensus report from the Rust quantitative engine.
    Analyzes live Volatility, Momentum, Volume Flow, and Active Candlestick Patterns on the execution 
    timeframe. Use this to determine micro-structure signals, indicator crossovers, and strategies bias.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        limit (int, optional): Number of candles to use for indicators computation. Defaults to 200.
        
    Returns:
        dict: The compiled consensus report with trend score, momentum state, and active patterns.
    """
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_consensus",
            json={"symbol": symbol, "limit": limit},
            timeout=10.0
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": f"Failed to compile consensus report: {str(e)}"}

@tool
def get_multi_tf_trend(symbol: str) -> dict:
    """
    Establishes the multi-timeframe directional trend bias for the specified trading symbol.
    Fetches historical candle data and calculates short, medium, and long-term trend alignment
    across 1-Hour (1H), 4-Hour (4H), and 1-Day (1D) equivalent horizons.
    Use this first to avoid trading against the macro trend.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        
    Returns:
        dict: Directional trend bias ("Bullish" or "Bearish") across 1H, 4H, and 1D horizons.
    """
    try:
        response = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "limit": 200},
            timeout=10.0
        )
        response.raise_for_status()
        candles = response.json()
        closes = [c["close"] for c in candles if "close" in c]
        if not closes:
            return {"error": "No candle price data found for trend analysis."}
        
        # Calculate EMA-9, EMA-21, EMA-50, and EMA-100 equivalents
        ema9 = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)
        ema50 = calculate_ema(closes, 50)
        ema100 = calculate_ema(closes, 100)
        
        trend_1h = "Bullish" if ema9 > ema21 else "Bearish"
        trend_4h = "Bullish" if ema21 > ema50 else "Bearish"
        trend_1d = "Bullish" if ema50 > ema100 else "Bearish"
        
        return {
            "symbol": symbol,
            "trend_1h": trend_1h,
            "trend_4h": trend_4h,
            "trend_1d": trend_1d,
            "indicators": {
                "ema_9": round(ema9, 2),
                "ema_21": round(ema21, 2),
                "ema_50": round(ema50, 2),
                "ema_100": round(ema100, 2)
            }
        }
    except Exception as e:
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
            return {"error": "Insufficient candle data to determine support/resistance."}
            
        h = max(highs[-20:])
        l = min(lows[-20:])
        c = closes[-1]
        
        pivot = (h + l + c) / 3.0
        r1 = 2 * pivot - l
        s1 = 2 * pivot - h
        r2 = pivot + (h - l)
        s2 = pivot - (h - l)
        
        return {
            "symbol": symbol,
            "pivot_point": round(pivot, 2),
            "resistance_1": round(r1, 2),
            "support_1": round(s1, 2),
            "resistance_2": round(r2, 2),
            "support_2": round(s2, 2),
            "recent_high": round(h, 2),
            "recent_low": round(l, 2)
        }
    except Exception as e:
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
            
        return {
            "symbol": symbol,
            "headlines": headlines,
            "sentiment_summary": sentiment
        }
    except Exception as e:
        try:
            fallback_response = httpx.get(f"http://localhost:8087/api/news?symbol={symbol}", timeout=5.0)
            if fallback_response.is_success:
                return {"symbol": symbol, "news": fallback_response.text, "sentiment_summary": "Retrieved from local aggregator"}
        except:
            pass
        return {"error": f"Failed to fetch news context: {str(e)}"}

@tool
def watch_price_condition(
    thread_id: str,
    price_level: float,
    direction: str,
    volume_multiplier: float,
    symbol: str = None
) -> str:
    """
    Registers a target condition watcher with the backend watcher service. Graph execution 
    will automatically pause (interrupt) and enter a 'watching' state. Execution resumes 
    automatically with meeting candle data when the condition triggers.
    
    Args:
        thread_id (str): The active thread/session identifier for state persistence.
        price_level (float): The price level to watch.
        direction (str): Trigger direction, either "above" (or "up") or "below" (or "down").
        volume_multiplier (float): Volume threshold multiplier relative to the 20-period average.
        symbol (str, optional): The symbol to watch. If omitted, the server will default to the current active symbol.
        
    Returns:
        str: Description of the triggered event once resumed.
    """
    try:
        payload = {
            "thread_id": thread_id,
            "symbol": symbol,
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
    except Exception as e:
        return f"Error registering watcher on Rust server: {str(e)}"

    triggered_candle = interrupt(
        {
            "status": "watching_registered",
            "thread_id": thread_id,
            "symbol": symbol,
            "price_level": price_level,
            "direction": direction,
            "volume_multiplier": volume_multiplier
        }
    )
    
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
    return f"Trade declared successfully: {action} with {conviction_score}% conviction."
