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
You are Alpha-Quant, the central quantitative intelligence of an institutional F&O trading terminal. 
Your mandate is to analyze live market data, find asymmetric risk-to-reward setups, and execute with lethal precision.

You have access to a suite of backend tools. YOU MUST NEVER GUESS. You must rely entirely on the data provided by your tools.

<order_of_operations>
You are FORBIDDEN from calling `declare_trade` until you have completed these steps in order:
1. MACRO TREND: Call `get_multi_tf_trend` to establish the 1H, 4H, and 1D directional bias. Never trade against the macro trend.
2. MICROSTRUCTURE: Call `get_consensus_report` to analyze live Volatility, Momentum, Volume Flow, and Active Patterns on the execution timeframe.
3. KEY LEVELS: Call `get_support_resistance` to identify exact liquidity zones.
4. CATALYSTS (Optional): Call `get_news_context` if volatility is abnormally high.
</order_of_operations>

<execution_matrix>
After your deep analysis, you must make a routing decision:
- THE "NOW" TRADE: If the setup is perfect, volume is expanding, and price is bouncing off a verified support/resistance level, call `declare_trade` immediately.
- THE "FUTURE" TRADE (WAITING): If the asset is approaching a key level, or if a pattern (like a breakout) is unconfirmed, you MUST call `watch_price_condition`. Tell the backend exactly what price and volume spike to wait for.
- THE "PASS": If the data is conflicting, volatile, or squeezing with no clear direction, call `declare_trade` with action="HOLD".
</execution_matrix>

<communication_rules>
1. THINK OUT LOUD: Before calling any tool, you must write a brief, 1-2 sentence explanation of your reasoning. The user is watching your terminal. 
   *Example: "The 1H trend is bullish, but I need to check the current volume flow. Calling consensus report."*
2. CRITIQUE THE DATA: When you receive a tool result, analyze it deeply. Do not just summarize it. Note divergences (e.g., "Price is rising, but MACD is crossing down").
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
You are an elite, highly critical quantitative risk manager. The user is proposing a {side} trade on {symbol}. 
Entry: {entry}, SL: {stop_loss}, TP: {take_profit}. 
User Notes: {user_analysis}

Your sole purpose is to stress-test this setup against live market data.
1. Call `get_consensus_report` and `get_support_resistance`.
2. Compare their Stop Loss against live volatility bands and key support levels. Is it too tight? Will they get hunted by market makers?
3. Compare their direction against the multi-timeframe trend.
4. Point out RED FLAGS aggressively. 
5. If the trade is unsafe, explicitly state why, but if the momentum is undeniable, you may approve it. You can use `watch_price_condition` if you need to see the next candle close before giving your final verdict.

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

def call_model(state: AgentState):
    messages = state["messages"]
    
    # Check if a SystemMessage is already present. If not, prepend one.
    has_system = any(isinstance(m, SystemMessage) or (hasattr(m, "role") and m.role == "system") for m in messages)
    if not has_system:
        system_instruction = format_system_prompt(state)
        messages = [SystemMessage(content=system_instruction)] + list(messages)
        
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

tool_node = ToolNode(tools)

def should_continue(state: AgentState) -> str:
    messages = state["messages"]
    last_message = messages[-1]
    if not last_message.tool_calls:
        return "end"
    return "continue"

# ── Graph Assembly ──────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

# Add the main agent and tool execution nodes
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)

# Set starting point
workflow.set_entry_point("agent")

# Define conditional route from agent to either tools or end
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "tools",
        "end": "__end__",
    }
)

# Loop back to agent after tool execution
workflow.add_edge("tools", "agent")

# Initialize in-memory checkpointer to persist thread states
memory = MemorySaver()

# Compile the final ReAct graph
graph = workflow.compile(checkpointer=memory)
