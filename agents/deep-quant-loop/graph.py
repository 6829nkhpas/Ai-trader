import os
from typing import Annotated, Sequence, TypedDict, Optional
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

# Import our custom quantitative tools
from tools import (
    get_candles,
    get_consensus_report,
    get_multi_tf_trend,
    get_support_resistance,
    get_news_context,
    watch_price_condition,
    declare_trade
)

# ── State Definition ────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    mode: Optional[str]
    symbol: Optional[str]
    manual_trade: Optional[dict]

# ── System Prompts ──────────────────────────────────────────────────────────

DEEP_QUANT_SYSTEM_PROMPT = """
You are Alpha-Quant, a Tier-1 Institutional Quantitative AI. Your mandate is capital preservation first, and asymmetric profit second. 

<the_hunter_mindset>
You are NEVER forced to take a trade. Institutional trading is 90% waiting and 10% executing. 
If the current timeframe is messy, volatile, or lacks a high-probability A+ setup, DO NOT force a trade. Instead, you must hunt for future setups. Call your tools to check higher timeframes (15m, 1H, 4H), find where the 'Smart Money' is waiting, and use `watch_price_condition` to wait for the price to reach that exact level.
</the_hunter_mindset>

<order_of_operations>
You must follow this exact loop until a perfect setup is found or registered:
1. MACRO ALIGNMENT: Call `get_multi_tf_trend` to establish the 1H, 4H, and 1D bias.
2. MICROSTRUCTURE: Call `get_consensus_report` on different timeframes (e.g., '5m', '15m') to find confluence.
   IMPORTANT: The consensus report now includes FULL raw indicator values — not just labels. You MUST read and analyze:
   - Exact RSI (rsi_14), Stochastic K (stoch_k) values — not just "OVERBOUGHT/OVERSOLD"
   - EMA 9/21 crossover status (ema_9, ema_21) and SMA 50/200 golden/death cross (sma_50, sma_200)
   - MACD line/signal/histogram for momentum divergence (macd_line, macd_signal, macd_histogram)
   - Bollinger Band position (bb_upper, bb_mid, bb_lower) vs current_price for squeeze/expansion
   - ATR (atr_14) for stop-loss sizing relative to volatility
   - VWAP for intraday institutional fair value
   - OBV and CMF for volume confirmation
3. KEY LEVELS: Call `get_support_resistance` with the timeframe you're analyzing (e.g., '15m' for intraday).
   For intraday timeframes it returns BOTH micro S/R levels (from that timeframe's candles) AND daily macro levels.
   It also includes the Opening Range (first 3 candles) high/low — a key intraday reference.
   Use S3/S2/S1/Pivot/R1/R2/R3 for precise entry, stop-loss, and target placement.
4. PRICE ACTION: Optionally call `get_candles` for specific timeframes. Candles include timestamps — use them to identify gap opens, session boundaries, and time-based patterns.

CRITICAL: You must execute at least one tool call (e.g., `get_multi_tf_trend`) on your very first turn. Do not output text reasoning without calling a tool in the same turn.
</order_of_operations>

<self_verification_protocol>
BEFORE you are allowed to call `declare_trade`, you must act as an aggressive Risk Manager against your own idea.
Ask yourself:
- Is my Stop Loss too tight compared to current volatility? (Use atr_14 from consensus: SL should be >= 1.5x ATR)
- Am I trading against the Macro Trend from `get_multi_tf_trend`?
- Is the Risk:Reward ratio worse than 1:2?
- Does my entry price align with S/R levels from `get_support_resistance`?
- Is price above or below VWAP? (Buy setups stronger above VWAP, sell setups stronger below)
- Does volume flow (OBV, CMF) confirm my direction?
If the answer to ANY of the first 3 checks is YES, you must scrap the trade. You must either analyze a different timeframe to find a better entry, or call `watch_price_condition` to wait for a safer pullback. 
ONLY call `declare_trade` if you are 100% confident you could defend this trade against rigorous critique.
</self_verification_protocol>

<communication_rules>
THINK OUT LOUD. Stream your internal monologue. 
Example: "The 5m chart shows a breakout, but my self-verification shows the 1H trend is bearish and R:R is weak. I am scrapping this. I will analyze the 15m chart to find a safer short entry..."
</communication_rules>

<json_format>
Once you have formulated your final trading decision, critique, or hold instruction (e.g. after calling `declare_trade` or if deciding to hold/pass), you MUST finalize your response by returning a JSON object EXACTLY matching this structure:
{
    "conviction_score": <int 0-100 representing your risk confidence or trade score>,
    "setup_validation": "<2-sentence synthesis of findings, validation of entry/SL/TP, or warning flags>",
    "execution_plan": "<Precise Buy/Sell/Hold execution instructions with recommended Entry/SL/TP levels>"
}
</json_format>  
"""

