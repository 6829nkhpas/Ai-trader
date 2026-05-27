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
2. MICROSTRUCTURE: Call `get_consensus_report` dynamically on different timeframes (e.g., '5m', '15m') to find confluence.
3. KEY LEVELS: Call `get_support_resistance` to identify exact liquidity zones.
</order_of_operations>

<self_verification_protocol>
BEFORE you are allowed to call `declare_trade`, you must act as an aggressive Risk Manager against your own idea.
Ask yourself:
- Is my Stop Loss too tight compared to current volatility?
- Am I trading against the Macro Trend?
- Is the Risk:Reward ratio worse than 1:2?
If the answer to ANY of these is YES, you must scrap the trade. You must either analyze a different timeframe to find a better entry, or call `watch_price_condition` to wait for a safer pullback. 
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
