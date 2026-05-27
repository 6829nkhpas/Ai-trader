import httpx
from langchain_core.tools import tool
from langgraph.types import interrupt

RUST_SERVER_URL = "http://localhost:8084"

@tool
def get_candles(symbol: str, limit: int = 200) -> list:
    """
    Fetch recent OHLCV candles from QuestDB for a given trading symbol.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        limit (int, optional): The maximum number of candles to retrieve. Defaults to 200.
        
    Returns:
        list: A list of candle dictionaries with open, high, low, close, and volume.
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
    Evaluate technical indicators (SMA, EMA, RSI, MACD, OBV, CMF, etc.) and
    patterns to compile a quantitative market consensus report for a symbol.
    
    Args:
        symbol (str): The trading symbol (e.g., "RELIANCE").
        limit (int, optional): Number of candles to use for indicators. Defaults to 200.
        
    Returns:
        dict: The compiled consensus report including trend score, momentum state, etc.
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
def watch_price_condition(
    thread_id: str,
    price_level: float,
    direction: str,
    volume_multiplier: float,
    symbol: str = None
) -> str:
    """
    Register a watcher for a price and volume condition. Execution will pause (interrupt)
    and resume when the market condition is triggered.
    
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

    # Gracefully interrupt graph execution, saving state.
    # When resumed via the /resume endpoint, the return value of interrupt()
    # will contain the triggered candle data.
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