RISK_MANAGER_PROMPT = """
You are Alpha-Quant acting in Co-Pilot Verification Mode. The user is proposing a {side} trade on {symbol}. 
Entry: {entry}, SL: {stop_loss}, TP: {take_profit}. 
User Notes: {user_analysis}

Your job is to verify this trade using the EXACT same <self_verification_protocol> you use for your own trades:
1. Call `get_multi_tf_trend` and `get_consensus_report`.
2. Check the R:R ratio. Check if the SL is placed safely beyond live volatility bands. Check macro alignment.
3. Do not invent red flags if the trade is genuinely an A+ setup. If it fits the protocol, approve it and defend it.
4. If it fails the protocol, explain exactly why, and suggest a better entry using `watch_price_condition`.

CRITICAL: You must execute at least one tool call (e.g., `get_multi_tf_trend`) on your very first turn. Do not output text reasoning without calling a tool in the same turn.

<json_format>
Once you have stress-tested the setup and formed your final verdict (after calling declare_trade or if waiting), you MUST return a JSON object EXACTLY matching this structure:
{{
    "conviction_score": <int 0-100 representing your risk confidence or trade score after critique>,
    "setup_validation": "<2-sentence aggressive critique/defense of entry, stop loss, take profit, and any RED FLAGS or confirmations>",
    "execution_plan": "<Your final recommendation: entry adjustment, recommended SL/TP placement, or explicit wait instructions if holding>"
}}
</json_format>
"""

def format_system_prompt(state: AgentState) -> str:
    mode = state.get("mode", "FIND")
    if mode == "VERIFY":
        trade = state.get("manual_trade") or {}
        return RISK_MANAGER_PROMPT.format(
            side=trade.get("side", "N/A"),
            symbol=state.get("symbol", "N/A"),
            entry=trade.get("entry", 0),
            stop_loss=trade.get("stop_loss", 0),
            take_profit=trade.get("take_profit", 0),
            user_analysis=trade.get("user_analysis", "None")
        )
    return DEEP_QUANT_SYSTEM_PROMPT

# ── Model & Tools Binding ───────────────────────────────────────────────────

