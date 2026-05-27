import os
from typing import Annotated, Sequence, TypedDict
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

# Import our custom proxy tools
from tools import get_candles, get_consensus_report, watch_price_condition

# ── State Definition ────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]

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

tools = [get_candles, get_consensus_report, watch_price_condition]
llm_with_tools = llm.bind_tools(tools)

# ── Nodes & Routing ─────────────────────────────────────────────────────────

def call_model(state: AgentState):
    messages = state["messages"]
    
    # Check if a SystemMessage is already present. If not, prepend one.
    has_system = any(isinstance(m, SystemMessage) or (hasattr(m, "role") and m.role == "system") for m in messages)
    if not has_system:
        system_instruction = (
            "You are a seasoned, ruthless Quantitative Trading AI. \n"
            "Your primary directive is capital preservation and high-probability directional conviction.\n"
            "The user will ask you to analyze a specific trading ticker/symbol (e.g., 'TMPV', 'RELIANCE', etc.). \n"
            "You must IMMEDIATELY execute tool calls to `get_consensus_report` and `get_candles` for the provided symbol. \n"
            "Do NOT ask the user for clarification, do NOT assume the symbol is an acronym, and do NOT discuss what you need. "
            "Simply call the tools for the requested symbol. Treat the requested symbol name literally as the symbol parameter for the tools.\n"
            "After compiling the tool outputs, if a setup is confirmed, finalise your analysis by outputting a JSON object "
            "wrapped in your final message, matching this exact structure:\n"
            "{\n"
            "  \"conviction_score\": <int 0-100>,\n"
            "  \"setup_validation\": \"<2-sentence aggressive synthesis of historical similarities and signals>\",\n"
            "  \"execution_plan\": \"<Actionable Buy/Sell/Hold plan with precise entry/SL/TP levels>\"\n"
            "}"
        )
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