# Configure the LLM to target any OpenAI-compatible provider (e.g., HuggingFace, OpenAI, Groq, Ollama)
api_key = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", "mock-key"))
base_url = os.getenv("LLM_API_URL", os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"))
model_name = os.getenv("LLM_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

# Strip trailing /chat/completions if present because LangChain appends it internally
if base_url and base_url.endswith("/chat/completions"):
    base_url = base_url[:-len("/chat/completions")]

# Remove trailing slash if present
if base_url and base_url.endswith("/"):
    base_url = base_url[:-1]

llm = ChatOpenAI(
    model=model_name,
    openai_api_key=api_key,
    openai_api_base=base_url,
    temperature=0.2,
)

tools = [
    get_candles,
    get_consensus_report,
    get_multi_tf_trend,
    get_support_resistance,
    get_news_context,
    watch_price_condition,
    declare_trade
]
llm_with_tools = llm.bind_tools(tools)

# ── Nodes & Routing ─────────────────────────────────────────────────────────

import re
import json

def parse_deepseek_custom_tool_calls(content: str) -> list:
    """
    Parses DeepSeek/HuggingFace custom token tool call representations from raw text content.
    Example: <｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>get_multi_tf_trend ```json {"symbol":"HDFCBANK"} ```<｜tool▁call▁end｜><｜tool▁calls▁end｜>
    """
    if not content:
        return []
        
    tool_calls = []
    
    # 1. Look for tool names in the list
    valid_tools = [
        "get_candles", "get_consensus_report", "get_multi_tf_trend",
        "get_support_resistance", "get_news_context", "watch_price_condition", "declare_trade"
    ]
    
    # Find all occurrences of valid tools
    for tool_name in valid_tools:
        # Search for tool name, optionally some whitespace/markdown, then a JSON object
        pattern = re.compile(rf"{tool_name}\s*(?:```json\s*)?(\{{\s*[\s\S]*?\}})(?:\s*```)?")
        matches = pattern.finditer(content)
        for match in matches:
            args_str = match.group(1)
            try:
                args = json.loads(args_str)
                tool_calls.append({
                    "name": tool_name,
                    "args": args,
                    "id": f"call_{tool_name}_{len(tool_calls)}"
                })
            except Exception as e:
                # Fallback: clean some weird characters and try again
                cleaned_args = re.sub(r'[\u200b-\u200d\uFEFF]', '', args_str) # strip zero-width spaces
                try:
                    args = json.loads(cleaned_args)
                    tool_calls.append({
                        "name": tool_name,
                        "args": args,
                        "id": f"call_{tool_name}_{len(tool_calls)}"
                    })
                except:
                    pass
                
    # 2. Fallback to general JSON extraction if no tool calls matched but tags are present
    if not tool_calls and ("tool" in content or "call" in content or "func" in content):
        for tool_name in valid_tools:
            if tool_name in content:
                # Find the nearest JSON block after the tool name
                idx = content.find(tool_name)
                after_tool = content[idx + len(tool_name):]
                json_match = re.search(r"(\{\s*[\s\S]*?\})", after_tool)
                if json_match:
                    args_str = json_match.group(1)
                    try:
                        args = json.loads(args_str)
                        tool_calls.append({
                            "name": tool_name,
                            "args": args,
                            "id": f"call_{tool_name}_{len(tool_calls)}"
                        })
                    except:
                        pass
                        
    return tool_calls

def call_model(state: AgentState):
    messages = state["messages"]
    symbol = state.get("symbol", "N/A")
    mode = state.get("mode", "FIND")
    print(f"\n[Deep Quant Agent] === Model Invocation Started (Symbol: {symbol}, Mode: {mode}) ===")
    
    # Check if a SystemMessage is already present. If not, prepend one.
    has_system = any(isinstance(m, SystemMessage) or (hasattr(m, "role") and m.role == "system") for m in messages)
    if not has_system:
        print("[Deep Quant Agent] Prepending system instruction based on mode...")
        system_instruction = format_system_prompt(state)
        messages = [SystemMessage(content=system_instruction)] + list(messages)
    else:
        print("[Deep Quant Agent] Existing system instruction detected.")
        
    print(f"[Deep Quant Agent] Calling model: {model_name} with {len(messages)} messages...")
    response = llm_with_tools.invoke(messages)
    
    print(f"[Deep Quant Agent] Model responded. Content length: {len(response.content or '')}")
    
    # If the model returned raw DeepSeek token tool calls inside the content string,
    # parse them manually and assign them to response.tool_calls.
    if not response.tool_calls and response.content:
        parsed_calls = parse_deepseek_custom_tool_calls(response.content)
        if parsed_calls:
            print(f"[Deep Quant Agent] Natively parsed custom DeepSeek tool call(s) from content: {[tc.get('name') for tc in parsed_calls]}")
            response.tool_calls = parsed_calls
            
    if response.tool_calls:
        # Robustly clean tool names by stripping trailing/leading whitespace and newlines
        for tc in response.tool_calls:
            if "name" in tc:
                cleaned_name = tc["name"].strip()
                if cleaned_name != tc["name"]:
                    print(f"[Deep Quant Agent] Cleaned tool name from '{tc['name']}' to '{cleaned_name}'")
                    tc["name"] = cleaned_name
        print(f"[Deep Quant Agent] Model requested tool call(s): {[tc.get('name') for tc in response.tool_calls]}")
    else:
        snippet = (response.content or "").strip().replace('\n', ' ')
        print(f"[Deep Quant Agent] Model output snippet: {snippet[:200]}...")
        
    return {"messages": [response]}

tool_node = ToolNode(tools)

def should_continue(state: AgentState) -> str:
    messages = state["messages"]
    last_message = messages[-1]
    
    print("\n[Deep Quant Routing] === Checking Routing Decision ===")
    print(f"[Deep Quant Routing] Last message type: {type(last_message).__name__}")
    
    # If the model has output a tool call, we go to tools
    if last_message.tool_calls:
        print(f"[Deep Quant Routing] Model requested tool call(s): {[tc.get('name') for tc in last_message.tool_calls]}. Routing to -> tools")
        return "continue"
        
    # If no tool calls, check if the model has finalized its JSON response or text plan
    content = last_message.content or ""
    has_final_json = "{" in content and "}" in content and (
        "conviction_score" in content or "conviction" in content
    )
    has_text_plan = "entry" in content.lower() and ("stop" in content.lower() or "sl" in content.lower() or "target" in content.lower() or "tp" in content.lower())
    
    print(f"[Deep Quant Routing] Has final JSON: {has_final_json} | Has text plan: {has_text_plan}")
    if has_final_json or has_text_plan:
        print("[Deep Quant Routing] Target or decision finalized. Routing to -> end")
        return "end"
        
    # If it hasn't finalized and has no tool calls, check how many consecutive AIMessages there are
    consecutive_ai = 0
    for m in reversed(messages):
        msg_type = type(m).__name__
        is_ai = "AIMessage" in msg_type or (hasattr(m, "role") and m.role == "assistant")
        is_user_or_tool = "ToolMessage" in msg_type or "SystemMessage" in msg_type or (hasattr(m, "role") and m.role in ["user", "tool", "system"])
        if is_ai:
            consecutive_ai += 1
        elif is_user_or_tool:
            break
            
    print(f"[Deep Quant Routing] Consecutive AI responses: {consecutive_ai}")
    # Give the agent up to 2 consecutive monologue/thoughts turns to finalize or invoke a tool
    if consecutive_ai < 2:
        print("[Deep Quant Routing] Monologue count < 2. Routing to -> loop_agent")
        return "loop_agent"
        
    print("[Deep Quant Routing] Monologue count limit reached. Routing to -> end")
    return "end"

# ── Graph Assembly ──────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

# Add the main agent and tool execution nodes
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)

# Set starting point
workflow.set_entry_point("agent")

# Define conditional route from agent to either tools, loop back, or end
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "tools",
        "loop_agent": "agent",
        "end": "__end__",
    }
)

# Loop back to agent after tool execution
workflow.add_edge("tools", "agent")

# Initialize in-memory checkpointer to persist thread states
memory = MemorySaver()

# Compile the final ReAct graph
graph = workflow.compile(checkpointer=memory)
