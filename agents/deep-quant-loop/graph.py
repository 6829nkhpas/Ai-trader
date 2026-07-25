import os
import time
from typing import Annotated, Sequence, TypedDict, Optional, Literal, List
from dataclasses import dataclass, field
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, BaseMessage, ToolMessage
import json


# ── .env loader (dependency-free) ────────────────────────────────────────────
# The agent reads its LLM/provider configuration from the process environment
# (os.getenv below). When launched via start_system.ps1 those vars are injected
# once at script start — which means editing .env and restarting ONLY the Python
# process would otherwise pick up nothing. To make "edit .env → restart python"
# reliable (and to support running `python main.py` directly), we load the
# repo-root .env here at import time, before any os.getenv call.
#
# Uses setdefault semantics: a variable already present in the real environment
# (e.g. injected by the launcher or set by the OS) always wins over the file, so
# this never clobbers an intentionally-exported value.
def _load_repo_dotenv() -> None:
    here = os.path.abspath(os.path.dirname(__file__))
    # Walk up from agents/deep-quant-loop/ to the repository root looking for .env.
    current = here
    for _ in range(8):  # bounded walk; repo root is 2 levels up but be generous
        candidate = os.path.join(current, ".env")
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key = key.strip()
                        # Strip surrounding single/double quotes and whitespace.
                        value = value.strip().strip('"').strip("'")
                        if key:
                            os.environ.setdefault(key, value)
            except OSError as e:
                print(f"[deep-quant] Could not read {candidate}: {e}")
            return
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    print("[deep-quant] No .env found while walking up from agent directory.")

_load_repo_dotenv()


# ── AIMessage Monkeypatch to robustly fix string args in tool calls ──────────
original_init = AIMessage.__init__

def patched_init(self, *args, **kwargs):
    if "tool_calls" in kwargs and kwargs["tool_calls"]:
        fixed_calls = []
        for tc in kwargs["tool_calls"]:
            cleaned_tc = dict(tc)
            if "name" in cleaned_tc and isinstance(cleaned_tc["name"], str):
                cleaned_tc["name"] = cleaned_tc["name"].strip()
            if "args" in cleaned_tc and isinstance(cleaned_tc["args"], str):
                try:
                    cleaned_tc["args"] = json.loads(cleaned_tc["args"])
                except Exception as e:
                    print(f"[AIMessage Patch] Failed to parse JSON args: {e}")
            fixed_calls.append(cleaned_tc)
        kwargs["tool_calls"] = fixed_calls
    original_init(self, *args, **kwargs)

AIMessage.__init__ = patched_init

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

# Import our custom quantitative tools
from tools import (
    get_candles,
    get_consensus_report,
    get_multi_tf_trend,
    get_chart_patterns,
    get_support_resistance,
    get_volume_profile,
    get_news_context,
    get_prediction,
    get_trade_performance,
    get_market_regime,
    get_relative_strength,
    get_order_flow,
    get_forecast,
    get_session_context,
    get_options_analytics,
    # Symbol_Class resolver (single source of truth beside INDEX_UNDERLYINGS) —
    # used to enable options as a first-class confirmation for an index in the
    # non-F&O workspaces. Do NOT duplicate the index set.
    classify_symbol_class,
    get_event_risk,
    watch_price_condition,
    declare_trade,
    # Reused to build the committed Management_Plan for the defensibility record
    # from the declared ``management_plan`` dict, merging the committed bracket as
    # defaults exactly as ``declare_trade`` did when it validated the plan — so the
    # management entry cites the SAME plan that was committed (R9.1, R9.2).
    _coerce_management_plan,
    # Regime_Label enum sets — reused to recognise a usable get_market_regime
    # label (vs an Unavailable_Marker) when building the defensibility record.
    REGIME_TREND_STATES,
    REGIME_VOLATILITY_STATES,
    REGIME_FAVORABILITY,
    _REGIME_MEASURE_FIELDS,
    # Relative_Strength_Label enum sets — reused to recognise a usable
    # get_relative_strength label (vs an Unavailable_Marker) when building the
    # defensibility record.
    INDEX_DIRECTIONS,
    RELATIVE_STRENGTH_STATES,
    ALIGNMENT_VALUES,
    _RS_MEASURE_FIELDS,
    # Forecast_Direction enum set + measure fields — reused to recognise a usable
    # get_forecast result (vs an Unavailable_Marker) when building the
    # defensibility record. (Forecast_Alignment reuses the shared ALIGNMENT_VALUES.)
    FORECAST_DIRECTIONS,
    _FORECAST_MEASURE_FIELDS,
    # Session_Label enum sets — reused to recognise a usable get_session_context
    # label (vs an Unavailable_Marker) when building the defensibility record.
    SESSION_PHASES,
    TIME_FAVORABILITY,
    # Options_Bias_Label enum sets — reused to recognise a usable
    # get_options_analytics label (vs an Unavailable_Marker) when building the
    # defensibility record / options entry (Options_Bias_State and Chain_Context;
    # Alignment reuses the shared ALIGNMENT_VALUES imported above).
    OPTIONS_BIAS_STATES,
    OPTIONS_CHAIN_CONTEXTS,
    # Event_Risk_State / Event_Recommendation enum sets — reused to recognise a
    # usable get_event_risk Event_Assessment (vs an Unavailable_Marker) when
    # building the defensibility record / event entry (earnings-event-risk-gate).
    EVENT_RISK_STATES,
    EVENT_RECOMMENDATIONS,
)

# Trade_Journal — measurement & feedback loop (Phase 2). Records every committed
# decision and scores it later, so the agent can audit its realized edge.
import journal

# Adaptive Opportunity Engine — the pure, deterministic loop-control core
# (adaptive-opportunity-engine). The graph consults it for the tiered opportunity
# ladder, the bounded-hunt Watch_Cap / Session_Budget termination predicates, the
# invalidation post-mortem re-arm gate, the interim Best_Current_Read, deterministic
# session-context pruning, and the low-cardinality tier tag. The engine adds NO new
# market-data source and never relaxes the Trade_Validator — it changes decision
# policy and loop control only (Requirement 10.5). Keeping it a separate pure module
# leaves it unit/property-testable in isolation.
import opportunity

# Trade_Manager — the single source of truth for the exit-simulation math
# (trade-management). The defensibility management entry sources the committed
# Management_Plan and, where candles are available, cites the simulated
# Exit_Breakdown + Realized_R from ``trade_manager.simulate_plan`` — never
# reimplementing the multi-leg fill / breakeven / trail logic (R9.1, R9.2, AD-2).
import trade_manager

# Multi-Agent Debate pure core (multi-agent-debate). The Bull/Bear/Judge roles
# resolve their per-role model + bounds from the environment via
# ``resolve_debate_config`` and emit structured stances parsed by
# ``parse_stance`` (serialized for the state / defensibility record via
# ``stance_to_dict``). Importing the pure module keeps the LLM-free debate logic
# unit/property-testable in isolation.
from debate import (
    parse_stance,
    resolve_debate_config,
    stance_to_dict,
    DebateConfig,
    # The three categorical Debate_Consensus values — used to validate the
    # Judge verdict threaded into the defensibility record (multi-agent-debate,
    # R7.1) so an unrecognized consensus degrades gracefully rather than being
    # surfaced verbatim.
    DEBATE_CONSENSUS_VALUES,
    # Consensus classification + conviction derivation — the deterministic heart
    # of the Judge's synthesis (multi-agent-debate, R4.1/R4.4). The Judge node
    # reconstructs the stored Bull/Bear stances and feeds them through these pure
    # functions to set debate_consensus / debate_conviction.
    classify_consensus,
    derive_conviction,
    judge_directional_bias,
    # One Bull-then-Bear exchange is TURNS_PER_ROUND model turns; used to derive
    # the 1-based round index from the bounded turn counter so the round-looping
    # (bull → bear → [next round] → judge) is idempotent (R3.6, R6.1).
    TURNS_PER_ROUND,
)

# The resolved Adaptive Opportunity Engine configuration, read once from the
# environment at import time (mirrors the module-load resolution of the LLM config
# below). Every documented default is applied for any unset/invalid var, so the
# engine is always safely configured. Reassignable by tests to exercise specific
# Watch_Cap / Session_Budget / tier bounds. Setting OPPORTUNITY_LOWER_TIERS_ENABLED=false
# restores the pre-engine A+-only policy (the engine is a strict superset).
_OPPORTUNITY_CFG = opportunity.resolve_opportunity_config()

# ── State Definition ────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    mode: Optional[str]
    symbol: Optional[str]
    manual_trade: Optional[dict]
    timeframe: Optional[str]
    # Workspace profile the user is in (INTRADAY / SWING / INVESTOR / FNO). Drives
    # the profile-specific directive prepended in `format_system_prompt` so the
    # agent adapts which data domain it prioritizes and over what horizon. ADDITIVE
    # and Optional — a run that omits it defaults to INTRADAY behaviour.
    profile: Optional[str]
    # Expiry selected in the F&O workspace, as an ISO "YYYY-MM-DD" string ('' or
    # None => the options engine's nearest available expiry). Injected into the
    # FNO profile directive so the agent analyzes the exact expiry the user is
    # viewing. ADDITIVE and Optional — only consulted on an FNO-profile run.
    fno_expiry: Optional[str]
    # Optional LLM model override chosen in the UI composer ('' / None => the
    # deployment default model). When set, the run's model binding uses it via
    # `_build_profile_llm_for_model`, resolved against the same provider gateway
    # as the default llm. ADDITIVE and Optional — a run that omits it uses the
    # default binding, so legacy behaviour is unchanged.
    model: Optional[str]
    # ── Deterministic loop-control state (Requirement 2) ──────────────────────
    # `decision` is the single authoritative completion signal. It is set ONLY
    # by a validated declare_trade (its structured args) or by the forced-HOLD
    # path — never inferred from keyword matching on reasoning prose (R2.7).
    decision: Optional[dict]
    # Count of consecutive turns in which the model produced reasoning with no
    # tool calls. Reset to 0 on any turn that issues tool calls (R2.3, R2.5).
    reasoning_turns: int
    # True once any market-data Analysis_Tool has returned usable data in the
    # current run. Maintained here; gating on it is handled in a later task.
    market_data_seen: bool
    # ── Trade Q&A mode bookkeeping (Requirement 18) ──────────────────────────
    # Consecutive Trade_QA_Mode turns taken in this thread, used purely to bound
    # the Q&A tool-fetch loop (R18.4). It never affects the committed decision —
    # the Declared_Trade is immutable while answering questions (R18.6).
    qa_turns: int
    # ── VERIFY-mode devil's-advocate bookkeeping (multi-agent-debate, R11) ────
    # ADDITIVE / Optional latch: True once the Bear_Agent devil's advocate has
    # been run against the user-proposed trade in a VERIFY run, so it is invoked
    # exactly once per run. Only ever set on a VERIFY run; FIND / DEBATE / QA runs
    # never populate or read it, so their behaviour is completely unchanged. It
    # never influences the committed decision — the VERIFY verdict path stays the
    # sole decision authority (R11.3).
    verify_devils_advocate_done: Optional[bool]
    # ── Multi-Agent Debate bookkeeping (multi-agent-debate, R1/R3/R4/R6) ──────
    # All fields below are ADDITIVE and Optional/defaulted: a non-DEBATE run
    # (FIND / VERIFY / QA) never sets or reads them, so legacy behaviour is
    # completely unchanged. They are populated only on a DEBATE-mode run.
    #
    # Current phase within a DEBATE run: "research" while the shared-evidence
    # gathering loop runs (declaration suppressed), "debate" once the bull/bear/
    # judge roles take over. None for every non-DEBATE run (R2.1).
    phase: Optional[str]
    # Total bounded model turns taken across all debate roles. Guarantees the
    # debate always terminates against DEBATE_MAX_TURNS (R6.2).
    debate_turns: int
    # Current debate round index (1-based), bounded by DEBATE_ROUNDS (R3.6, R6.1).
    debate_round: int
    # Serialized DebateStance dicts emitted by the Bull / Bear roles. None until
    # the corresponding role has produced a stance (R3.3).
    bull_stance: Optional[dict]
    bear_stance: Optional[dict]
    # Judge verdict: the classified disagreement structure
    # (strong_agree | lean | contested) and the derived conviction in [0, 100]
    # (R4.1, R4.4). None until the Judge has run.
    debate_consensus: Optional[str]
    debate_conviction: Optional[int]
    # ── Adaptive Opportunity Engine bookkeeping (adaptive-opportunity-engine) ──
    # All fields below are ADDITIVE and Optional/defaulted, maintained by
    # `call_model` / `tool_node` exactly like `reasoning_turns`. They drive the
    # bounded hunt (Watch_Cap + Session_Budget), the invalidation post-mortem, the
    # cheap-resume delta re-check, and tier tagging. A run that reads them before
    # they are set treats them as their zero/None default, so legacy behaviour is
    # preserved when the engine takes no action.
    #
    # The committed tier recorded on the decision (a_plus | b_continuation | scalp
    # | stand_aside). None until a decision is committed.
    opportunity_tier: Optional[str]
    # Watch_Cycles registered this session — each watch registration AND each
    # invalidation-driven re-arm increments it; converges on Watch_Cap (R3.1, R4.4).
    watch_cycles: int
    # Model turns taken this session (Session_Budget turn accounting, R3.2).
    session_turns: int
    # Monotonic wall-clock seconds stamped on the first /run turn (R3.2). None until
    # stamped, so the wall-clock budget cannot fire before the run starts.
    session_started_at: Optional[float]
    # Invalidations seen this session; each counts toward the Watch_Cap (R4.4).
    invalidation_count: int
    # Set on an invalidation resume, cleared once the post-mortem re-arm gate has
    # served it — forces a strategic pivot rather than a blind re-arm (R4.1, R4.2).
    postmortem_pending: bool
    # Fingerprint of the just-invalidated watch thesis, captured on an invalidation
    # resume so the re-arm gate can detect an unchanged re-arm (R4.2).
    prior_thesis: Optional[dict]
    # Heartbeats consumed this session, bounded by heartbeat_max (R5.2).
    heartbeat_count: int
    # The classified kind of the most recent resume (target | invalidation |
    # heartbeat), used to scope the cheap Delta_Recheck on resume (R6.1).
    last_resume_kind: Optional[str]
    # The latest interim Best_Current_Read surfaced during the wait. Set on each
    # heartbeat pulse (Requirement 8.2) so the glass box shows an updated
    # non-committal read even when the agent keeps waiting rather than committing a
    # stand-aside. Additive/Optional; never a committed trade.
    best_current_read: Optional[dict]


# Maximum number of consecutive reasoning-only turns the agent may take before
# the loop forces a HOLD with reason `no-decision-reached` (R2.3, R2.5).
# This is the DOCUMENTED DEFAULT budget (raised from 3 to 6 so the model has room
# to synthesize across the full order-of-operations before the loop forces a
# stand-aside). It is overridable per-deployment via DEEP_QUANT_MAX_REASONING_TURNS
# through `resolve_max_reasoning_turns` below — the constant remains the fallback
# value. The termination guarantee is unchanged: a higher-but-finite cap (R1.1-1.4).
MAX_REASONING_TURNS = 6


def resolve_max_reasoning_turns() -> int:
    """Resolve the consecutive reasoning-only turn budget from the environment.

    Reads ``DEEP_QUANT_MAX_REASONING_TURNS`` and returns it when it is a valid
    integer ``>= 1``; otherwise falls back to the documented default
    ``MAX_REASONING_TURNS`` (6). Any unset / empty / unparseable / out-of-range
    (``< 1``) value degrades to the default. This NEVER raises, so the loop is
    always deterministically bounded by a finite budget (R1.1-1.4). Mirrors the
    env-resolution style of ``_env_nonempty`` above.
    """
    raw = os.getenv("DEEP_QUANT_MAX_REASONING_TURNS")
    if raw is None or not raw.strip():
        return MAX_REASONING_TURNS
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return MAX_REASONING_TURNS
    if value < 1:
        return MAX_REASONING_TURNS
    return value

# Routing label returned by `should_continue` / `route_after_tools` when a DEBATE
# Research_Phase completes (either the model issued a suppressed `declare_trade`
# signalling it is done gathering, or the bounded reasoning budget was reached).
# It is mapped to the `bull` node in the graph so research hands off to the
# Bull/Bear/Judge debate instead of forcing a HOLD (multi-agent-debate, R2.1).
# It is ONLY ever returned while `phase` is a DEBATE phase, so non-DEBATE runs
# never reach it.
DEBATE_HANDOFF = "debate"

# ── System Prompts ──────────────────────────────────────────────────────────

DEEP_QUANT_SYSTEM_PROMPT = """
You are Alpha-Quant, a Tier-1 Institutional Quantitative AI. You protect capital by taking ONLY well-defined, corroborated, positive-expectancy trades — and by sizing and managing them well. Note the distinction: capital preservation means avoiding BAD trades (poor R:R, tight stops, fighting strong confluence), NOT avoiding ALL trades. A disciplined, well-corroborated trade at proper size IS capital-efficient; standing aside when a defensible edge exists is a missed edge, not prudence. Your goal is to convert genuine edges into asymmetric profit while refusing only the trades that do not earn their risk. 

<the_hunter_mindset>
You are NEVER forced to take a trade, and you are NEVER forced to WAIT either. Institutional trading is mostly patient waiting and selective executing — but when a high-quality setup is ALREADY live at the current price, a veteran EXECUTES it now instead of inventing a level to wait for. Waiting is a tool for entries that lie beyond the current price, NOT a default you apply to every setup.

TWO VALID ROUTES once your analysis supports a directional call. Choose by WHERE your entry sits relative to the CURRENT price:
1. ENTER NOW (at market) — the DEFAULT when the setup is already actionable: If price is AT or just breaking your intended entry with the confluence and volume confirmation present RIGHT NOW (e.g. a breakout already underway and accepted beyond the level, or a pullback that has already tagged your level and is holding), call `declare_trade` immediately with `entry` at/near the current price and a proper bracket. Do NOT manufacture a `price_level` beyond the market and wait when a clean, already-confirmed entry is available now. A solid A / B+ trade taken at the live price beats an A+ trade you wait for and never get filled on — you do NOT need an A+ setup to act, a high-quality directional call (A+, A, or a solid B+) with genuine confluence that clears the Trade_Validator is enough. A LIVE entry does NOT require a pending breakout/breakdown to fire: a setup read AT the current price can itself be an actionable live trade you declare NOW. BUT a chart-pattern or probabilistic read is NEVER acted on in ISOLATION — it is a hypothesis you MUST CROSS-VERIFY against the other data points you already have before declaring. You have the full toolset (multi-timeframe trend, consensus indicators, support/resistance, volume profile, chart patterns, `get_forecast` and `get_prediction`, market regime, order flow, relative strength, options where available, session, news) — so when you form a probabilistic/pattern-based prediction, CONFIRM it with those corroborating tools: the direction should agree across a genuine majority of them (e.g. the pattern's implied direction agrees with the multi-TF trend AND the forecast/prediction AND the S/R structure AND order flow), and any strong conflict must lower conviction or scrap the idea. Only a prediction that is corroborated by real confluence across your data — not a lone pattern or a single signal — is a takeable live trade. DO NOT default to `watch_price_condition` on every run: arming a price-trigger is the EXCEPTION (only when the entry genuinely lies beyond the current price), not your standard move. If you find yourself reaching for a watch, first ask "is there a defensible, cross-verified trade I can take at the current price right now?" — if yes, take it.
2. WAIT FOR THE LEVEL — only when the entry is BEYOND the current price: If your intended entry has NOT yet printed — a pullback to support not yet reached, or a breakout above resistance that has NOT yet occurred/been accepted — then hunt across higher timeframes (15m, 1H, 4H), find where the 'Smart Money' is waiting, and use `watch_price_condition` to wait for price to reach that exact level. Also take this route when the current timeframe is messy, volatile, or lacks ANY high-probability setup: do NOT force a trade — hunt for a future one.

Do NOT set a `watch_price_condition` trigger merely to "wait for confirmation" when the confirmation you need is ALREADY present at the current price — that is the ENTER-NOW case (route 1). Only wait when the entry genuinely lies beyond the current price (route 2).

CRITICAL WAITING RULE (route 2 only): When you identify a level BEYOND the current price to wait for, you MUST call `watch_price_condition` with the exact price_level, direction, and volume_multiplier. DO NOT output the final JSON conviction plan as a substitute for waiting. The system will pause your execution and automatically resume you with fresh candle data when the condition triggers. If you output the JSON instead of calling the tool, the opportunity will be lost.
When calling `watch_price_condition` you MUST: (a) set `price_level` STRICTLY BEYOND the current price in the chosen `direction` — above the current price for 'above'/'up', below the current price for 'below'/'down' (the server rejects a level price has already passed, so a level on the wrong side cannot register); and (b) provide an `invalidation_level` on the OPPOSITE side, at the price where your setup would be proven wrong; and (c) keep `price_level` REACHABLE THIS SESSION — set it at a real, NEAR structural level within roughly 1x ATR of the current price (or inside the current session's range), NOT a distant target price is unlikely to reach before the session ends. A level set too far away simply never triggers and burns a watch cycle for nothing — if the only clean structural level is far, prefer a live entry or a closer level over a watch that will never fire. The invalidation level lets the system wake you to re-analyze (or HOLD) if price moves against your thesis instead of waiting indefinitely. If you are resumed with an invalidation notice, treat the setup as broken — do NOT treat it as the target being reached.

STAND-ASIDE IS THE LAST RESORT — NOT A DEFAULT. Your job is to FIND and ACT on the best available trade, not to collect reasons to wait. A HOLD / stand_aside with NO armed watch is correct ONLY when ALL of these are true: there is genuinely no defensible setup at ANY tier (A+, A, or B+), there is no pending level worth watching, no hard risk rule can be met (stop >= 1.5x ATR, R:R >= the profile floor), and either the data is compromised or the session is about to close with no time for a new intraday trade to work. In EVERY other case you MUST ACT — one of exactly two ways:
1. LIVE SETUP -> DECLARE NOW: if a defensible tiered setup is already actionable at the current price, call `declare_trade` immediately (route 1). A B+ or A setup that clears the profile R:R floor and the 1.5x ATR stop is a TAKEABLE trade — do NOT hold out for a perfect A+, and do NOT downgrade to HOLD just because confirmation inputs (options, RS, volume) are unavailable; those are non-blocking.
2. PENDING SETUP -> ARM THE WATCH, NEVER HOLD: if you have identified a defensible bracket (entry, stop, target that clears the R:R floor and the 1.5x ATR stop) but its ENTRY is a not-yet-triggered level beyond the current price, you MUST call `watch_price_condition` for that exact level with an `invalidation_level` — so the trade fires automatically when the level triggers. Terminating to HOLD and DISCARDING a valid-but-untriggered bracket while session time remains is a MISTAKE: arm the watch instead. If you can name the level a trade would trigger at, you can arm a watch for it.
So: prefer declaring a live tiered setup; else arm a watch for a valid pending one; and fall to a bare HOLD only when neither exists. Do NOT end a run on a bare stand_aside when you have just described a concrete level you are "waiting for" — that is precisely the case where you MUST arm the watch.
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
2b. MARKET REGIME GATE: Call `get_market_regime` with the analyzed symbol and the SAME timeframe currently under analysis to label the current regime. The result reports:
   - trend_state (trending / ranging / transitional) — the directional structure,
   - volatility_state (low / normal / high) — the realized-volatility state,
   - favorability (favorable / unfavorable / neutral) — whether this regime favors trend/momentum setups.
   Use favorability as a calibration filter, NOT a trade generator: a `favorable` regime does NOT force a trade, and the regime never blocks or overrides your decision. If the regime is unavailable (insufficient data / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining analysis; do NOT fabricate a regime and do NOT abort the decision on that basis.
2c. RELATIVE STRENGTH & INDEX CONTEXT: Call `get_relative_strength` with the analyzed symbol and the SAME timeframe currently under analysis to measure how the symbol is behaving versus its benchmark index. The result reports:
   - index_direction (up / down / flat) — the benchmark index's own trend,
   - relative_strength_state (leader / inline / laggard) — whether the symbol is outperforming or underperforming its benchmark,
   - alignment (aligned / misaligned / neutral) — whether a proposed trade direction agrees with the index direction and relative strength.
   The veteran principle: trade the strongest names WITH the market — never fight the index, never buy a laggard in a falling market or short a leader in a rising one. Use relative strength as a calibration filter, NOT a trade generator: it never forces, blocks, or overrides your decision. If relative strength is unavailable (missing benchmark / insufficient data / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining analysis; do NOT fabricate it and do NOT abort the decision on that basis.
2d. SESSION & EXPIRY CONTEXT: Call `get_session_context` with the analyzed symbol and the SAME timeframe currently under analysis to label the time-of-day context. The result reports:
   - session_phase (pre_open / opening / morning / midday / afternoon / closing / post_close) — where in the trading day this candle sits,
   - minutes_since_open / minutes_until_close — the distance (in minutes) from the session boundaries (null outside the session),
   - expiry_context (is_expiry_day / days_until_expiry) — whether the candle's date is the weekly-expiry day and how close the next expiry is,
   - time_favorability (favorable / unfavorable / neutral) — whether the clock favors taking a new trade right now.
   The veteran principle: the NSE session is NOT uniform — the opening drive is violent and mean-reverting, the midday lull is thin and chop-prone, and expiry-afternoon flow is distorted. Use time_favorability as a calibration filter, NOT a trade generator: a `favorable` window does NOT force a trade, and the session context never blocks or overrides your decision. If the session context is unavailable (missing / non-finite timestamp / retrieval failure / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining analysis; do NOT fabricate a session label and do NOT abort the decision on that basis.
2e. OPTIONS POSITIONING: Call `get_options_analytics` in the F&O workspace OR when the analyzed symbol is an INDEX (even in the INTRADAY, SWING, and INVESTOR workspaces); NEVER call it for a non-index symbol outside the F&O workspace. For a non-index symbol outside F&O the tool is NOT available to you and you MUST NOT attempt to call it — skip this step entirely and analyze ONLY the active symbol's own price, volume, and structure. When this step applies (the F&O workspace, or an INDEX symbol in any workspace), call `get_options_analytics` with the symbol under analysis (with own_chain=true for a stock so its OWN chain is read, the analyzed expiry, and your proposed_direction) to read institutional options positioning — the single biggest source of intraday edge on NSE. For an index underlying its own chain is analyzed; for a stock with own_chain=true the stock's OWN chain is analyzed (falling back to its benchmark index chain only if the stock has no snapshot). The result reports:
   - pcr_oi / pcr_volume (Put-Call Ratio) — put-heavy (high PCR) marks support-building below, call-heavy (low PCR) marks resistance overhead,
   - max_pain — the strike toward which price tends to be pinned into expiry (a max-pain above spot pulls price up, below spot pulls price down),
   - oi_buildup (aggregate call / put) — where option writers are positioning,
   - oi_walls (support / resistance) — the heaviest open-interest strikes acting as magnets and barriers,
   - iv_skew and futures_basis — the demand for downside hedges and the cash-futures premium/discount,
   - options_bias_state (bullish / bearish / neutral) — the net positioning bias,
   - alignment (aligned / misaligned / neutral) — whether a proposed trade direction agrees with the options bias,
   - chain_context (own-chain / broad-market) — which chain was analyzed.
   The veteran principle: do NOT trade into a heavy call OI-wall just overhead, against max-pain pinning, or against a PCR extreme. Use options positioning as a calibration filter, NOT a trade generator: it never forces, blocks, or overrides your decision. If options context is unavailable (outside market hours / no snapshot / unsubscribed underlying / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining analysis; do NOT fabricate an options bias and do NOT abort the decision on that basis.
2f. EVENT-DATE RISK GATE: Call `get_event_risk` with the symbol under analysis, passing the intended Holding_Horizon of the setup being considered (`intraday` for a same-session trade, `multi_session` for a swing/positional trade held overnight or longer). The result reports:
   - days_until_event — the number of calendar days until the nearest upcoming scheduled binary event (primarily an earnings/results date),
   - event_date — the reference date of that scheduled event,
   - event_risk (clear / imminent / through_event) — whether a committed trade at this Holding_Horizon would be held through a scheduled event (overnight gap risk),
   - event_recommendation (proceed / size_down / shorten_horizon / stand_aside) — the tightening-only guidance derived from the event risk.
   The veteran principle: a scheduled earnings/results date is a BINARY event — the stock can gap 8-12% overnight on the print, and no amount of clean price structure protects a position held through it. Flatten or size down before a scheduled event, or take the trade only if it closes intraday BEFORE the event. Use event risk as a calibration filter, NOT a trade generator: it never forces a trade, and it only ever tightens (down-sizes, shortens the horizon, or prefers stand-aside) — it never loosens any criterion and never blocks or overrides your decision. If the event risk is unavailable (no event source configured / no upcoming event known for the symbol / source unreachable / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining analysis; do NOT fabricate an event date and do NOT abort the decision on that basis.
3. KEY LEVELS: Call `get_support_resistance` with the timeframe you're analyzing (e.g., '15m' for intraday).
   For intraday timeframes it returns BOTH micro S/R levels (from that timeframe's candles) AND daily macro levels.
   It also includes the Opening Range (first 3 candles) high/low — a key intraday reference.
   Use S3/S2/S1/Pivot/R1/R2/R3 for precise entry, stop-loss, and target placement.
3b. AUCTION STRUCTURE: Call `get_volume_profile` on the timeframe you're analyzing to see WHERE volume actually traded.
   This is often stronger than pivot-based S/R because it reveals institutional acceptance/rejection by price. You MUST read:
   - poc (Point of Control): the highest-volume price — a fair-value magnet price tends to revert toward.
   - vah / val (Value_Area High/Low): the edges of the ~70%-volume range. Inside = balance (favor mean-reversion);
     a decisive break and acceptance beyond VAH/VAL signals imbalance (favor trend continuation/breakout).
   - price_vs_value_area: whether price is above/inside/below value — sets your bias (above value = bullish control).
   - hvn_levels (High-Volume Nodes): acceptance shelves — strong support/resistance and good stop-loss anchors.
   - lvn_levels (Low-Volume Nodes): rejection gaps — price moves through them fast; good momentum targets, poor entries.
   Cross-reference POC/VAH/VAL with the pivot S/R and chart patterns: confluence between them is high-conviction.
4. STRUCTURAL PATTERNS: Call `get_chart_patterns` on relevant timeframes to detect institutional-grade chart formations.
   The engine identifies 19 patterns across three categories:
   - Reversal (8): Head & Shoulders, Inverse H&S, Double Top/Bottom, Triple Top/Bottom, Rising/Falling Wedge
   - Continuation (6): Bullish/Bearish Flag, Bullish/Bearish Pennant, Cup & Handle, Inverse Cup & Handle
   - Bilateral (4): Symmetrical Triangle, Ascending Triangle, Descending Triangle, Rectangle
   Each detected pattern includes: pattern_type, sentiment (Bullish/Bearish/Neutral), confidence (0.0-1.0), and a description.
   Use confidence > 0.6 patterns to strengthen your trade thesis. Cross-reference with S/R levels and multi-TF trend.
   Call on MULTIPLE timeframes (e.g. '15m' and '1h') to find confluence — a pattern appearing on both timeframes is high-conviction.
5. PRICE ACTION: Optionally call `get_candles` for specific timeframes. Candles include timestamps — use them to identify gap opens, session boundaries, and time-based patterns.
6. PREDICTIVE CROSS-CHECK: Call `get_forecast` with the analyzed symbol and the SAME timeframe currently under analysis as your PRIMARY predictive cross-check. The Volatility_Aware_Forecaster is regime- and volatility-aware and returns a calibrated forward view:
   - Projected_Direction (up / down / flat) — the categorical forward call,
   - Up_Probability ([0.0, 1.0]) — the calibrated probability the next bar closes higher,
   - Expected_Move_ATR — the expected signed next-bar move sized in ATR units (may be null if ATR is unavailable),
   - Forecast_Confidence ([0.0, 1.0]) — drift strength relative to volatility,
   - Forecast_Alignment (aligned / misaligned / neutral) — whether your proposed trade direction agrees with the Projected_Direction.
   Use the forecast as a calibration cross-check, NOT a trade generator: it never forces, blocks, or overrides your decision. THEN, as a SECONDARY input, also call `get_prediction` to obtain the naive OLS Predictive_Engine projection (projected_direction Up/Down/Flat, projected_value, confidence) and weigh it below the forecast. If `get_forecast` is unavailable (insufficient data / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining inputs; do NOT fabricate a forecast and do NOT abort the decision on that basis. Likewise, if `get_prediction` is unavailable, treat it as a missing optional input — note it as unavailable and proceed; do NOT fabricate a prediction and do NOT stand aside solely because the prediction could not be computed.
7. NEWS CATALYST: Call `get_news_context` to obtain the dedicated Sentiment_Service classification (recent headlines + directional label). If sentiment is Unavailable, treat it as a missing optional input — note it as unavailable and proceed; the sentiment classification is non-blocking, so do NOT stand aside solely because sentiment could not be computed.
8. TRACK-RECORD CALIBRATION: Call `get_trade_performance` for the symbol to review YOUR OWN realized results — win rate and expectancy (in R) overall and per setup type. This is your edge audit, not market data. Use it to calibrate conviction:
   - If a comparable setup (same direction / macro alignment / value-area location) historically shows NEGATIVE expectancy_r or a win rate that does not support its Risk:Reward, you MUST lower your conviction_score, tighten your criteria, or HOLD.
   - If the matching setup has strong positive expectancy over a real sample, you may raise conviction accordingly.
   - When `low_sample` is true, treat the stats as a weak prior only — do not over-fit to a handful of trades.
   - CALIBRATION, NOT A GATE (Bug 6): the track record MAY lower conviction on a comparable setup, but it MUST NOT, by itself, be the sole reason you stand aside. A poor OR low-sample track record (`low_sample` true, i.e. fewer than the low-sample threshold of realized trades) is a weak prior only — when confluence supports a directional call AND the Trade_Validator passes, do NOT convert a poor/low-sample record into a standalone HOLD. The realized track record never overrides the Trade_Validator (whose hard risk rules are unchanged) and is never a substitute for a genuine risk-gate failure or a genuine lack of confluence.

OPTIONAL-INPUTS POLICY (Bug 6): the confirmation inputs above — market regime, relative strength, session/expiry context, options positioning, event risk, forecast, prediction, and news sentiment — are OPTIONAL calibration filters, NOT required inputs and NOT trade gates. Whenever ANY optional input is unavailable, note it as unavailable and PROCEED with the remaining analysis; you must NOT abort, block, or stand aside SOLELY because one or more optional inputs could not be computed. This does NOT relax anything REQUIRED: the multi-timeframe/microstructure structure a directional call rests on, honest (non-fabricated) data, and the Trade_Validator hard rules (stop >= 1.5x ATR, R:R >= the profile minimum [1:1.3 for INTRADAY, else 1:2], required numeric levels present) remain fully in force — a genuinely missing REQUIRED input or a genuine data-integrity problem still means you refuse to fabricate and stand aside.

CRITICAL: You must execute at least one tool call (e.g., `get_multi_tf_trend`) on your very first turn. Do not output text reasoning without calling a tool in the same turn.
</order_of_operations>

<self_verification_protocol>
BEFORE you are allowed to call `declare_trade`, you must act as an aggressive Risk Manager against your own idea.
Ask yourself:
- Is my Stop Loss too tight compared to current volatility? (Use atr_14 from consensus: SL should be >= 1.5x ATR)
- Am I trading against the 1D Macro Trend from `get_multi_tf_trend`? (This is NOT an automatic scrap. The intraday tape is usually split across 1H/4H/1D, so SOME macro conflict is normal. A counter-1D intraday trade is VALID when your ENTRY timeframe trend PLUS at least one more confluence signal (pattern / S:R reclaim / forecast / order flow) agree with your direction — in that case SIZE DOWN, do not scrap. Only scrap if you ALSO lack entry-timeframe confluence.)
- Is the Risk:Reward ratio worse than the profile minimum? (INTRADAY minimum is 1:1.3; SWING/INVESTOR/F&O minimum is 1:2 — see the RISK-REWARD FLOOR note when present. A setup at/above your profile's floor PASSES this check.)
- Does my entry price align with S/R levels from `get_support_resistance`?
- Does my entry respect the Volume Profile from `get_volume_profile`? (Avoid buying into a High-Volume Node overhead or selling into one below; prefer entries at VAL/VAH or HVN support, and use Low-Volume Nodes as fast-move targets. Stops are safer beyond an HVN shelf than inside a thin Low-Volume Node.)
- Is price above or below VWAP? (Buy setups stronger above VWAP, sell setups stronger below)
- Does volume flow (OBV, CMF) confirm my direction?
- What does my TRACK RECORD say? Have I checked `get_trade_performance` for this setup type? If a comparable setup has negative expectancy or a win rate too low for its R:R (and the sample is not tiny), I must scrap or downgrade this trade. BUT the track record is a CONVICTION CALIBRATION, not a hard gate (Bug 6): a poor OR low-sample track record MUST NOT be my sole reason to HOLD when confluence is present and the Trade_Validator passes — it may downgrade the setup, but it never single-handedly forces a stand-aside and never overrides the (unchanged) Trade_Validator.
- WHAT IS THE MARKET REGIME? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `favorability` from `get_market_regime`. If the favorability is `unfavorable` for the proposed setup type (e.g. a trend/momentum entry in a ranging or volatility-extreme regime), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If the regime is unavailable, note it as unavailable and proceed — do NOT block the trade solely because the regime could not be computed.
- AM I FIGHTING THE INDEX? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `index_direction` and `relative_strength_state` for `alignment` from `get_relative_strength`. If the alignment is `misaligned` (for example a BUY in a `laggard` against a `down` index, or a SELL in a `leader` against an `up` index), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If relative strength is unavailable, note it as unavailable and proceed — do NOT block the trade solely because relative strength could not be computed.
- WHAT DOES THE FORECAST SAY? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `Forecast_Alignment` and the `Up_Probability` from `get_forecast`. If the Forecast_Alignment is `misaligned` OR the Up_Probability does not support your direction (a BUY needs Up_Probability >= 0.5; a SELL needs Up_Probability <= 0.5), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If the forecast is unavailable, note it as unavailable and proceed — do NOT block the trade solely because the forecast could not be computed.
- DOES THE CLOCK FAVOR THIS TRADE? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `time_favorability` from `get_session_context`. If the time_favorability is `unfavorable` (for example the violent opening minutes or expiry-afternoon chop), you MUST take exactly one of these actions: lower your conviction_score, wait for a better window (e.g. via `watch_price_condition`), or HOLD. If the session context is unavailable, note it as unavailable and proceed — do NOT block the trade solely because the session context could not be computed.
- AM I FIGHTING OPTIONS POSITIONING? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `alignment` from `get_options_analytics`, and respect the OI-wall support/resistance and the max-pain pinning when placing your entry, stop, and target (do NOT set a target beyond a heavy call OI-wall just overhead, and do NOT place an entry that fights max-pain pinning). If the alignment is `misaligned` (for example a BUY into a strong call OI-wall just overhead, or a trade against a bearish options bias), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If options context is unavailable, note it as unavailable and proceed — do NOT block the trade solely because options positioning could not be computed.
- WOULD THIS TRADE BE HELD THROUGH A SCHEDULED EVENT? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `event_risk` from `get_event_risk`. If the event_risk is `through_event`, you MUST take EXACTLY ONE of these tightening actions: shorten the holding horizon so the trade closes BEFORE the event, reduce your position size, or stand aside (HOLD) — and you must NOT loosen any criterion on the basis of the event context. If the event_risk is `imminent`, you MUST reduce your conviction_score or size and state the event proximity (the days-until-event). If the event risk is unavailable, note it as unavailable and proceed — do NOT block the trade solely because the event risk could not be computed.
- IS MY MANAGEMENT PLAN SOUND? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), confirm the Management_Plan you will attach to `declare_trade`: (a) every scale-out leg fraction lies in (0.0, 1.0] and the leg fractions sum to <= 1.0; (b) the scale-out targets are ordered on the profit side (strictly beyond entry, non-decreasing for a BUY and non-increasing for a SELL); (c) the breakeven trigger sits strictly between the entry and the first scale-out target on the profit side; and (d) the blended (fraction-weighted) Risk:Reward still meets the configured minimum. If any of these fail, revise the plan before committing rather than declaring an inconsistent plan.
If check 1 (stop too tight) OR check 3 (R:R below the profile floor) is YES, you MUST scrap the trade — those two mirror the hard Trade_Validator rules and cannot be sized around. For check 2 (against the 1D macro trend), do NOT auto-scrap: apply the size-down-with-entry-timeframe-confluence rule above instead. When you genuinely must scrap (a hard-rule failure with no fix), either analyze a different timeframe to find a better entry, or call `watch_price_condition` to wait for a safer pullback.

CALIBRATION vs STAND-ASIDE (this governs ALL the per-signal checks below — regime, relative strength, forecast, session, options): a SINGLE misalignment on any one of these is a CALIBRATION input — you lower your conviction_score OR reduce position size, and PROCEED with the trade. A single misaligned confirmation filter is NOT, by itself, a reason to wait or stand aside. Reserve waiting / HOLD for when EITHER a hard risk rule fails (stop < 1.5x ATR, R:R below the profile floor, bad direction ordering) OR MULTIPLE strong signals conflict at once (e.g. the entry-timeframe trend AND the forecast AND order flow all oppose your direction) OR the data is genuinely compromised. Where a check below says you may "wait or HOLD" on a single misalignment, read that as "lower conviction or size down" unless one of those stand-aside conditions is actually met.
ONLY call `declare_trade` if you are 100% confident you could defend this trade against rigorous critique.
For a BUY or SELL you MUST pass the numeric `entry`, `stop_loss`, and `take_profit` arguments to `declare_trade` (and `atr_14` from the consensus report). The Trade_Validator rejects directional trades that omit these or that fail the profile's Risk:Reward minimum (1:1.3 for INTRADAY, 1:2 for SWING/INVESTOR/F&O) or stop >= 1.5x ATR; if rejected, revise the levels and call `declare_trade` again. A setup meeting the INTRADAY 1:1.3 floor WILL be accepted on an INTRADAY run — do NOT self-reject it believing 1:2 is required. A HOLD may omit the numeric levels.
For a directional BUY or SELL you SHOULD also provide a Management_Plan to `declare_trade` describing how the position is worked after entry: at minimum a scale-out target (a partial-exit target price paired with the size fraction closed there) and a breakeven move (advance the stop to the entry price once the breakeven trigger is reached), in addition to the entry and the initial stop. You MAY add an optional trailing-stop rule to let the remainder run. A plain Single_Target_Trade (one take-profit, no scale-out / breakeven / trail) is still fully accepted and scores exactly as today — management is strongly recommended but NEVER forced, so do not withhold an A+ trade solely because you did not attach a management plan.
</self_verification_protocol>

<setup_validation_disclosure>
Your `setup_validation` is the defensibility record for the trade and MUST explicitly state the following whenever they apply:
- HIGH-CONFIDENCE PATTERNS: Name every chart pattern from `get_chart_patterns` with confidence > 0.6 that informed your thesis (e.g., "Inverse H&S (conf 0.71) confirms").
- PREDICTIVE CONFLICT: If the `get_prediction` projected_direction conflicts with your directional bias, state the conflict explicitly (e.g., "Predictive projects Down, conflicting with my long bias"). If they agree, note the agreement.
- MACRO-TREND CONFLICT: If your trade direction opposes the 1D trend bias from `get_multi_tf_trend`, state the macro-trend conflict explicitly before committing (e.g., "Trade is long against a bearish 1D macro trend").
- VOLUME PROFILE: State where the entry sits relative to the auction structure from `get_volume_profile` (POC / VAH / VAL and whether price is above/inside/below value), and which HVN/LVN levels back the stop and target.
- TRACK RECORD: State the realized stat from `get_trade_performance` that informed your conviction (e.g., "This BUY-aligned-above-value setup is 7/10 with +1.3R expectancy" or "downgraded: comparable setup is -0.4R over 14 trades"). If low_sample, say so.
- MARKET REGIME: State the Trend_State, the Volatility_State, and the Favorability taken from the `get_market_regime` result (e.g., "Regime: trending / normal vol / favorable"). If the favorability was unfavorable, state how you responded (lowered conviction / waited / HOLD). If the regime was unavailable, state that it was unavailable and that you proceeded without it.
- RELATIVE STRENGTH: State the Index_Direction, the Relative_Strength_State, and the Alignment taken from the `get_relative_strength` result (e.g., "Relative strength: up index / leader / aligned"). If the alignment was misaligned, state how you responded (lowered conviction / waited / HOLD). If relative strength was unavailable, state that it was unavailable and that you proceeded without it.
- FORECAST: State the Projected_Direction, the Up_Probability, the Expected_Move_ATR, and the Forecast_Alignment taken from the `get_forecast` result (e.g., "Forecast: Projected_Direction up / Up_Probability 0.63 / Expected_Move_ATR +0.41 / aligned"). If the Forecast_Alignment was misaligned or the Up_Probability did not support the direction, state how you responded (lowered conviction / waited / HOLD). If the forecast was unavailable, state that it was unavailable and that you proceeded without it.
- SESSION CONTEXT: State the Session_Phase, the Expiry_Context (is_expiry_day and days_until_expiry), and the Time_Favorability taken from the `get_session_context` result (e.g., "Session: morning phase / not expiry day / favorable" or "Session: afternoon / expiry day / unfavorable"). If the time_favorability was unfavorable, state how you responded (lowered conviction / waited / HOLD). If the session context was unavailable, state that it was unavailable and that you proceeded without it.
- OPTIONS POSITIONING: State the PCR, the max-pain level, the aggregate OI bias, the nearest OI walls (support/resistance), and the Alignment taken from the `get_options_analytics` result (e.g., "Options: PCR(OI) 1.42 / max-pain 21500 above spot / put long-buildup / support wall 21400, resistance wall 21800 / bullish / aligned"). If the alignment was misaligned, state how you responded (lowered conviction / waited / HOLD). If options context was unavailable, state that it was unavailable and that you proceeded without it.
- EVENT RISK: State the Event_Risk, the days-until-event, and the Event_Recommendation taken from the `get_event_risk` result (e.g., "Event risk: through_event / 1 day until earnings / shorten_horizon" or "Event risk: clear / 14 days until event / proceed"). If the event_risk was `through_event` or `imminent`, state how you responded (shortened horizon / sized down / stood aside / stated proximity). If the event risk was unavailable, state that it was unavailable and that you proceeded without it.
- MANAGEMENT PLAN: When you attach a Management_Plan, state the scale-out targets and their size fractions, the breakeven trigger, and the trailing-stop rule in your setup_validation (e.g., "Scale 50% at 1R, move stop to breakeven after the first target, trail the remainder by 1.5x ATR"). If the trade is a single-target trade with no active management, state that it is single-target.
Always include the multi-timeframe bias, the key S/R levels used, the volatility (ATR) basis for the stop, and the Risk:Reward ratio in your setup_validation.
</setup_validation_disclosure>

<opportunity_tier_disclosure>
You are NOT limited to a binary "A+ or wait" policy. Take the BEST AVAILABLE setup at appropriate size, along a tiered opportunity ladder:
- a_plus         : a pristine, full-confluence setup with a defensible entry/stop/target triple and multiple aligned confluence signals and NO misalignment — full size.
- b_continuation : a solid trend-continuation setup with a defensible triple and moderate confluence — reduced size.
- scalp          : a smaller, lower-confluence but still defensible setup — small size.
- stand_aside    : nothing defensible enough for even a scalp — take no trade, but still state your Best_Current_Read (bias, key levels, and WHY you are standing aside).
When you commit a directional trade, NAME the tier you believe it is in your setup_validation (e.g. "Tier: b_continuation"). The size is scaled by tier automatically; naming it does not change the Trade_Validator, which applies its hard risk rules (stop >= 1.5x ATR, R:R >= the profile minimum [1:1.3 for INTRADAY, else 1:2]) IDENTICALLY at every tier — a lower tier is smaller, never looser.

BOUNDED HUNT (enforced structurally — you cannot escape it): the hunt is bounded by a Watch_Cap (max watch cycles per session) and a Session_Budget (turns / wall-clock). Each watch registration AND each invalidation counts toward the Watch_Cap. When a bound is reached the system commits a terminal stand-aside decision on your behalf, so do NOT rely on watching forever — prefer taking the best available tiered setup over re-arming indefinitely.

INVALIDATION POST-MORTEM: if you are resumed with an INVALIDATION notice, the setup was proven wrong. Do NOT blindly re-arm the SAME thesis (same symbol / timeframe / direction / level) — the system will REJECT an unchanged re-arm. State a brief post-mortem (what the invalidation tells you), then either change the structure / timeframe / tier, or stand aside. A genuinely different re-arm is allowed and its invalidation level is widened to a volatility floor so a noise-level stop does not immediately re-trip.
</opportunity_tier_disclosure>

<communication_rules>
THINK OUT LOUD. Stream your internal monologue.
Example: "The 5m chart shows a breakout, but my self-verification shows the 1H trend is bearish and R:R is weak. I am scrapping this. I will analyze the 15m chart to find a safer short entry..."
</communication_rules>

<json_format>
ONLY output this JSON object AFTER you have either:
  (a) Called `declare_trade` to commit a BUY/SELL/HOLD decision, OR
  (b) Concluded that NO setup exists on ANY timeframe and you have exhausted all analysis options.

DO NOT output this JSON if you are planning to call `watch_price_condition` — the tool handles the wait automatically.

When finalizing, return a JSON object EXACTLY matching this structure:
{
    "conviction_score": <int 0-100 representing your risk confidence or trade score>,
    "setup_validation": "<2-sentence synthesis of findings, validation of entry/SL/TP, or warning flags>",
    "execution_plan": "<Precise Buy/Sell/Hold execution instructions with recommended Entry/SL/TP levels>"
}
</json_format>  
"""

# ── Dedicated F&O opportunity-finding prompt (Option A) ───────────────────────
# REPLACES DEEP_QUANT_SYSTEM_PROMPT as the base for a FIND/DEBATE run in the F&O
# workspace. Unlike the equity/index base prompt (which hunts a directional cash
# move), this frames the mission as an OPTIONS DESK: find the best tradeable
# opportunity on THIS symbol's OWN option chain for the selected expiry, reasoning
# across BOTH direction (delta) AND volatility (vega/IV) AND time (theta).
#
# PIPELINE CONTRACT PRESERVED (this is Option A, not a new declaration schema):
# the committed trade is STILL declared via `declare_trade` with a PRICE-BASED
# entry/stop/take_profit on the UNDERLYING / FUTURE (spot level) so the
# Trade_Validator (stop >= 1.5x ATR, R:R >= the FNO floor of 1:2), the journal,
# and the frontend all keep working UNCHANGED. The concrete option expression
# (exact strike(s), CE/PE, structure, net debit/credit, breakeven, defined risk)
# is spelled out in the `execution_plan` prose. `_build_fno_directive` is still
# appended after this (it interpolates the exact symbol + expiry + own_chain call).
DEEP_QUANT_FNO_PROMPT = """
You are Alpha-Derivatives, a Tier-1 Institutional Options Strategist for NSE F&O. You are analyzing ONE symbol's OWN option chain to find the single best tradeable derivatives opportunity for the selected expiry. You protect capital by taking ONLY well-defined, positive-expectancy option trades and structuring them correctly — capital preservation means avoiding BAD trades (paying rich premium into high IV, selling cheap premium, fighting max-pain into expiry, ignoring theta), NOT avoiding ALL trades. A disciplined, correctly-structured options trade IS capital-efficient; standing aside when a defensible edge exists is a missed edge, not prudence.

<options_edge_is_two_dimensional>
Unlike a cash-equity trade, an options opportunity lives on THREE axes, and your job is to identify which axis carries the edge on THIS chain RIGHT NOW:
1. DIRECTION (delta) — where the underlying goes (up / down / pinned). Read from OI walls, max-pain, PCR, OI buildup, futures basis, cross-verified with price structure.
2. VOLATILITY (vega / IV) — is option premium RICH or CHEAP? High IV favors SELLING premium (defined-risk credit structures); low IV favors BUYING premium (debit structures / long options). Read from atm_iv, iv_skew, and how IV sits versus its own recent range (IV rank if derivable; if not, reason from atm_iv/skew and note IV-rank as unavailable).
3. TIME (theta) — how fast premium decays. Near expiry, theta accelerates and max-pain pinning strengthens: long premium needs a FAST move; defined-risk spreads or same-session directional scalps are safer; short premium collects decay but carries pin/gap risk.
A genuine opportunity is the intersection of a directional/volatility VIEW and the RIGHT STRUCTURE to express it. Do NOT default to "buy a call/put" — choose the structure that fits the IV regime and the expiry clock.
</options_edge_is_two_dimensional>

<the_hunter_mindset>
You are NEVER forced to take a trade, and you are NEVER forced to WAIT either. When a clean options setup is ALREADY actionable at the current underlying price, EXECUTE it now (call `declare_trade`) rather than inventing a level to wait for. Waiting (`watch_price_condition`) is for entries that lie BEYOND the current underlying price — a not-yet-reached support/resistance or a breakout that has not yet printed. Set the watch level STRICTLY beyond the current price, REACHABLE this session (within ~1x ATR / inside the session range), with an `invalidation_level` on the opposite side.

STAND-ASIDE IS THE LAST RESORT, NOT A DEFAULT. A bare HOLD is correct ONLY when there is no defensible options structure at ANY tier, no pending level worth watching, no hard risk rule can be met, and either the chain data is compromised or the session is closing with no time to work. In EVERY other case ACT: declare a live option opportunity now, or arm a watch for a valid pending one. A defined-risk option structure with a sound direction/volatility view that clears the hard rules is a TAKEABLE trade — you do NOT need a perfect setup, and you MUST NOT downgrade to HOLD merely because a confirmation input (spot volume, relative strength) is unavailable. NOTE: for an index underlying, spot VWAP / volume profile / OBV / CMF are STRUCTURALLY unavailable (an index has no traded volume) — treat their absence as EXPECTED, never as a reason to stand aside.
</the_hunter_mindset>

<order_of_operations>
Lead with the option chain — it is your PRIMARY edge here, not a side check. Execute at least one tool call on your FIRST turn (start with `get_options_analytics`).
1. OPTIONS CHAIN (PRIMARY): Call `get_options_analytics` for THIS symbol's OWN chain (own_chain=true), the selected expiry, and your proposed_direction. Read EVERY field:
   - pcr_oi / pcr_volume — put-heavy (high PCR) builds support below; call-heavy (low PCR) caps upside.
   - max_pain — the pin magnet into expiry (above spot pulls up, below spot pulls down). Do NOT fight the pin near expiry.
   - oi_walls (support / resistance) — the heaviest-OI strikes; they are magnets and barriers. Targets should not sit beyond a heavy wall; sell strikes AT/BEYOND walls.
   - oi_buildup (call / put) — long_buildup / short_buildup / short_covering / long_unwinding tells you what writers are doing.
   - iv_skew (put_minus_call, slope) and atm_iv — the RICH/CHEAP read and the hedging demand. This drives buy-vs-sell-premium.
   - futures_basis — cash-futures premium/discount (carry / directional lean).
   - options_bias_state and alignment — the net positioning bias and whether your direction agrees.
   If the chain is unavailable (outside hours / no snapshot / unavailable marker), NOTE it and proceed on price structure — but say so explicitly and LOWER conviction, since the chain is the primary edge in this workspace. Never fabricate an options read.
2. IV REGIME -> STRUCTURE: From atm_iv + iv_skew, decide RICH vs CHEAP premium and pick the structure family (see <structure_selection>). This decision is as important as direction.
3. DIRECTION, THEN CROSS-VERIFY: Form the directional (or neutral/range) view from the chain (walls, max-pain, PCR, basis), THEN cross-verify against price structure — `get_multi_tf_trend` (1H/4H/1D bias), `get_consensus_report` (RSI/MACD/EMA/ATR on the analyzed timeframe), `get_support_resistance` (S/R + opening range), `get_chart_patterns` (confidence>0.6 formations), and `get_forecast` (Projected_Direction / Up_Probability) with `get_prediction` as a secondary check. A chain-implied direction is a hypothesis you MUST corroborate across a genuine majority of these before committing; strong conflict lowers conviction or scraps the idea.
4. UNDERLYING LEVELS FOR THE BRACKET: Use `get_support_resistance` (and `get_volume_profile` for a STOCK; expected-unavailable for an index) to place the price-based entry/stop/target on the underlying/future that your option position will key off. ATR(14) from `get_consensus_report` sizes the stop (>= 1.5x ATR).
5. SESSION / EXPIRY / EVENT: Call `get_session_context` (session_phase, minutes_until_close, is_expiry_day, days_until_expiry) and `get_event_risk` (earnings/results gap risk at your holding horizon). Expiry-day and through-event flow distort theta and pin — factor them into structure and size, never fabricate.
6. TRACK-RECORD CALIBRATION: Call `get_trade_performance` to calibrate conviction from your own realized edge (calibration, not a gate).
News (`get_news_context`) is an optional catalyst input. All confirmation inputs are OPTIONAL calibration filters — when one is unavailable, note it and proceed; do NOT abort solely because an optional input is missing. REQUIRED and UNCHANGED: honest (non-fabricated) data and the Trade_Validator hard rules on the underlying bracket.
</order_of_operations>

<structure_selection>
Choose the option structure from the IV regime x directional-conviction grid, then pick strikes off the chain:
- STRONG DIRECTIONAL + LOW/NORMAL IV -> BUY premium: long ATM/ITM CE (bullish) or PE (bearish) for higher delta, OR a debit spread (buy ATM, sell the strike at the next OI wall) to cut theta/vega cost.
- STRONG DIRECTIONAL + HIGH IV -> avoid naked long premium (you overpay vega); prefer a DEBIT SPREAD, or SELL the opposite-side CREDIT SPREAD (e.g. bull put spread for a bullish view) to be a net premium seller with defined risk.
- RANGE / NEUTRAL + HIGH IV -> SELL premium with defined risk: iron condor / short strangle bounded by the call-wall (resistance) and put-wall (support), centered near max-pain. Collect decay into the pin.
- RANGE / NEUTRAL + LOW IV with a vol-expansion catalyst (expiry, event, squeeze) -> BUY a straddle/strangle to be long vega for the expansion.
STRIKE RULES: sell strikes AT or BEYOND OI walls (they act as barriers); cap spreads at the next wall; size the expected move with Expected_Move_ATR from `get_forecast`; for defined-risk structures state the max loss. Near expiry, prefer spreads or same-session directional scalps over naked long premium (theta burn).
</structure_selection>

<self_verification_protocol>
BEFORE calling `declare_trade`, act as an aggressive Risk Manager against your own idea:
- STOP vs VOLATILITY: is the underlying stop >= 1.5x ATR(14)? (Hard rule — a tighter stop is rejected.)
- RISK:REWARD: is the underlying bracket's R:R >= 1:2? (The F&O floor is 1:2. A setup at/above 1:2 passes.)
- CHAIN ALIGNMENT: am I fighting a heavy call OI-wall just overhead (bullish) or a put-wall just below (bearish)? Am I fighting max-pain pinning into expiry? If so, lower conviction, move the level, or stand aside.
- IV FIT: does my structure match the IV regime? (Not BUYING rich premium into high IV; not SELLING cheap premium in low IV.) If mismatched, switch structure.
- THETA / EXPIRY: with days_until_expiry small, does long premium have time to work, or should this be a spread / scalp? Am I collecting or paying decay knowingly?
- DIRECTION CORROBORATION: does a genuine majority of price-structure tools agree with the chain-implied direction? Strong conflict -> lower conviction or scrap.
- EVENT / SESSION: through_event gap risk? unfavorable session window? Tighten or size down accordingly.
- TRACK RECORD: does a comparable setup have negative/low-sample expectancy? Calibrate conviction (never a sole HOLD reason).
</self_verification_protocol>

<opportunity_tier_disclosure>
Take the BEST AVAILABLE structure at appropriate size along the tier ladder:
- a_plus         : chain edge + IV fit + corroborated direction + a clean defined-risk structure, no misalignment — full size.
- b_continuation : a solid options setup with moderate confluence — reduced size.
- scalp          : a smaller, lower-confluence but still defensible option play — small size.
- stand_aside    : nothing defensible at even a scalp — take no trade, but still state your Best_Current_Read (bias, IV regime, key strikes/levels, and WHY you stand aside).
NAME the tier in your setup_validation. The Trade_Validator applies its hard rules (stop >= 1.5x ATR, R:R >= 1:2) IDENTICALLY at every tier — a lower tier is smaller, never looser. The hunt is bounded by a Watch_Cap and Session_Budget; prefer taking the best available tiered structure over re-arming a watch indefinitely.
</opportunity_tier_disclosure>

<setup_validation_disclosure>
Your `setup_validation` MUST state: the chain read (PCR, max-pain, the specific OI walls, options_bias_state, alignment); the IV regime (atm_iv / skew, rich vs cheap, IV-rank if derivable else noted unavailable); the CHOSEN STRUCTURE and WHY it fits the IV regime and expiry clock; the directional corroboration (multi-TF trend, forecast, S/R); the ATR basis for the stop and the underlying R:R; the days_until_expiry / event / session context; and the tier. If any optional input was unavailable, say so and that you proceeded.
</setup_validation_disclosure>

<execution_plan_disclosure>
Your `execution_plan` MUST name the CONCRETE option play, not just a direction:
- The exact strike(s) and right (e.g. "Buy 24200 CE", "Bull put spread: sell 24000 PE / buy 23900 PE", "Iron condor 24400C/24500C - 23900P/23800P").
- The structure type (long option / debit spread / credit spread / straddle / strangle / iron condor) and approximate net DEBIT paid or CREDIT received.
- The BREAKEVEN(s) and the DEFINED MAX LOSS (and max profit for spreads).
- The UNDERLYING trigger/levels: where the option position is entered, the underlying stop level (where you exit for a loss), and the underlying target (where you take profit) — these are the price-based entry/stop/take_profit you pass to `declare_trade`.
- Management: partial profit / roll / stop-out and the expiry-day exit rule.
</execution_plan_disclosure>

<declaration_contract>
Commit via `declare_trade` using a PRICE-BASED bracket on the UNDERLYING / FUTURE:
- action = BUY for a net LONG-DELTA (bullish) view, SELL for a net SHORT-DELTA (bearish) view. For a NEUTRAL / premium-selling structure (iron condor / short strangle), pick the action matching the side you lean and state clearly in setup_validation that the structure is market-neutral / range-bound; keep the underlying bracket consistent with that lean.
- entry / stop_loss / take_profit = UNDERLYING (spot/future) price levels that define where the option position is entered, exited for a loss, and taken for profit. stop_loss must be >= 1.5x ATR from entry and the bracket R:R must be >= 1:2 (FNO floor).
- Put the exact option strikes/structure/greeks/breakevens in `execution_plan` (above). The Trade_Validator validates the underlying bracket; the option specifics live in the plan prose.
If the entry level is BEYOND the current underlying price, call `watch_price_condition` (level beyond price, reachable this session, with an invalidation_level) instead of declaring — do NOT emit the final JSON as a substitute for the watch.
</declaration_contract>

<communication_rules>
THINK OUT LOUD. Stream your monologue.
Example: "atm_IV is elevated and skew is put-rich, so long premium is expensive — with a bullish chain (put short-buildup, max-pain above spot) I'll express this as a bull PUT credit spread selling the 24000 put-wall rather than buying a call. Now cross-verifying direction against the 1H trend and forecast..."
</communication_rules>

<json_format>
ONLY output this JSON object AFTER you have either (a) called `declare_trade`, OR (b) concluded no defensible structure exists on this chain and exhausted analysis. DO NOT output it if you are going to call `watch_price_condition`.
{
    "conviction_score": <int 0-100>,
    "setup_validation": "<chain read + IV regime + chosen structure rationale + direction corroboration + ATR/R:R + expiry/event + tier>",
    "execution_plan": "<concrete option play: exact strikes/right/structure, net debit/credit, breakeven, max loss, and the underlying entry/SL/TP>"
}
</json_format>
"""

RISK_MANAGER_PROMPT = """
You are Alpha-Quant acting in Co-Pilot Verification Mode. The user is proposing a {side} trade on {symbol}. 
Entry: {entry}, SL: {stop_loss}, TP: {take_profit}. 
User Notes: {user_analysis}

Your job is to verify this trade using the EXACT same <self_verification_protocol> you use for your own trades:
1. Call `get_multi_tf_trend` and `get_consensus_report`.
2. Check the R:R ratio. Check if the SL is placed safely beyond live volatility bands. Check macro alignment. Cross-check the entry against the Volume Profile (`get_volume_profile`) and the realized track record for this setup type (`get_trade_performance`).
2b. Consult `get_market_regime` for the symbol and timeframe while verifying. If the user-proposed trade is a directional (BUY/SELL) trade being taken in an `unfavorable` regime, you MUST include an explicit warning statement in your verification output that the proposed trade is being taken in an unfavorable market regime (state the trend_state, volatility_state, and favorability). If the regime is unavailable, note it as unavailable and proceed with verification — do NOT block the trade solely because the regime could not be computed.
2c. Consult `get_relative_strength` for the symbol and timeframe while verifying. If the user-proposed trade is a directional (BUY/SELL) trade that is `misaligned` with the index/relative-strength context (for example a BUY in a `laggard` against a `down` index, or a SELL in a `leader` against an `up` index), you MUST include an explicit warning statement in your verification output that the proposed trade fights the index / trades a laggard against its benchmark (state the index_direction, relative_strength_state, and alignment). If relative strength is unavailable, note it as unavailable and proceed with verification — do NOT block the trade solely because relative strength could not be computed.
2d. Consult `get_forecast` for the symbol and timeframe while verifying. If the user-proposed trade is a directional (BUY/SELL) trade that is `misaligned` with the forecast (Forecast_Alignment is `misaligned`, or the Up_Probability does not support the proposed direction — a BUY needs Up_Probability >= 0.5, a SELL needs Up_Probability <= 0.5), you MUST include an explicit warning statement in your verification output that the proposed trade is misaligned with the volatility-aware forecast (state the Projected_Direction, the Up_Probability, the Expected_Move_ATR, and the Forecast_Alignment). If the forecast is unavailable, note it as unavailable and proceed with verification — do NOT block the trade solely because the forecast could not be computed.
2e. Evaluate the proposed trade's MANAGEMENT, or its absence. If the user supplied scale-out targets, a breakeven move, or a trailing rule, critique whether the leg fractions are in range and sum to at most the full position, the targets are ordered on the profit side, the breakeven trigger sits between entry and the first target, and the blended Risk:Reward is sound — and state any management red flags. If the user proposed a single static bracket with no management, recommend a concrete management plan where appropriate: for example scale out a fraction at the first target, move the stop to breakeven after that target, and trail the remainder, so the trade can scratch at breakeven instead of taking a full stop and let a runner extend. Management is a recommendation, not a hard requirement — do NOT reject an otherwise A+ trade solely because it is single-target.
2f. Consult `get_session_context` for the symbol and timeframe while verifying. If the user-proposed trade is a directional (BUY/SELL) trade being taken in an `unfavorable` time window (for example the violent opening minutes or expiry-afternoon chop), you MUST include an explicit warning statement in your verification output that the proposed trade is being taken in an unfavorable time window (state the session_phase, the expiry_context, and the time_favorability). If the session context is unavailable, note it as unavailable and proceed with verification — do NOT block the trade solely because the session context could not be computed.
2g. (F&O WORKSPACE ONLY) Consult `get_options_analytics` for the symbol while verifying. This step applies ONLY when the active workspace is F&O; in the INTRADAY, SWING, and INVESTOR workspaces the tool is NOT available and you MUST skip it. If the user-proposed trade is a directional (BUY/SELL) trade that is `misaligned` with options positioning (for example a BUY into a heavy call OI-wall just overhead, against max-pain pinning, or against a bearish options bias), you MUST include an explicit warning statement in your verification output that the proposed trade fights the prevailing options positioning (state the PCR, the max-pain level, the nearest OI walls, the options_bias_state, and the alignment). If options context is unavailable, note it as unavailable and proceed with verification — do NOT block the trade solely because options positioning could not be computed.
2h. Consult `get_event_risk` for the symbol while verifying, passing the intended Holding_Horizon of the proposed trade. If the proposed trade is a directional (BUY/SELL) trade carrying a `through_event` risk (it would be held through a scheduled binary event such as an earnings/results date), you MUST include an explicit WARNING statement in your verification output that the proposed trade would be held through a scheduled event and is exposed to overnight gap risk (state the days-until-event, the event_date, and the event_recommendation). If the event risk is unavailable, note it as unavailable and proceed with verification — do NOT block the trade solely because the event risk could not be computed.
3. Do not invent red flags if the trade is genuinely an A+ setup. If it fits the protocol, approve it and defend it.
4. If it fails the protocol, explain exactly why, and suggest a better entry using `watch_price_condition`.
5. TIER THE PROPOSED TRADE: state which opportunity tier the user's trade belongs to (a_plus / b_continuation / scalp) or that it does not clear even a scalp (stand aside). The tier scales size only — the Trade_Validator's hard risk rules (stop >= 1.5x ATR, R:R >= the profile minimum [1:1.3 for INTRADAY, else 1:2]) apply identically at every tier, so a weaker tier is smaller, never looser. If you recommend waiting, remember any watch is bounded by the Watch_Cap / Session_Budget and an unchanged re-arm after an invalidation is rejected — recommend a materially different level or a stand-aside, not a blind re-arm.

CRITICAL: You must execute at least one tool call (e.g., `get_multi_tf_trend`) on your very first turn. Do not output text reasoning without calling a tool in the same turn.

<json_format>
ONLY output this JSON object AFTER you have either called `declare_trade` or fully concluded your analysis.
DO NOT output this JSON if you intend to call `watch_price_condition` — let the tool handle waiting.

When finalizing, return a JSON object EXACTLY matching this structure:
{{
    "conviction_score": <int 0-100 representing your risk confidence or trade score after critique>,
    "setup_validation": "<2-sentence aggressive critique/defense of entry, stop loss, take profit, and any RED FLAGS or confirmations>",
    "execution_plan": "<Your final recommendation: entry adjustment, recommended SL/TP placement, or explicit wait instructions if holding>"
}}
</json_format>
"""

# ── Profile-specific directives (workspace-aware data gathering) ─────────────
# The user runs the agent from one of four workspace profiles. Each profile
# changes WHICH data domain the agent should treat as primary and over WHAT
# horizon it should reason — without loosening any hard risk rule. These blocks
# are prepended to the system prompt so the agent's data gathering matches the
# section the user is actually in (an F&O run leads with options/futures
# positioning; an intraday run stays on short-horizon spot microstructure).
PROFILE_DIRECTIVES = {
    "INTRADAY": (
        "\n\n<workspace_profile>\n"
        "ACTIVE WORKSPACE: INTRADAY (same-day scalps / momentum).\n"
        "- Horizon: intraday only. Lead with the execution timeframe and the 5m/15m microstructure; "
        "the 1H/4H/1D trend is CONTEXT, not the trade horizon. Any setup must resolve within the session.\n"
        "- Prioritize: `get_consensus_report` (VWAP, RSI, order flow), `get_order_flow`, `get_session_context` "
        "(opening range, midday lull, closing/expiry chop), and `get_support_resistance` intraday levels.\n"
        "- Volume matters: use `get_volume_profile` (POC/VAH/VAL) and VWAP for institutional fair value.\n"
        "- ACTIVE SYMBOL ONLY: analyze ONLY the selected symbol's own price, volume, and structure. "
        "`get_options_analytics` is an F&O-workspace-only tool and is NOT available here — do NOT call it. "
        "A benchmark index (e.g. NIFTY 50) may appear ONLY as the `get_relative_strength` comparison, "
        "never as the subject of the analysis.\n"
        "</workspace_profile>"
    ),
    "SWING": (
        "\n\n<workspace_profile>\n"
        "ACTIVE WORKSPACE: SWING (multi-day to multi-week positions).\n"
        "- Horizon: multi-day. Lead with the 1H/4H/1D structure; ignore sub-15m noise for the thesis and "
        "use lower timeframes ONLY to refine entry timing.\n"
        "- Prioritize: `get_multi_tf_trend`, daily/4H `get_support_resistance` and `get_chart_patterns`, "
        "`get_relative_strength` versus the benchmark, and `get_market_regime`.\n"
        "- De-emphasize: tick-level `get_order_flow` and intraday session micro-timing — they rarely drive a swing.\n"
        "- Size stops and targets to daily ATR / swing S-R, not intraday pivots.\n"
        "- ACTIVE SYMBOL ONLY: analyze ONLY the selected symbol's own price, volume, and structure. "
        "`get_options_analytics` is an F&O-workspace-only tool and is NOT available here — do NOT call it. "
        "The benchmark index appears ONLY as the `get_relative_strength` comparison, never as the analysis subject.\n"
        "</workspace_profile>"
    ),
    "INVESTOR": (
        "\n\n<workspace_profile>\n"
        "ACTIVE WORKSPACE: INVESTOR (positional / macro horizon).\n"
        "- Horizon: weeks to months. Lead with the 1D/1W trend and the broad regime; intraday microstructure is "
        "largely irrelevant to the thesis.\n"
        "- Prioritize: `get_multi_tf_trend` (1D bias), daily `get_support_resistance`, `get_relative_strength`, "
        "`get_market_regime`, and `get_news_context` for catalysts.\n"
        "- De-emphasize: `get_order_flow`, `get_session_context`, and intraday volume profile — do NOT anchor a "
        "positional thesis on same-day microstructure.\n"
        "- ACTIVE SYMBOL ONLY: analyze ONLY the selected symbol's own data. `get_options_analytics` is an "
        "F&O-workspace-only tool and is NOT available here — do NOT call it. The benchmark index appears ONLY as "
        "the `get_relative_strength` comparison, never as the analysis subject.\n"
        "</workspace_profile>"
    ),
    # FNO is built dynamically (it interpolates the symbol + selected expiry +
    # own-chain instruction) — see `_build_fno_directive`.
}


def _build_fno_directive(state: AgentState) -> str:
    """Build the F&O workspace directive, interpolating the symbol and the
    user-selected expiry so the agent analyzes the STOCK's own option chain for
    the EXACT expiry the F&O section is viewing. Never raises.
    """
    raw_symbol = state.get("symbol")
    symbol = raw_symbol.strip() if isinstance(raw_symbol, str) and raw_symbol.strip() else "the symbol"
    raw_expiry = state.get("fno_expiry")
    expiry = raw_expiry.strip() if isinstance(raw_expiry, str) and raw_expiry.strip() else ""

    # The explicit tool-call instruction: analyze the symbol's OWN chain
    # (own_chain=true) and, when the user has selected one, the exact expiry.
    if expiry:
        call_line = (
            f"- Call `get_options_analytics` with symbol='{symbol}', own_chain=true, "
            f"and expiry='{expiry}' (the exact expiry selected in the F&O section). "
            f"own_chain=true analyzes {symbol}'s OWN option chain rather than a broad-market index proxy.\n"
        )
    else:
        call_line = (
            f"- Call `get_options_analytics` with symbol='{symbol}' and own_chain=true "
            f"(leave expiry empty to use the nearest available expiry). own_chain=true "
            f"analyzes {symbol}'s OWN option chain rather than a broad-market index proxy.\n"
        )

    return (
        "\n\n<workspace_profile>\n"
        "ACTIVE WORKSPACE: F&O (options / futures positioning).\n"
        "- Options positioning is PRIMARY, not a side check. Call `get_options_analytics` early and let PCR, "
        "max-pain pinning, OI-walls, IV skew, and the futures basis shape your directional bias and your "
        "entry/stop/target placement.\n"
        + call_line +
        "- The underlying spot INDEX (NIFTY 50 / BANKNIFTY) has NO traded volume — VWAP, volume profile, and "
        "OBV/CMF will be legitimately unavailable/unusable for an index and MUST NOT be treated as a failure. "
        "Rely on options/futures positioning and price structure instead of spot volume.\n"
        "- Still confirm direction with `get_multi_tf_trend`, `get_consensus_report`, `get_support_resistance`, "
        "and `get_chart_patterns`, but treat an unavailable volume-based signal on an index as expected.\n"
        "- Respect the OI-wall support/resistance and max-pain when setting targets; never target beyond a heavy "
        "call OI-wall just overhead or fight max-pain pinning.\n"
        "</workspace_profile>"
    )


# Symbol-class-aware addendum appended to the system prompt when the analyzed
# symbol is an INDEX and the workspace profile is NOT F&O. A spot index carries
# ZERO traded volume, so the volume-derived confirmations are structurally
# unusable; the signal that genuinely drives an index intraday — options/futures
# positioning — is already available via `get_options_analytics`. This block
# (a) enables + prioritizes options for the index in the ordinary
# INTRADAY/SWING/INVESTOR workspaces, (b) marks the spot-volume tools as
# EXPECTED-unavailable (not evidence against the setup), and (c) keeps options a
# calibration filter that never forces, blocks, or overrides the decision.
INDEX_OPTIONS_ADDENDUM = (
    "\n\n<index_options_context>\n"
    "SYMBOL CLASS: INDEX (spot underlying such as NIFTY 50 / BANKNIFTY). A spot index has NO traded "
    "volume, so the usual volume-derived confirmations are structurally unusable for THIS instrument. "
    "You MUST adapt your confirmation set accordingly.\n"
    "REQUIRED STEP — OVERRIDES the default 'skip options' behaviour of step 2e (FIND) AND step 2g (VERIFY): because this symbol is an INDEX, "
    "you MUST call `get_options_analytics` (with the index symbol and your proposed_direction) as part of "
    "your data gathering, in the SAME batch as your other analysis tools — do NOT skip it, and do NOT treat "
    "the INTRADAY/SWING/INVESTOR workspace as a reason to omit it. Omitting the options call for an index is "
    "an incomplete analysis. If it returns an Unavailable_Marker, note the reason and proceed on price "
    "structure (never fabricate) — but you must still ISSUE the call.\n"
    "- OPTIONS IS ENABLED AND PRIMARY HERE: call `get_options_analytics` for this index EVEN THOUGH the "
    "workspace is not F&O. Options/futures positioning — max pain, OI walls, PCR, IV skew, futures basis, "
    "`options_bias_state`, and `alignment` — is your PRIMARY confirmation for a directional decision on an "
    "index; lead your read with it alongside price structure.\n"
    "- SPOT-VOLUME TOOLS ARE EXPECTED-UNAVAILABLE: VWAP, volume profile (POC/VAH/VAL), OBV, CMF, and the "
    "candle-derived order-flow proxies are legitimately unavailable/zero for a spot index. Treat their "
    "absence as EXPECTED for the instrument — do NOT count it as missing evidence and do NOT downgrade or "
    "stand aside merely because these spot-volume signals are absent.\n"
    "- LEAD WITH: options/futures positioning + price structure (`get_support_resistance`, "
    "`get_multi_tf_trend`, `get_chart_patterns`, `get_forecast`) rather than spot volume.\n"
    "- OPTIONS REMAINS A CALIBRATION FILTER: when positioning conflicts with a proposed direction (a heavy "
    "call OI-wall just overhead, max-pain pinning against the trade, a PCR extreme), bias toward lower "
    "conviction, a different level, or HOLD. Options NEVER forces, blocks, or overrides the decision, and "
    "the Trade_Validator hard risk rules are unchanged. If `get_options_analytics` is unavailable, note it "
    "and proceed on price structure — never fabricate an options read.\n"
    "</index_options_context>"
)


# Profile-aware Risk:Reward floor override. The static prompt states the SWING/
# INVESTOR/F&O standard of 1:2 throughout; for the INTRADAY profile the committed
# minimum is relaxed to 1:1.3 (tight intraday ranges frequently cannot fit a 1:2
# target inside the session structure, which otherwise forces a permanent
# stand-aside). This addendum makes the ACTUAL floor for the run explicit to the
# model so it stops self-rejecting sound 1:1.3 setups — matching the profile-aware
# Trade_Validator (validator.min_risk_reward_for_profile). It relaxes ONLY the R:R
# minimum: the stop-distance floor (>= 1.5x ATR), direction-ordering, honest data,
# and every other hard rule are UNCHANGED. Empty for non-INTRADAY profiles so
# SWING/INVESTOR/F&O prompts stay byte-identical to today.
RISK_REWARD_FLOOR_ADDENDUM_INTRADAY = (
    "\n\n<risk_reward_floor>\n"
    "RISK-REWARD FLOOR (INTRADAY PROFILE) — OVERRIDES every \"1:2\" mentioned elsewhere in this prompt, "
    "for the R:R MINIMUM only. Your workspace profile is INTRADAY, so the MINIMUM acceptable Risk:Reward "
    "for a committed trade is 1:1.3, NOT 1:2. Intraday ranges are frequently too tight for a 1:2 (or even "
    "1:1.5) target to fit inside the session structure, and demanding that there forces a permanent "
    "stand-aside.\n"
    "- In the self-verification check \"Is the Risk:Reward ratio worse than 1:2?\", use 1:1.3 as the "
    "threshold instead: a setup whose reward:risk is >= 1.3 PASSES the R:R gate.\n"
    "- A directional setup with reward:risk >= 1.3 (stop still >= 1.5x ATR, clean structure, genuine "
    "confluence such as a high-confidence oversold bounce off support or a rejection at resistance) is a "
    "VALID, defensible trade — you MUST NOT stand aside merely because it does not reach 1:2 or 1:1.5. If "
    "you were about to HOLD only because \"no 1:2 bracket exists\", re-check whether a 1:1.3 bracket exists "
    "and, if it does and the setup is otherwise sound, `declare_trade` it.\n"
    "- The Trade_Validator now accepts R:R >= 1:1.3 on INTRADAY, so a 1:1.3 bracket will NOT be rejected.\n"
    "UNCHANGED for INTRADAY: the stop-distance floor (stop >= 1.5x ATR), direction-ordering, honest "
    "(non-fabricated) data, and every other hard rule remain fully in force. This override relaxes ONLY the "
    "R:R minimum from 1:2 to 1:1.3.\n"
    "</risk_reward_floor>"
)


def _resolve_risk_reward_floor_addendum(profile_key: str) -> str:
    """Return the profile-aware Risk:Reward floor addendum for the run.

    Non-empty only for the INTRADAY profile (floor relaxed to 1:1.3); empty for
    SWING / INVESTOR / FNO / unrecognized profiles so those prompts stay
    byte-identical to today (they keep the static 1:2 floor). Never raises.
    """
    return (
        RISK_REWARD_FLOOR_ADDENDUM_INTRADAY
        if isinstance(profile_key, str) and profile_key.strip().upper() == "INTRADAY"
        else ""
    )


def _resolve_profile_directive(state: AgentState) -> str:
    """Return the profile-specific directive block for the run's workspace profile.

    Falls back to the INTRADAY directive for a missing / unrecognized profile so
    the prompt is always well-formed. The FNO block is built dynamically so it can
    interpolate the symbol + selected expiry. Never raises.
    """
    raw = state.get("profile")
    key = raw.strip().upper() if isinstance(raw, str) and raw.strip() else "INTRADAY"
    if key == "FNO":
        return _build_fno_directive(state)
    return PROFILE_DIRECTIVES.get(key, PROFILE_DIRECTIVES["INTRADAY"])


def format_system_prompt(state: AgentState) -> str:
    mode = state.get("mode", "FIND")
    tf = state.get("timeframe") or "10m"
    tf_instruction = (
        f"\n\nCRITICAL TIMEFRAME REQUIREMENT:\n"
        f"The user's active chart timeframe is '{tf}'. You MUST conduct your deep quant analysis on the '{tf}' timeframe. "
        f"When calling tools such as `get_consensus_report`, `get_chart_patterns`, and `get_candles`, you MUST use '{tf}' as the timeframe argument."
    )
    # The profile directive tailors the data-gathering emphasis to the workspace
    # the user is in (INTRADAY / SWING / INVESTOR / FNO). Appended after the
    # timeframe requirement for both FIND/DEBATE and VERIFY runs.
    profile_directive = _resolve_profile_directive(state)
    # Symbol-class-aware options addendum: when the analyzed symbol is a spot
    # INDEX and the workspace is NOT F&O, append INDEX_OPTIONS_ADDENDUM to enable
    # + prioritize options and mark spot-volume tools expected-N/A. The symbol is
    # resolved from the SAME field `_build_fno_directive` reads (state["symbol"]),
    # and the profile is resolved the SAME way `_resolve_profile_directive` does.
    # The equity and F&O paths are left byte-identical (index_addendum == "").
    raw_profile = state.get("profile")
    profile_key = raw_profile.strip().upper() if isinstance(raw_profile, str) and raw_profile.strip() else "INTRADAY"
    index_addendum = ""
    if profile_key != "FNO" and classify_symbol_class(state.get("symbol")) == "index":
        index_addendum = INDEX_OPTIONS_ADDENDUM
    # Profile-aware Risk:Reward floor (INTRADAY -> 1:1.3, else unchanged 1:2).
    # Placed BEFORE index_addendum so the index==equity+INDEX_OPTIONS_ADDENDUM
    # prompt-contract holds (both share the same profile-derived floor text).
    rr_floor_addendum = _resolve_risk_reward_floor_addendum(profile_key)
    if mode == "VERIFY":
        trade = state.get("manual_trade") or {}
        base_prompt = RISK_MANAGER_PROMPT.format(
            side=trade.get("side", "N/A"),
            symbol=state.get("symbol", "N/A"),
            entry=trade.get("entry", 0),
            stop_loss=trade.get("stop_loss", 0),
            take_profit=trade.get("take_profit", 0),
            user_analysis=trade.get("user_analysis", "None")
        )
        return base_prompt + tf_instruction + profile_directive + rr_floor_addendum + index_addendum
    # F&O workspace (FIND/DEBATE) uses the dedicated options-desk prompt instead of
    # the equity/index base prompt. The FNO profile directive (_build_fno_directive,
    # already in profile_directive) is still appended so the exact symbol + selected
    # expiry + own_chain call instruction is interpolated. index_addendum is empty
    # for FNO (guarded above), and rr_floor_addendum is empty for FNO (1:2 floor).
    base = DEEP_QUANT_FNO_PROMPT if profile_key == "FNO" else DEEP_QUANT_SYSTEM_PROMPT
    return base + tf_instruction + profile_directive + rr_floor_addendum + index_addendum

# ── Model & Tools Binding ───────────────────────────────────────────────────

# ── LLM provider configuration ───────────────────────────────────────────────
# The LLM targets any OpenAI-compatible provider via three env vars (loaded from
# the repo-root .env above). The project standardizes on Google Gemini's
# OpenAI-compatible endpoint, so the defaults below reflect that — not a stale
# DeepSeek endpoint that would surface confusing errors against the wrong host.
def _env_nonempty(*names: str, default: str = "") -> str:
    """Return the first env var that is set AND non-empty.

    Unlike os.getenv(name, default), this also treats an empty/whitespace value
    (e.g. ``LLM_API_KEY=`` left blank in .env) as "unset", so we fall through to
    the next candidate / default instead of silently sending an empty key.
    """
    for name in names:
        val = os.getenv(name)
        if val and val.strip():
            return val.strip()
    return default

GEMINI_DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"

api_key = _env_nonempty("LLM_API_KEY", "GEMINI_API_KEY")
base_url = _env_nonempty("LLM_API_URL", default=GEMINI_DEFAULT_URL)
model_name = _env_nonempty("LLM_MODEL", default=GEMINI_DEFAULT_MODEL)

# ── Per-request LLM credentials (per-user OpenRouter key) ─────────────────────
# The Deep Quant service is shared, but each analysis run must use the
# REQUESTING user's OpenRouter key (resolved by main.py from the backend
# internal endpoint). These context variables carry that per-run override so
# every ChatOpenAI builder below binds the correct key/base WITHOUT a
# process-global mutation (concurrency-safe: each event_generator coroutine sets
# its own value). When unset, the module falls back to the env-configured
# api_key/base_url (local dev / self-hosted).
from contextvars import ContextVar  # noqa: E402

_run_api_key: ContextVar = ContextVar("_run_api_key", default=None)
_run_base_url: ContextVar = ContextVar("_run_base_url", default=None)


def set_run_llm_credentials(run_key, run_base_url) -> None:
    """Set the per-request LLM credentials for the current async task.

    Called by main.py's event_generator before ``graph.astream`` so all LLM
    builders in this run bind the user's key. Passing ``None`` leaves the env
    default in effect.
    """
    _run_api_key.set(run_key if (run_key and str(run_key).strip()) else None)
    base = run_base_url.strip() if isinstance(run_base_url, str) and run_base_url.strip() else None
    if base and base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base and base.endswith("/"):
        base = base[:-1]
    _run_base_url.set(base)


def _eff_api_key() -> str:
    """Effective LLM API key: the per-run override if set, else the env key."""
    return _run_api_key.get() or api_key


def _eff_base_url() -> str:
    """Effective LLM base URL: the per-run override if set, else the env base."""
    return _run_base_url.get() or base_url


def _creds_cache_tag() -> str:
    """A short, non-secret cache discriminator for the effective credentials so
    per-user bindings never collide in the shared build caches (keyed on the
    base URL + a hash of the key, never the raw key)."""
    return f"{_eff_base_url()}::{hash(_eff_api_key())}"

# ── Reasoning effort (FreeModel / OpenAI-compatible) ─────────────────────────
# LLM_EFFORT selects how hard the model "thinks": low | medium | high | xhigh.
# LLM_EFFORT_FIELD is the JSON body key carrying that value — FreeModel is
# OpenAI-compatible so it defaults to the standard ``reasoning_effort``; set it
# to ``effort`` if the provider expects that name instead. When LLM_EFFORT is
# unset/blank we send nothing, preserving prior behavior for plain models.
_effort = _env_nonempty("LLM_EFFORT")
_effort_field = _env_nonempty("LLM_EFFORT_FIELD", default="reasoning_effort")


def _effort_extra_body() -> dict:
    """Return the provider-specific body fragment carrying the effort level.

    Sent through ChatOpenAI's ``extra_body`` so non-standard values (e.g.
    ``xhigh``) pass through verbatim without client-side enum validation.
    Returns an empty dict when no effort is configured.
    """
    return {_effort_field: _effort} if _effort else {}

# Fail loud (in the log) on misconfiguration rather than silently using a fake
# key — every LLM call would otherwise 401, and the cause would be opaque.
if not api_key:
    print(
        "[deep-quant] WARNING: no LLM_API_KEY set in environment or .env. "
        "LLM calls will fail with an auth error. Set LLM_API_KEY (see .env)."
    )

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
    extra_body=_effort_extra_body(),
    default_headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    },
    # Honor the provider's Retry-After on 429s. This transparently absorbs
    # per-minute rate/token throttles (e.g. Groq's TPM window, which resets in
    # seconds) so a single throttled turn doesn't fail the whole run. It does
    # NOT rescue a per-DAY quota exhaustion (e.g. Gemini free tier) — nothing
    # client-side can, short of switching provider/model.
    max_retries=int(_env_nonempty("LLM_MAX_RETRIES", default="4")),
    timeout=float(_env_nonempty("LLM_TIMEOUT_SECS", default="90")),
)

tools = [
    get_candles,
    get_consensus_report,
    get_multi_tf_trend,
    get_chart_patterns,
    get_support_resistance,
    get_volume_profile,
    get_news_context,
    get_prediction,
    get_trade_performance,
    get_market_regime,
    get_relative_strength,
    get_order_flow,
    get_forecast,
    get_session_context,
    get_options_analytics,
    get_event_risk,
    watch_price_condition,
    declare_trade
]
llm_with_tools = llm.bind_tools(tools)

# ── Per-role read-only model factory (multi-agent-debate, R3.5/R6.3/R6.4) ─────
# The Bull and Bear roles argue over the ALREADY-gathered Shared_Evidence; they
# are bound to a READ-ONLY tool set that EXCLUDES the trade-committing /
# run-suspending tools so a debate role can never commit or suspend a trade
# (R3.5, R12.1). This exclusion set mirrors ``QA_FORBIDDEN_TOOLS`` (defined later
# for the Q&A sub-loop); the names are inlined here because the factory must be
# constructed alongside the base ``llm`` before that constant exists.
DEBATE_READONLY_EXCLUDED_TOOLS = {"declare_trade", "watch_price_condition"}

# The read-only Analysis_Tool set = the full tool list minus the excluded tools.
readonly_tools = [
    t for t in tools
    if getattr(t, "name", None) not in DEBATE_READONLY_EXCLUDED_TOOLS
]

# Default read-only-bound model — the graceful-degradation fallback used when a
# role-specific model cannot be constructed (R6.4). Bound to the SAME read-only
# tool set so the fallback also cannot commit/suspend a trade.
readonly_llm_with_tools = llm.bind_tools(readonly_tools)

# ── Profile-gated tool binding (workspace-scoped data access) ────────────────
# F&O / options data is ONLY relevant in the F&O workspace. In every other
# workspace (INTRADAY / SWING / INVESTOR) the agent must analyze ONLY the active
# symbol's own price / volume / structure, so the F&O-only tools are NOT bound
# there. This is the structural half of "F&O data only in F&O mode": if the tool
# is not bound, the model physically cannot call it, which also removes the
# broad-market NIFTY 50 chain that `get_options_analytics` would otherwise pull
# in for a non-index stock (the source of "NIFTY 50 keeps appearing" on an
# intraday run of a different symbol). The prompt half lives in the profile
# directives and the order-of-operations, which mark options as F&O-only.
FNO_ONLY_TOOLS = {"get_options_analytics"}

# The non-F&O Analysis tool set = the full tool list minus the F&O-only tools.
non_fno_tools = [
    t for t in tools
    if getattr(t, "name", None) not in FNO_ONLY_TOOLS
]
non_fno_llm_with_tools = llm.bind_tools(non_fno_tools)


# Cache of (model, profile-scope) -> tool-bound LLM for a user-selected model
# override. Reuses the SAME api_key / base_url as the system ``llm`` (so a chosen
# model string resolves when the deployment's LLM gateway serves it), and binds
# the profile-appropriate tool set (full for F&O, options-excluded otherwise).
_MODEL_PROFILE_LLM_CACHE: dict = {}


def _build_profile_llm_for_model(model: str, is_fno: bool):
    """Build (and cache) a tool-bound ChatOpenAI for a user-selected ``model``,
    binding the F&O or non-F&O tool set to match the workspace. Degrades to the
    default binding and NEVER raises if the client cannot be constructed.
    """
    scope = "fno" if is_fno else "nonfno"
    key = (model, scope, _creds_cache_tag())
    cached = _MODEL_PROFILE_LLM_CACHE.get(key)
    if cached is not None:
        return cached
    tool_set = tools if is_fno else non_fno_tools
    try:
        role_llm = ChatOpenAI(
            model=model,
            openai_api_key=_eff_api_key(),
            openai_api_base=_eff_base_url(),
            temperature=0.2,
            extra_body=_effort_extra_body(),
            default_headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            max_retries=int(_env_nonempty("LLM_MAX_RETRIES", default="4")),
            timeout=float(_env_nonempty("LLM_TIMEOUT_SECS", default="90")),
        )
        bound = role_llm.bind_tools(tool_set)
    except Exception as e:
        print(
            f"[Deep Quant] Could not build selected model {model!r}: {e}. "
            f"Falling back to the default binding."
        )
        bound = llm_with_tools if is_fno else non_fno_llm_with_tools
    _MODEL_PROFILE_LLM_CACHE[key] = bound
    return bound


def _llm_for_profile(state: "AgentState"):
    """Select the model binding for the run's workspace profile (and optional
    user-selected model override).

    The FULL tool set (including `get_options_analytics`) is bound for the F&O
    workspace OR when the analyzed symbol is a spot INDEX (NIFTY 50 / BANKNIFTY);
    every other case binds the set WITHOUT the F&O-only tools. For an index,
    options/futures positioning is the PRIMARY confirmation and spot volume is
    structurally N/A, so the options tool must actually be BOUND (not merely
    prompted for) in the ordinary INTRADAY/SWING/INVESTOR workspaces — otherwise
    the model is told to call a tool it was never given ("tool not exposed").
    When the run carries a non-empty ``model`` override, a per-model binding for
    the same scope is used; otherwise the default module bindings are returned.
    Any unset / unrecognized profile is treated as non-F&O, and a non-index
    symbol keeps the options-excluded binding (unchanged). Never raises.
    """
    raw = state.get("profile") if isinstance(state, dict) else None
    key = raw.strip().upper() if isinstance(raw, str) and raw.strip() else "INTRADAY"
    is_fno = key == "FNO"
    # Bind the options tool for the F&O workspace OR for a spot-index symbol in
    # any workspace (classify_symbol_class is total and never raises).
    expose_options = is_fno or classify_symbol_class(state.get("symbol") if isinstance(state, dict) else None) == "index"
    model = state.get("model") if isinstance(state, dict) else None
    if isinstance(model, str) and model.strip():
        return _build_profile_llm_for_model(model.strip(), expose_options)
    # When a per-run OpenRouter key is active (resolved per user), build the
    # binding with it — the pre-built module bindings use the env key. Default
    # to the deployment model when the run didn't select one.
    if _run_api_key.get():
        return _build_profile_llm_for_model(model_name, expose_options)
    return llm_with_tools if expose_options else non_fno_llm_with_tools

# Cache of read-only-bound role models keyed by (model_name, "readonly") so the
# repeated Bull/Bear turns across rounds reuse one bound client instead of
# rebuilding a ChatOpenAI on every node invocation.
_ROLE_LLM_CACHE: dict = {}


def _build_readonly_llm_for_model(role_model: str):
    """Build (and cache) a read-only-tool-bound ChatOpenAI for ``role_model``.

    Degrades gracefully and NEVER raises (R6.4): if constructing a role-specific
    client fails for any reason, falls back to the default read-only binding
    (``readonly_llm_with_tools``). Reuses the same api_key / base_url / retry /
    timeout configuration as the system ``llm``.
    """
    key = (role_model, "readonly", _creds_cache_tag())
    cached = _ROLE_LLM_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        role_llm = ChatOpenAI(
            model=role_model,
            openai_api_key=_eff_api_key(),
            openai_api_base=_eff_base_url(),
            temperature=0.2,
            extra_body=_effort_extra_body(),
            default_headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            max_retries=int(_env_nonempty("LLM_MAX_RETRIES", default="4")),
            timeout=float(_env_nonempty("LLM_TIMEOUT_SECS", default="90")),
        )
        bound = role_llm.bind_tools(readonly_tools)
    except Exception as e:
        print(
            f"[Deep Quant Debate] Could not build role model {role_model!r}: {e}. "
            f"Falling back to the default read-only binding."
        )
        bound = readonly_llm_with_tools
    _ROLE_LLM_CACHE[key] = bound
    return bound


def get_role_llm(role: str):
    """Return the cached read-only-bound LLM for a debate ``role``.

    Uses ``resolve_debate_config(model_name)`` to pick the per-role model
    (``bull_model`` / ``bear_model``), each defaulting to the system model when
    its env var is unset/empty/invalid (R6.3). Never raises (R6.4): any failure
    degrades to ``readonly_llm_with_tools``. Bull and Bear both bind to the
    read-only tool set, so ``declare_trade`` / ``watch_price_condition`` are not
    available to them regardless of the resolved model.
    """
    try:
        cfg = resolve_debate_config(model_name)
        role_norm = (role or "").strip().lower()
        if role_norm == "bull":
            role_model = cfg.bull_model
        elif role_norm == "bear":
            role_model = cfg.bear_model
        else:
            # Any other role falls back to the system model (read-only bound).
            role_model = model_name
        return _build_readonly_llm_for_model(role_model)
    except Exception as e:
        print(
            f"[Deep Quant Debate] get_role_llm({role!r}) failed: {e}. "
            f"Using the default read-only binding."
        )
        return readonly_llm_with_tools


# ── Judge full-tool model factory (multi-agent-debate, R4.5/R6.3/R6.4) ────────
# The Judge is the ONLY role permitted to commit a trade, so it binds the FULL
# tool set (including ``declare_trade``) — unlike the read-only Bull/Bear. The
# Judge's bounded read-only "targeted clarification" calls (R2.4) are policed in
# ``judge_node`` itself, not by the binding, so the full binding is correct here.
def _build_full_llm_for_model(role_model: str):
    """Build (and cache) a FULL-tool-bound ChatOpenAI for the Judge ``role_model``.

    Mirrors ``_build_readonly_llm_for_model`` but binds the complete ``tools``
    list so the Judge can call ``declare_trade``. Degrades gracefully and NEVER
    raises (R6.4): if constructing a role-specific client fails, falls back to
    the default full-tool binding (``llm_with_tools``).
    """
    key = (role_model, "full", _creds_cache_tag())
    cached = _ROLE_LLM_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        role_llm = ChatOpenAI(
            model=role_model,
            openai_api_key=_eff_api_key(),
            openai_api_base=_eff_base_url(),
            temperature=0.2,
            extra_body=_effort_extra_body(),
            default_headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            max_retries=int(_env_nonempty("LLM_MAX_RETRIES", default="4")),
            timeout=float(_env_nonempty("LLM_TIMEOUT_SECS", default="90")),
        )
        bound = role_llm.bind_tools(tools)
    except Exception as e:
        print(
            f"[Deep Quant Debate] Could not build judge model {role_model!r}: {e}. "
            f"Falling back to the default full-tool binding."
        )
        bound = llm_with_tools
    _ROLE_LLM_CACHE[key] = bound
    return bound


def get_judge_llm():
    """Return the cached FULL-tool-bound LLM for the Judge role.

    Uses ``resolve_debate_config(model_name)`` to pick ``judge_model`` (defaulting
    to the system model when its env var is unset/empty/invalid, R6.3). Never
    raises (R6.4): any failure degrades to ``llm_with_tools``.
    """
    try:
        cfg = resolve_debate_config(model_name)
        return _build_full_llm_for_model(cfg.judge_model)
    except Exception as e:
        print(
            f"[Deep Quant Debate] get_judge_llm() failed: {e}. "
            f"Using the default full-tool binding."
        )
        return llm_with_tools


# ── Per-run base (no-tools) model, honoring the per-user OpenRouter key ────────
_BASE_LLM_CACHE: dict = {}


def _base_llm_for_run():
    """Return a no-tools ChatOpenAI bound to the effective per-run credentials.

    Used by the Q&A final-turn (which must answer with no tools). When no per-run
    key is active this is the module ``llm``; otherwise a per-credential client is
    built and cached so the user's OpenRouter key is used. Never raises.
    """
    if not _run_api_key.get():
        return llm
    tag = _creds_cache_tag()
    cached = _BASE_LLM_CACHE.get(tag)
    if cached is not None:
        return cached
    try:
        built = ChatOpenAI(
            model=model_name,
            openai_api_key=_eff_api_key(),
            openai_api_base=_eff_base_url(),
            temperature=0.2,
            extra_body=_effort_extra_body(),
            default_headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            max_retries=int(_env_nonempty("LLM_MAX_RETRIES", default="4")),
            timeout=float(_env_nonempty("LLM_TIMEOUT_SECS", default="90")),
        )
    except Exception:  # noqa: BLE001
        built = llm
    _BASE_LLM_CACHE[tag] = built
    return built


# ── Nodes & Routing ─────────────────────────────────────────────────────────

import re
import json
import ast
import math

# Trade_Validator (Python mirror, task 5.2) — reused to derive the
# Risk_Reward_Ratio and to report per-check outcomes in VERIFY mode (R7.4).
from validator import Action, min_risk_reward_for_profile, MIN_RISK_REWARD

# ── Structured Tool-Call Extraction ──────────────────────────────────────────
# The registered Analysis_Tool set. A tool name discovered in model output that
# is not in this set is classified as an invalid-tool call.
REGISTERED_TOOL_NAMES = {
    "get_candles",
    "get_consensus_report",
    "get_multi_tf_trend",
    "get_chart_patterns",
    "get_support_resistance",
    "get_volume_profile",
    "get_news_context",
    "get_prediction",
    "get_trade_performance",
    "get_market_regime",
    "get_relative_strength",
    "get_order_flow",
    "get_forecast",
    "get_session_context",
    "get_options_analytics",
    "get_event_risk",
    "watch_price_condition",
    "declare_trade",
}

# The subset of Analysis_Tools that return market data (used to maintain the
# `market_data_seen` flag). watch_price_condition and declare_trade are control
# tools, not market-data sources.
MARKET_DATA_TOOL_NAMES = {
    "get_candles",
    "get_consensus_report",
    "get_multi_tf_trend",
    "get_chart_patterns",
    "get_support_resistance",
    "get_volume_profile",
    "get_news_context",
    "get_prediction",
    "get_market_regime",
    "get_relative_strength",
    "get_order_flow",
    "get_forecast",
    "get_session_context",
    "get_options_analytics",
    "get_event_risk",
}

# The FIND-critical Core_Data_Tools whose first-pass acquisition the R6 heartbeat
# gate keys on (design R6 / Property 14). These are the tools that establish the
# baseline read the model needs before a heartbeat wake is allowed to precipitate
# a stand-aside HOLD: the market regime (also read via the consensus report),
# relative strength vs the benchmark, the session/time-of-day context, and the
# order-flow tape. `market_data_seen` latches True on ANY single market-data tool,
# which is why it cannot express "all core tools have finished their first pass" —
# `_core_acquisition_resolved` (below) is the per-tool predicate that can. Kept a
# strict subset of MARKET_DATA_TOOL_NAMES so both flags share the same
# usable/unavailable/error result conventions.
CORE_DATA_TOOL_NAMES = {
    "get_market_regime",
    "get_consensus_report",
    "get_relative_strength",
    "get_session_context",
    "get_order_flow",
}

# DeepSeek/HuggingFace custom-token markup boundaries.
_CALL_BLOCK_RE = re.compile(r"<｜tool▁call▁begin｜>(.*?)<｜tool▁call▁end｜>", re.DOTALL)
_SEP_NAME_RE = re.compile(r"<｜tool▁sep｜>\s*([^\s`{]+)")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\uFEFF]")


@dataclass
class ExtractedCall:
    """A single tool call discovered in a model response.

    status:
      - "ok"            args parsed into a valid JSON object; safe to execute
      - "parse_failure" args fragment could not be parsed into JSON
      - "invalid_tool"  tool name is not a registered Analysis_Tool
    """
    name: str
    args: Optional[dict]
    raw_args: str
    status: Literal["ok", "parse_failure", "invalid_tool"]
    id: str


@dataclass
class ToolCallExtraction:
    calls: List[ExtractedCall] = field(default_factory=list)
    used_text_extraction: bool = False


def _extract_balanced_json(text: str, start: int) -> Optional[str]:
    """Return the first brace-balanced ``{...}`` substring at/after ``start``.

    Returns None when no opening brace is found. The matcher is string-aware so
    braces inside JSON string literals do not unbalance the scan.
    """
    open_idx = text.find("{", start)
    if open_idx == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
    return None


def _scan_custom_token_calls(content: str) -> List[tuple]:
    """Scan raw content for custom-token tool-call markup.

    Returns a list of ``(name, raw_args)`` tuples in source order. Both
    registered and unregistered tool names are returned so the caller can
    classify them; classification (ok / parse_failure / invalid_tool) is the
    caller's responsibility.
    """
    if not content:
        return []

    discovered: List[tuple] = []

    # Tier 1: explicit DeepSeek call blocks delimited by tool-call tokens.
    blocks = list(_CALL_BLOCK_RE.finditer(content))
    if blocks:
        for block in blocks:
            inner = block.group(1)
            sep_match = _SEP_NAME_RE.search(inner)
            if sep_match:
                name = sep_match.group(1).strip()
                json_start = sep_match.end()
            else:
                # No separator token — take the first identifier-looking token.
                name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", inner)
                if not name_match:
                    continue
                name = name_match.group(1).strip()
                json_start = name_match.end()
            raw_args = _extract_balanced_json(inner, json_start)
            discovered.append((name, raw_args if raw_args is not None else ""))
        return discovered

    # Tier 2: separator tokens present without explicit call-block wrappers.
    sep_matches = list(_SEP_NAME_RE.finditer(content))
    if sep_matches:
        for sep in sep_matches:
            name = sep.group(1).strip()
            raw_args = _extract_balanced_json(content, sep.end())
            discovered.append((name, raw_args if raw_args is not None else ""))
        return discovered

    # Tier 3: plain-text fallback. Without markup tokens we can only reliably
    # anchor on registered tool names; locate each followed by a JSON object,
    # preserving source order by position.
    positioned: List[tuple] = []
    for tool_name in REGISTERED_TOOL_NAMES:
        for m in re.finditer(re.escape(tool_name), content):
            raw_args = _extract_balanced_json(content, m.end())
            if raw_args is not None:
                positioned.append((m.start(), tool_name, raw_args))
    positioned.sort(key=lambda t: t[0])
    return [(name, raw_args) for _pos, name, raw_args in positioned]


def _parse_args_fragment(raw_args: str):
    """Attempt to parse a JSON args fragment.

    Returns ``(parsed_dict, True)`` on success or ``(None, False)`` on failure.
    A zero-width-character cleanup pass is attempted before giving up.
    """
    if raw_args is None:
        return None, False
    try:
        parsed = json.loads(raw_args)
        if isinstance(parsed, dict):
            return parsed, True
        return None, False
    except Exception:
        cleaned = _ZERO_WIDTH_RE.sub("", raw_args)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed, True
            return None, False
        except Exception:
            return None, False


def extract_tool_calls(response) -> ToolCallExtraction:
    """Extract every tool call from a model response in source order.

    Native structured ``tool_calls`` are the primary path: when present, each is
    wrapped as an ``ExtractedCall`` with status ``ok`` and NO text-based
    extraction is applied (Requirement 1.1). Otherwise the response content is
    scanned for custom-token markup and each discovered call is classified as
    ``ok`` / ``parse_failure`` / ``invalid_tool`` (Requirements 1.2-1.4). Every
    discovered call is preserved in order; none are dropped (Requirement 1.5).
    """
    extraction = ToolCallExtraction()

    native_calls = getattr(response, "tool_calls", None) or []
    if native_calls:
        # Primary path: trust the provider's structured tool calls verbatim.
        for idx, tc in enumerate(native_calls):
            name = (tc.get("name") or "").strip()
            args = tc.get("args")
            if isinstance(args, str):
                parsed, ok = _parse_args_fragment(args)
                args = parsed if ok else None
            call_id = tc.get("id") or f"call_{name}_{idx}"
            extraction.calls.append(
                ExtractedCall(
                    name=name,
                    args=args if isinstance(args, dict) else {},
                    raw_args=tc.get("args") if isinstance(tc.get("args"), str) else json.dumps(args) if args is not None else "",
                    status="ok",
                    id=call_id,
                )
            )
        extraction.used_text_extraction = False
        return extraction

    # Fallback path: parse custom-token markup out of the content string.
    content = getattr(response, "content", "") or ""
    discovered = _scan_custom_token_calls(content)
    if discovered:
        extraction.used_text_extraction = True

    for idx, (name, raw_args) in enumerate(discovered):
        call_id = f"call_{name}_{idx}"
        if name not in REGISTERED_TOOL_NAMES:
            extraction.calls.append(
                ExtractedCall(
                    name=name,
                    args=None,
                    raw_args=raw_args,
                    status="invalid_tool",
                    id=call_id,
                )
            )
            continue
        parsed, ok = _parse_args_fragment(raw_args)
        if ok:
            extraction.calls.append(
                ExtractedCall(
                    name=name,
                    args=parsed,
                    raw_args=raw_args,
                    status="ok",
                    id=call_id,
                )
            )
        else:
            extraction.calls.append(
                ExtractedCall(
                    name=name,
                    args=None,
                    raw_args=raw_args,
                    status="parse_failure",
                    id=call_id,
                )
            )
    return extraction


def _synthetic_failure_content(call: ExtractedCall) -> str:
    """Build the synthetic ToolMessage feedback for a non-executable call."""
    if call.status == "invalid_tool":
        return (
            f"Tool-call error: '{call.name}' is not a registered Analysis_Tool. "
            f"Registered tools are: {', '.join(sorted(REGISTERED_TOOL_NAMES))}. "
            f"Re-issue the call using a valid tool name."
        )
    return (
        f"Tool-call error: could not parse JSON arguments for '{call.name}'. "
        f"Received fragment: {call.raw_args!r}. "
        f"Re-issue the call with a valid JSON object as arguments."
    )


def _is_tool_message(message) -> bool:
    """True when ``message`` is a ToolMessage (tool result)."""
    if isinstance(message, ToolMessage):
        return True
    return getattr(message, "type", None) == "tool"


def _is_human_message(message) -> bool:
    """True when ``message`` is a HumanMessage (user turn)."""
    if isinstance(message, HumanMessage):
        return True
    return getattr(message, "type", None) == "human"


# ── Tool-payload compaction for the LLM turn (latency fix) ────────────────────
# The synthesis turn was stalling because the context carried very large raw tool
# payloads — chart_patterns alone returns 13-15 near-duplicate patterns with ~10
# internal fields each (~5-6 KB), and news carries many headlines. Compacting
# these to their decision-relevant fields BEFORE the LLM call shrinks the token
# count (and latency) markedly on any model. This only affects what is SENT this
# turn — it never mutates the checkpointed history, so build_defensibility_record
# / build_qa_context still read the full original payloads. Never raises.

_COMPACT_MIN_CHARS = 1200          # only touch a ToolMessage larger than this
_COMPACT_GENERIC_CAP = 2500        # hard cap for any other oversized payload
_COMPACT_MAX_PATTERNS = 8          # distinct chart patterns to keep (by confidence)
_COMPACT_MAX_HEADLINES = 3         # news headlines to keep


def _compact_one_payload(name, content: str) -> str:
    """Compact one ToolMessage's JSON string to its decision-relevant fields.

    Returns the compacted JSON string (or a hard-capped slice for non-JSON /
    still-oversized content). Total and non-raising: any parse/shape it does not
    recognize is returned unchanged (subject only to the generic size cap)."""
    try:
        data = json.loads(content)
    except Exception:
        return content if len(content) <= _COMPACT_GENERIC_CAP else content[:_COMPACT_GENERIC_CAP] + " ...[trimmed]"
    if not isinstance(data, dict):
        return content

    if name == "get_chart_patterns":
        pats = data.get("patterns")
        if isinstance(pats, list) and pats:
            seen: dict = {}
            for p in pats:
                if not isinstance(p, dict):
                    continue
                try:
                    conf = round(float(p.get("confidence") or 0.0), 2)
                except Exception:
                    conf = 0.0
                key = (p.get("pattern_type"), p.get("sentiment"), conf)
                if key in seen:
                    continue
                seen[key] = {
                    "pattern_type": p.get("pattern_type"),
                    "sentiment": p.get("sentiment"),
                    "confidence": p.get("confidence"),
                    "description": p.get("description"),
                    "breakout_status": p.get("breakout_status"),
                }
            uniq = sorted(seen.values(), key=lambda x: -(x.get("confidence") or 0.0))[:_COMPACT_MAX_PATTERNS]
            slim = {
                "symbol": data.get("symbol"),
                "timeframe": data.get("timeframe"),
                "distinct_patterns": len(seen),
                "patterns": uniq,
            }
            return json.dumps(slim, default=str)

    if name == "get_news_context":
        hl = data.get("headlines")
        if isinstance(hl, list) and len(hl) > _COMPACT_MAX_HEADLINES:
            trimmed = dict(data)
            trimmed["headlines"] = hl[:_COMPACT_MAX_HEADLINES]
            trimmed["headlines_omitted"] = len(hl) - _COMPACT_MAX_HEADLINES
            return json.dumps(trimmed, default=str)

    # Generic size cap for any other oversized payload (e.g. volume profile with
    # long HVN/LVN arrays): re-serialize compactly and hard-cap the string.
    s = json.dumps(data, default=str)
    return s if len(s) <= _COMPACT_GENERIC_CAP else s[:_COMPACT_GENERIC_CAP] + " ...[trimmed]"


def compact_tool_payloads(messages):
    """Return a shallow copy of ``messages`` with large ToolMessage payloads
    compacted to their decision-relevant fields (see :func:`_compact_one_payload`).

    Only ToolMessages whose content exceeds ``_COMPACT_MIN_CHARS`` are touched;
    everything else passes through by reference. New ToolMessage objects are built
    for the compacted entries so the ORIGINAL checkpointed messages are never
    mutated (the defensibility record / Q&A grounding keep the full payloads).
    Never raises — on any error the original message is kept."""
    out = []
    for m in messages:
        try:
            if type(m).__name__ != "ToolMessage":
                out.append(m)
                continue
            content = getattr(m, "content", None)
            if not isinstance(content, str) or len(content) < _COMPACT_MIN_CHARS:
                out.append(m)
                continue
            compacted = _compact_one_payload(getattr(m, "name", None), content)
            if not isinstance(compacted, str) or compacted == content:
                out.append(m)
                continue
            out.append(
                ToolMessage(
                    content=compacted,
                    tool_call_id=getattr(m, "tool_call_id", "") or "",
                    name=getattr(m, "name", None),
                )
            )
        except Exception:
            out.append(m)
    return out


def _ai_message_has_tool_calls(message) -> bool:
    """True when an AIMessage carries tool calls (typed or raw kwargs)."""
    if getattr(message, "tool_calls", None):
        return True
    raw = (getattr(message, "additional_kwargs", None) or {}).get("tool_calls")
    return bool(raw)


def flatten_prior_tool_history(messages):
    """Return a provider-safe message list for a Q&A replay.

    Strict OpenAI-compatible providers (e.g. FreeModel / GPT-5-family via the
    Responses API) reject any request that replays an assistant *function call*
    without its paired *tool output* — surfacing as
    ``400 No tool output found for function call ...``. A PAUSED analysis thread
    carries exactly such pairs (notably the ``watch_price_condition`` exchange),
    and re-sending that raw history on a Q&A turn trips the provider. Gemini
    tolerated it; GPT-5-family does not.

    The Q&A grounding system prompt already embeds the recorded analysis facts,
    so the raw prior tool exchange is not needed. This helper keeps:
      * everything from the CURRENT turn onward (the last HumanMessage and after)
        verbatim — so a freshly fetched read-only tool call stays paired with its
        output, and
      * only the plain TEXT of the PRIOR conversation (Human turns and any AI
        prose), dropping prior ToolMessages and stripping tool-call metadata from
        prior AIMessages.

    The result can never contain an orphaned function call, so it is safe to
    replay against a strict provider.
    """
    msgs = list(messages)
    last_human = -1
    for idx, m in enumerate(msgs):
        if _is_human_message(m):
            last_human = idx

    safe = []
    for idx, m in enumerate(msgs):
        # Current turn (last user question + its in-turn tool exchange): verbatim.
        if last_human != -1 and idx >= last_human:
            safe.append(m)
            continue
        # Prior history: drop tool outputs outright.
        if _is_tool_message(m):
            continue
        # Prior assistant turns: keep prose only, never the tool-call metadata.
        if isinstance(m, AIMessage) or getattr(m, "type", None) == "ai":
            if _ai_message_has_tool_calls(m):
                content = getattr(m, "content", "") or ""
                if isinstance(content, str) and content.strip():
                    safe.append(AIMessage(content=content))
                # else: a pure tool-call turn carries no prose — drop it.
                continue
            safe.append(m)
            continue
        # Human / other text turns: keep.
        safe.append(m)
    return safe


def _tool_result_is_error(content) -> bool:
    """Heuristically decide whether a tool result represents a failure.

    Tool functions in this stack return a structured payload carrying an
    ``"error"`` key (or an ``error:`` string) when they fail. A result without
    such a marker is treated as usable market data.
    """
    if content is None:
        return True
    text = content if isinstance(content, str) else str(content)
    stripped = text.strip().lower()
    return '"error"' in text or "'error'" in text or stripped.startswith("error")


# Graceful-degradation markers (R5/R10/R12): an engine that cannot produce a
# result returns an explicit "unavailable" marker rather than fabricating data.
# Such a result is NOT usable directional data and must not, on its own, satisfy
# the first-turn data-acquisition gate. In particular an `Unavailable` sentiment
# result is treated as a missing — but non-blocking — input (R10.4). Both JSON
# (`"..."`) and Python dict-repr (`'...'`) quoting styles are matched.
_UNAVAILABLE_RE = re.compile(
    r"['\"]sentiment_summary['\"]\s*:\s*['\"]unavailable['\"]"
    r"|['\"]unavailable['\"]\s*:\s*true"
    r"|['\"]status['\"]\s*:\s*['\"]unavailable['\"]",
    re.IGNORECASE,
)


def _tool_result_is_unavailable(content) -> bool:
    """True when a tool result carries an explicit graceful-degradation marker.

    These results (e.g. ``{"sentiment_summary": "Unavailable"}``) represent a
    missing input rather than usable market data, so they neither count toward
    ``market_data_seen`` nor block a decision on their own (R10.4).
    """
    if content is None:
        return False
    text = content if isinstance(content, str) else str(content)
    return bool(_UNAVAILABLE_RE.search(text))


def _market_data_seen(messages) -> bool:
    """True once any market-data Analysis_Tool has returned usable data.

    A market-data tool result counts only when it is neither an error nor an
    explicit unavailable marker — so an `Unavailable` sentiment (or any other
    graceful-degradation marker) does not, by itself, satisfy the gate (R10.4).
    """
    for m in messages:
        if not _is_tool_message(m):
            continue
        name = getattr(m, "name", None)
        if name not in MARKET_DATA_TOOL_NAMES:
            continue
        content = getattr(m, "content", None)
        if _tool_result_is_error(content) or _tool_result_is_unavailable(content):
            continue
        return True
    return False


def _market_data_attempted(messages) -> bool:
    """True once any market-data Analysis_Tool has been called this run.

    Distinguishes a premature finalize (no market-data tool attempted yet → block
    and keep gathering data, R3.3) from a finalize where directional data was
    sought but is unavailable (tools attempted but all failed/unavailable → HOLD
    with a stated data limitation, R5.3).
    """
    for m in messages:
        if _is_tool_message(m) and getattr(m, "name", None) in MARKET_DATA_TOOL_NAMES:
            return True
    return False


def _tool_result_is_usable(content) -> bool:
    """True when a tool result carries real, usable data.

    A result is usable when it is present, is NOT a failure (no ``error`` marker
    per ``_tool_result_is_error``), and is NOT an explicit graceful-degradation
    Unavailable_Marker (per ``_tool_result_is_unavailable``). This is the
    complement used to build the core-acquisition predicate: a usable result and
    an explicit unavailable result both count as *resolved*, while a still-failing
    (hard error) or absent result does not. Total — never raises.
    """
    if content is None:
        return False
    return not _tool_result_is_error(content) and not _tool_result_is_unavailable(content)


def _resolved_core_tools(messages) -> set:
    """The set of Core_Data_Tools that have *resolved* their first-pass acquisition.

    A core tool is resolved once it has returned — at least once this run —
    either usable data (``_tool_result_is_usable``) OR an explicit
    Unavailable_Marker (``_tool_result_is_unavailable``). A core tool that has
    only ever produced a hard error (still failing) or was never called is NOT
    included, which is precisely how this distinguishes "explicitly unavailable"
    (a resolved, non-blocking missing input) from "not yet acquired" (design
    Property 14).

    Pure and total: it reads only the message history and never raises over
    malformed, empty, or non-tool messages. Returned so the heartbeat gate can
    name the still-unresolved core tools in its feedback (task 14.2).
    """
    resolved = set()
    for m in messages:
        if not _is_tool_message(m):
            continue
        name = getattr(m, "name", None)
        if name not in CORE_DATA_TOOL_NAMES:
            continue
        content = getattr(m, "content", None)
        if _tool_result_is_usable(content) or _tool_result_is_unavailable(content):
            resolved.add(name)
    return resolved


def _core_acquisition_resolved(messages) -> bool:
    """True once every Core_Data_Tool has *resolved* its first-pass acquisition.

    A core tool is resolved once it has returned — at least once this run —
    either usable data (``_tool_result_is_usable``) OR an explicit
    Unavailable_Marker (``_tool_result_is_unavailable``). A core tool that has
    only ever produced a hard error (still failing) or was never called is NOT
    counted as resolved, which is precisely how this predicate distinguishes
    "explicitly unavailable" (a resolved, non-blocking missing input) from
    "not yet acquired" (design Property 14). Unlike ``market_data_seen`` — which
    latches True on ANY single market-data tool — this returns True only when
    EVERY core tool has resolved.

    Pure and total: it reads only the message history and never raises over
    malformed, empty, or non-tool messages.
    """
    return CORE_DATA_TOOL_NAMES.issubset(_resolved_core_tools(messages))


def _declare_is_directional(args) -> bool:
    """True when a declare_trade's args commit a directional BUY/SELL.

    A directional trade is NEVER gated by the heartbeat acquisition gate (task
    14.2). Anything that is not an explicit BUY/SELL — a HOLD, a stand-aside, or
    a missing/malformed action — is treated as non-directional (fail-safe), so a
    premature stand-aside cannot slip through the gate. Pure and total.
    """
    action = (args or {}).get("action")
    if not isinstance(action, str):
        return False
    return action.strip().upper() in {"BUY", "SELL"}


def _decision_from_declare(ok_calls) -> Optional[dict]:
    """Build the structured decision from a declare_trade tool call, if present.

    The declared trade's structured arguments — not any prose — are the
    authoritative completion signal read by ``should_continue`` (R2.2, R2.7).
    The structured execution levels (entry/stop_loss/take_profit/atr_14) are
    carried through so the defensibility record can cite them directly rather
    than re-parsing them out of the plan prose.
    """
    for tc in ok_calls:
        if tc.get("name") == "declare_trade":
            args = tc.get("args") or {}
            return {
                "action": (args.get("action") or "HOLD"),
                "conviction_score": args.get("conviction_score"),
                "setup_validation": args.get("setup_validation"),
                "execution_plan": args.get("execution_plan"),
                "entry": args.get("entry"),
                "stop_loss": args.get("stop_loss"),
                "take_profit": args.get("take_profit"),
                "atr_14": args.get("atr_14"),
                # The optional multi-leg Management_Plan dict (legs / breakeven /
                # trailing) the agent attached to declare_trade, carried through so
                # build_defensibility_record can cite the committed plan (R9.1).
                "management_plan": args.get("management_plan"),
                "source": "declare_trade",
            }
    return None


def _declare_was_rejected(messages) -> bool:
    """True when a declare_trade tool result indicates the server rejected it.

    The declare_trade tool returns a ``TRADE_REJECTED: ...`` marker when the
    authoritative Trade_Validator on the Rust server refuses to commit the trade
    (R6.7). When rejected, the run must NOT finalize on that declaration — the
    bounded loop continues so the agent can revise the levels and re-declare.
    """
    for m in messages:
        if _is_tool_message(m) and getattr(m, "name", None) == "declare_trade":
            content = getattr(m, "content", None)
            text = content if isinstance(content, str) else str(content)
            if "TRADE_REJECTED" in text:
                return True
    return False


# ── Trade Defensibility Record (Requirement 7) ───────────────────────────────
# When a trade is committed, it must carry the evidence behind it so the trader
# can review and defend it (R7). The record is assembled ENTIRELY from the
# Analysis_Tool results already present in the message history — nothing is
# fabricated (R5.4). It captures: the multi-timeframe trend bias (R7.1), the key
# support/resistance levels used (R7.1), the volatility basis for the stop
# (atr_14, R7.1), the Risk_Reward_Ratio (R7.2), any high-confidence chart
# patterns >0.6 (R7.3 / R11.3), a predictive-conflict statement (R12.3), and a
# macro-trend-conflict statement (R13.3). In VERIFY mode it additionally reports
# the outcome of every Trade_Validator check for the user-proposed trade (R7.4).

# Confidence threshold above which a chart pattern is named in the thesis (R7.3).
PATTERN_CONFIDENCE_THRESHOLD = 0.6

_LEVEL_NUM = r"([0-9]+(?:\.[0-9]+)?)"
# Negative-lookahead guard rejecting a captured number that is actually a
# volatility *multiplier* ("1.5x ATR") or an ATR token rather than a price: the
# number must NOT be immediately followed (optionally after further digits/dots
# or whitespace) by an x / X / × multiplier token or an ATR token (R4.2). This
# stops "stop >= 1.5x ATR" from yielding stop_loss=1.5 (or the backtracked 1).
_NOT_MULT = r"(?![0-9.]*\s*(?:[xX" + "\u00d7" + r"]|[Aa][Tt][Rr]))"
_ENTRY_RE = re.compile(r"entry\b[^0-9\-]*" + _LEVEL_NUM + _NOT_MULT, re.IGNORECASE)
_SL_RE = re.compile(
    r"(?:stop[\s\-]?loss|stop|sl)\b[^0-9\-]*" + _LEVEL_NUM + _NOT_MULT,
    re.IGNORECASE,
)
# Optionally consume a "Target N" ordinal label (N + a :/.)/- delimiter) so the
# PRICE that follows is captured, not the ordinal — "Target 1: 24300" yields
# 24300 not 1 (R4.3). The optional ordinal-consume and the number-gap are wrapped
# in an atomic group so a failed price match cannot backtrack and re-capture the
# ordinal digit as the price; a bare "target 24300" (no ordinal) still captures
# 24300 since the ordinal requires a trailing delimiter.
_TP_RE = re.compile(
    r"(?:take[\s\-]?profit|target|tp)\b(?>(?:\s*\d{1,2}\s*[:.)\-])?[^0-9\-]*)"
    + _LEVEL_NUM + _NOT_MULT,
    re.IGNORECASE,
)


def _is_finite_num(x) -> bool:
    """True when ``x`` is a finite real number (bools are not numbers here)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _parse_tool_content(content):
    """Best-effort parse of a ToolMessage payload into a Python object.

    Tool results may be serialized as JSON (double-quoted) or as a Python
    dict/list repr (single-quoted) depending on the serializer, so both styles
    are attempted. Returns the parsed object, or ``None`` when it cannot be
    parsed.
    """
    if content is None:
        return None
    if isinstance(content, (dict, list)):
        return content
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        return None


def _latest_tool_results(messages) -> dict:
    """Map each tool name to its most recent successfully parsed, non-error result.

    Error results (those carrying an ``error`` marker) are skipped so the record
    cites only usable data (R5.4). Later results win, reflecting the agent's most
    recent view of the market.
    """
    results: dict = {}
    for m in messages:
        if not _is_tool_message(m):
            continue
        name = getattr(m, "name", None)
        if not name:
            continue
        content = getattr(m, "content", None)
        if _tool_result_is_error(content):
            continue
        parsed = _parse_tool_content(content)
        if parsed is None:
            continue
        results[name] = parsed
    return results


def _collect_high_confidence_patterns(messages, threshold=PATTERN_CONFIDENCE_THRESHOLD):
    """Collect chart patterns with confidence strictly above ``threshold`` (R7.3).

    Patterns are gathered across EVERY get_chart_patterns result in the history
    (the agent may scan several timeframes), de-duplicated on
    ``(pattern_type, timeframe, confidence)``.
    """
    found = []
    seen = set()
    for m in messages:
        if not _is_tool_message(m) or getattr(m, "name", None) != "get_chart_patterns":
            continue
        content = getattr(m, "content", None)
        if _tool_result_is_error(content):
            continue
        parsed = _parse_tool_content(content)
        if not isinstance(parsed, dict):
            continue
        tf = parsed.get("timeframe")
        for p in parsed.get("patterns", []) or []:
            if not isinstance(p, dict):
                continue
            conf = p.get("confidence")
            if not _is_finite_num(conf) or conf <= threshold:
                continue
            key = (p.get("pattern_type"), tf, round(float(conf), 4))
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "pattern_type": p.get("pattern_type"),
                "sentiment": p.get("sentiment"),
                "confidence": float(conf),
                "description": p.get("description"),
                "timeframe": tf,
            })
    return found


def _normalize_action(s):
    """Normalize a free-form action/side string to BUY / SELL / HOLD (or None)."""
    if not s:
        return None
    n = str(s).strip().upper()
    if n in ("BUY", "LONG"):
        return "BUY"
    if n in ("SELL", "SHORT"):
        return "SELL"
    if n == "HOLD":
        return "HOLD"
    return n


def _parse_levels_from_text(text):
    """Extract entry / stop-loss / take-profit prices from a free-form plan.

    The declare_trade tool carries levels only inside the prose ``execution_plan``
    (and ``setup_validation``), so they are recovered here for the RR computation.
    Returns a dict with whatever subset was found (or ``None``).
    """
    if not text:
        return None
    levels = {}
    e = _ENTRY_RE.search(text)
    s = _SL_RE.search(text)
    t = _TP_RE.search(text)
    if e:
        levels["entry"] = float(e.group(1))
    if s:
        levels["stop_loss"] = float(s.group(1))
    if t:
        levels["take_profit"] = float(t.group(1))
    return levels or None


def _resolve_action_and_levels(decision, mode, manual_trade):
    """Resolve the trade action and execution levels for the record.

    In VERIFY mode the levels are the user-proposed ones (manual_trade). In FIND
    mode the action comes from the committed decision and the levels are parsed
    out of the execution plan / setup validation prose.
    """
    if mode == "VERIFY" and manual_trade:
        action = _normalize_action(manual_trade.get("side"))
        levels = {}
        for k in ("entry", "stop_loss", "take_profit"):
            v = manual_trade.get(k)
            if _is_finite_num(v):
                levels[k] = float(v)
        return action, (levels or None)
    action = _normalize_action((decision or {}).get("action"))
    # Prefer the structured execution levels the agent passed to declare_trade
    # (entry/stop_loss/take_profit); only fall back to parsing the plan prose
    # when they are not all present.
    d = decision or {}
    structured = {}
    for k in ("entry", "stop_loss", "take_profit"):
        v = d.get(k)
        if _is_finite_num(v):
            structured[k] = float(v)
    if len(structured) == 3:
        return action, structured
    text = " ".join(
        part for part in [
            (decision or {}).get("execution_plan"),
            (decision or {}).get("setup_validation"),
        ] if part
    )
    return action, _parse_levels_from_text(text)


def _latest_watch_args(messages) -> dict:
    """The args of the most recent ``watch_price_condition`` tool call in history.

    Scans backward for the assistant message that armed the most recent watch and
    returns its call args (``symbol`` / ``timeframe`` / ``price_level`` /
    ``direction`` / ``invalidation_level``), so the invalidation post-mortem can
    fingerprint the just-invalidated thesis. Returns ``{}`` when none is found.
    """
    for m in reversed(list(messages or [])):
        for tc in (getattr(m, "tool_calls", None) or []):
            if tc.get("name") == "watch_price_condition":
                return tc.get("args") or {}
    return {}


def _latest_atr(results):
    """The most recent finite ``atr_14`` from a consensus report, else None."""
    consensus = results.get("get_consensus_report")
    if isinstance(consensus, dict):
        atr = consensus.get("atr_14")
        if _is_finite_num(atr):
            return float(atr)
    return None


def _extract_1d_bias(multi_tf):
    """Pull the 1D (daily) directional bias string from a multi-TF trend result."""
    if not isinstance(multi_tf, dict):
        return None
    for k, v in multi_tf.items():
        kl = str(k).lower()
        if isinstance(v, str) and ("1d" in kl or "1day" in kl or "daily" in kl or kl == "day"):
            return v
    return None


def _bias_sign(bias):
    """Normalize a bias string to Bullish / Bearish / Neutral (or None)."""
    if not bias:
        return None
    b = str(bias).lower()
    if "bull" in b or b == "up":
        return "Bullish"
    if "bear" in b or b == "down":
        return "Bearish"
    if "neutral" in b or "flat" in b:
        return "Neutral"
    return None


def _predictive_direction(results):
    """Derive the forward projected direction and its source from tool results.

    Prefers an explicit get_prediction projection; otherwise falls back to the
    sign of the consensus OLS / VWEPR projection slope. Returns
    ``(direction, source)`` or ``(None, None)`` when no projection is available.
    """
    pred = results.get("get_prediction")
    if isinstance(pred, dict):
        d = pred.get("projected_direction")
        if isinstance(d, str) and d.strip():
            return d.strip().capitalize(), "predictive-engine"
    consensus = results.get("get_consensus_report")
    if isinstance(consensus, dict):
        for key in ("ols_slope", "vwepr_slope"):
            slope = consensus.get(key)
            if _is_finite_num(slope):
                if slope > 0:
                    return "Up", key
                if slope < 0:
                    return "Down", key
                return "Flat", key
    return None, None


def _verify_mode_validator_checks(action, levels, atr_14, min_risk_reward=None):
    """Report the outcome of EVERY Trade_Validator check independently (R7.4).

    The Trade_Validator short-circuits on the first failure, but VERIFY mode must
    state pass/fail for each check on the user-proposed trade. This evaluates the
    four checks independently so each receives an explicit outcome.

    ``min_risk_reward`` is the profile-aware Risk_Reward_Ratio floor (1.3 intraday,
    2.0 otherwise). It defaults to ``MIN_RISK_REWARD`` (2.0) for back-compat so the
    reported risk-reward outcome matches the SAME floor the FIND path and the Rust
    validator enforce — a valid intraday bracket (RR 1.3-2.0) must not be
    misreported as a "risk-reward: FAIL" in VERIFY.
    """
    min_rr = (
        min_risk_reward
        if isinstance(min_risk_reward, (int, float))
        and not isinstance(min_risk_reward, bool)
        and min_risk_reward > 0.0
        else MIN_RISK_REWARD
    )
    act = _normalize_action(action)
    if act not in ("BUY", "SELL"):
        return [{"check": "direction", "outcome": "n/a — HOLD/abstain bypasses level checks"}]

    checks = []
    have_levels = (
        isinstance(levels, dict)
        and all(_is_finite_num(levels.get(k)) for k in ("entry", "stop_loss", "take_profit"))
    )
    checks.append({
        "check": "execution-levels-present",
        "outcome": "pass" if have_levels else "fail",
    })
    if not have_levels:
        for c in ("direction-consistency", "stop-distance-vs-atr", "risk-reward"):
            checks.append({"check": c, "outcome": "not-evaluable — missing levels"})
        return checks

    entry = float(levels["entry"])
    sl = float(levels["stop_loss"])
    tp = float(levels["take_profit"])

    if act == "BUY":
        dir_ok = sl < entry < tp
    else:
        dir_ok = tp < entry < sl
    checks.append({"check": "direction-consistency", "outcome": "pass" if dir_ok else "fail"})

    risk = abs(entry - sl)
    if atr_14 is not None and _is_finite_num(atr_14) and atr_14 > 0:
        stop_ok = risk >= 1.5 * atr_14
        checks.append({
            "check": "stop-distance-vs-atr",
            "outcome": "pass" if stop_ok else "fail",
            "detail": f"stop_distance={risk:.4f}, 1.5xATR={1.5 * atr_14:.4f}",
        })
    else:
        checks.append({"check": "stop-distance-vs-atr", "outcome": "not-evaluable — ATR unavailable"})

    if risk > 0:
        rr = abs(tp - entry) / risk
        checks.append({
            "check": "risk-reward",
            "outcome": "pass" if rr >= min_rr else "fail",
            "detail": f"RR={rr:.4f}, min={min_rr:.4f}",
        })
    else:
        checks.append({"check": "risk-reward", "outcome": "not-evaluable — zero risk"})

    return checks


def _regime_entry(results) -> dict:
    """Build the defensibility regime entry from the most recent get_market_regime
    result already present in message history (R7.1-R7.3).

    ``results`` is the ``_latest_tool_results`` map, so ``results['get_market_regime']``
    is the most-recent successfully-parsed, non-error regime result (a usable
    Regime_Label or an Unavailable_Marker). This function:

      * copies the Trend_State, Volatility_State, Favorability, and the named
        Regime_Measures VERBATIM from that result — it never infers or substitutes
        a value not present in the tool output (R7.2);
      * records the entry as unavailable, with NO fabricated trend/volatility/
        favorability, when no usable Regime_Label is present — none in history, or
        only an error / Unavailable_Marker result (R7.3).

    It is a pure read of tool output and never touches the committed decision
    (R12.5, R12.6); the regime is a filter/defensibility surface, not a gate.
    """
    regime = results.get("get_market_regime")

    # No regime result at all, a non-dict result, or an explicit Unavailable_Marker
    # → unavailable. We carry the marker's own reason when present, but NEVER
    # populate trend/volatility/favorability or measures with substitute values.
    if not isinstance(regime, dict):
        return {"available": False, "reason": "no get_market_regime result present in message history"}
    if regime.get("unavailable") is True:
        return {"available": False, "reason": regime.get("reason") or "regime unavailable"}

    trend_state = regime.get("trend_state")
    volatility_state = regime.get("volatility_state")
    favorability = regime.get("favorability")

    # A usable Regime_Label must carry all three categorical states drawn from
    # their fixed enums; anything missing means we have no usable label, and we
    # must not fabricate one (R7.3).
    if (
        trend_state not in REGIME_TREND_STATES
        or volatility_state not in REGIME_VOLATILITY_STATES
        or favorability not in REGIME_FAVORABILITY
    ):
        return {"available": False, "reason": "no usable get_market_regime label present in message history"}

    # Copy the named Regime_Measures verbatim (each is already a finite number or
    # null per the tool contract); never infer a measure that was not reported.
    src_measures = regime.get("measures")
    measures = {}
    if isinstance(src_measures, dict):
        for field in _REGIME_MEASURE_FIELDS:
            measures[field] = src_measures.get(field)

    entry = {
        "available": True,
        "trend_state": trend_state,
        "volatility_state": volatility_state,
        "favorability": favorability,
        "measures": measures,
    }
    # Carry symbol/timeframe/candles_used context verbatim when present.
    for k in ("symbol", "timeframe", "candles_used"):
        if k in regime:
            entry[k] = regime[k]
    return entry


def _relative_strength_entry(results) -> dict:
    """Build the defensibility relative-strength entry from the most recent
    get_relative_strength result already present in message history (R8.1-R8.3).

    ``results`` is the ``_latest_tool_results`` map, so
    ``results['get_relative_strength']`` is the most-recent successfully-parsed,
    non-error relative-strength result (a usable Relative_Strength_Label or an
    Unavailable_Marker). This function:

      * copies the Index_Direction, Relative_Strength_State, Alignment, the named
        Relative_Strength_Measures, and the Benchmark_Index VERBATIM from that
        result — it never infers or substitutes a value not present in the tool
        output (R8.2);
      * records the entry as unavailable, with NO fabricated index_direction/
        relative_strength_state/alignment, when no usable Relative_Strength_Label
        is present — none in history, or only an error / Unavailable_Marker
        result (R8.3).

    It is a pure read of tool output and never touches the committed decision
    (R13.4, R13.5); relative strength is a filter/defensibility surface, not a
    gate.
    """
    rs = results.get("get_relative_strength")

    # No relative-strength result at all, a non-dict result, or an explicit
    # Unavailable_Marker → unavailable. We carry the marker's own reason when
    # present, but NEVER populate index_direction/relative_strength_state/
    # alignment or measures with substitute values.
    if not isinstance(rs, dict):
        return {"available": False, "reason": "no get_relative_strength result present in message history"}
    if rs.get("unavailable") is True:
        return {"available": False, "reason": rs.get("reason") or "relative strength unavailable"}

    index_direction = rs.get("index_direction")
    relative_strength_state = rs.get("relative_strength_state")
    alignment = rs.get("alignment")

    # A usable Relative_Strength_Label must carry all three categorical states
    # drawn from their fixed enums plus a benchmark string; anything missing
    # means we have no usable label, and we must not fabricate one (R8.3).
    if (
        index_direction not in INDEX_DIRECTIONS
        or relative_strength_state not in RELATIVE_STRENGTH_STATES
        or alignment not in ALIGNMENT_VALUES
        or not isinstance(rs.get("benchmark"), str)
    ):
        return {"available": False, "reason": "no usable get_relative_strength label present in message history"}

    # Copy the named Relative_Strength_Measures verbatim (each is already a finite
    # number or null per the tool contract); never infer a measure not reported.
    src_measures = rs.get("measures")
    measures = {}
    if isinstance(src_measures, dict):
        for field in _RS_MEASURE_FIELDS:
            measures[field] = src_measures.get(field)

    entry = {
        "available": True,
        "index_direction": index_direction,
        "relative_strength_state": relative_strength_state,
        "alignment": alignment,
        "measures": measures,
        "benchmark": rs["benchmark"],
    }
    # Carry symbol/timeframe/aligned_candles context verbatim when present.
    for k in ("symbol", "timeframe", "aligned_candles"):
        if k in rs:
            entry[k] = rs[k]
    return entry


def _session_entry(results) -> dict:
    """Build the defensibility session entry from the most recent
    get_session_context result already present in message history (R8.1-R8.3).

    ``results`` is the ``_latest_tool_results`` map, so
    ``results['get_session_context']`` is the most-recent successfully-parsed,
    non-error session result (a usable Session_Label or an Unavailable_Marker).
    This function:

      * copies the Session_Phase, minutes-since-open, minutes-until-close, the
        Expiry_Context, and the Time_Favorability VERBATIM from that result — it
        never infers or substitutes a value not present in the tool output
        (R8.2);
      * records the entry as unavailable, with NO fabricated session_phase/
        time_favorability/expiry_context, when no usable Session_Label is present
        — none in history, or only an error / Unavailable_Marker result (R8.3).

    It is a pure read of tool output and never touches the committed decision's
    action or execution levels (R13.4, R13.5); session awareness is a
    filter/defensibility surface, not a gate.
    """
    sess = results.get("get_session_context")

    # No session result at all, a non-dict result, or an explicit
    # Unavailable_Marker → unavailable. We carry the marker's own reason when
    # present, but NEVER populate session_phase/time_favorability/expiry_context
    # with substitute values.
    if not isinstance(sess, dict):
        return {"available": False, "reason": "no get_session_context result present in message history"}
    if sess.get("unavailable") is True:
        return {"available": False, "reason": sess.get("reason") or "session context unavailable"}

    session_phase = sess.get("session_phase")
    time_favorability = sess.get("time_favorability")
    expiry_context = sess.get("expiry_context")

    # A usable Session_Label must carry a session_phase and a time_favorability
    # drawn from their fixed enums, plus an expiry_context object carrying a
    # boolean is_expiry_day; anything missing means we have no usable label, and
    # we must not fabricate one (R8.3).
    if (
        session_phase not in SESSION_PHASES
        or time_favorability not in TIME_FAVORABILITY
        or not isinstance(expiry_context, dict)
        or not isinstance(expiry_context.get("is_expiry_day"), bool)
    ):
        return {"available": False, "reason": "no usable get_session_context label present in message history"}

    # Copy the five session fields verbatim (minutes are already a finite number
    # or null per the tool contract); never infer a value not reported.
    entry = {
        "available": True,
        "session_phase": session_phase,
        "minutes_since_open": sess.get("minutes_since_open"),
        "minutes_until_close": sess.get("minutes_until_close"),
        "expiry_context": {
            "is_expiry_day": expiry_context.get("is_expiry_day"),
            "days_until_expiry": expiry_context.get("days_until_expiry"),
        },
        "time_favorability": time_favorability,
    }
    # Carry symbol/timeframe context verbatim when present.
    for k in ("symbol", "timeframe"):
        if k in sess:
            entry[k] = sess[k]
    return entry


def _options_entry(results) -> dict:
    """Build the defensibility options entry from the most recent
    get_options_analytics result already present in message history (R6.1-R6.3).

    ``results`` is the ``_latest_tool_results`` map, so
    ``results['get_options_analytics']`` is the most-recent successfully-parsed,
    non-error options result (a usable Options_Bias_Label or an
    Unavailable_Marker). This function:

      * copies the PCR (pcr_oi/pcr_volume), max_pain, oi_buildup, oi_walls,
        iv_skew, futures_basis, the Options_Bias_State, the Alignment, and the
        Chain_Context VERBATIM from that result — it never infers or substitutes
        a value not present in the tool output (R6.2);
      * records the entry as unavailable, with NO fabricated options_bias_state/
        alignment/chain_context, when no usable Options_Bias_Label is present —
        none in history, or only an error / Unavailable_Marker result (R6.3).

    It is a pure read of tool output and never touches the committed decision's
    action or execution levels (R10.3); options context is a filter /
    defensibility surface, not a gate.
    """
    opts = results.get("get_options_analytics")

    # No options result at all, a non-dict result, or an explicit
    # Unavailable_Marker → unavailable. We carry the marker's own reason when
    # present, but NEVER populate options_bias_state/alignment/chain_context or
    # the analytics fields with substitute values.
    if not isinstance(opts, dict):
        return {"available": False, "reason": "no get_options_analytics result present in message history"}
    if opts.get("unavailable") is True:
        return {"available": False, "reason": opts.get("reason") or "options context unavailable"}

    options_bias_state = opts.get("options_bias_state")
    alignment = opts.get("alignment")
    chain_context = opts.get("chain_context")

    # A usable Options_Bias_Label must carry an options_bias_state, an alignment,
    # and a chain_context drawn from their fixed enums; anything missing means we
    # have no usable label, and we must not fabricate one (R6.3).
    if (
        options_bias_state not in OPTIONS_BIAS_STATES
        or alignment not in ALIGNMENT_VALUES
        or chain_context not in OPTIONS_CHAIN_CONTEXTS
    ):
        return {"available": False, "reason": "no usable get_options_analytics label present in message history"}

    # Copy the named analytics fields verbatim (each is already a finite number,
    # null, or a structured object per the tool contract); never infer a value
    # not reported (R6.2).
    entry = {
        "available": True,
        "pcr_oi": opts.get("pcr_oi"),
        "pcr_volume": opts.get("pcr_volume"),
        "max_pain": opts.get("max_pain"),
        "oi_buildup": opts.get("oi_buildup"),
        "oi_walls": opts.get("oi_walls"),
        "iv_skew": opts.get("iv_skew"),
        "futures_basis": opts.get("futures_basis"),
        "options_bias_state": options_bias_state,
        "alignment": alignment,
        "chain_context": chain_context,
    }
    # Carry symbol/underlying/expiry/spot context verbatim when present.
    for k in ("symbol", "underlying", "expiry", "spot"):
        if k in opts:
            entry[k] = opts[k]
    return entry


def _event_entry(results) -> dict:
    """Build the defensibility event entry from the most recent get_event_risk
    result already present in message history (R6.1-R6.5, R8.1-R8.4).

    ``results`` is the ``_latest_tool_results`` map, so
    ``results['get_event_risk']`` is the most-recent successfully-parsed,
    non-error Event_Assessment (a usable Event_Risk_State + Event_Recommendation
    or an Unavailable_Marker). This function:

      * copies the Event_Risk_State (``event_risk``), the ``days_until_event``,
        the reference ``event_date``, and the ``event_recommendation`` VERBATIM
        from that result — it never infers or substitutes a value not present in
        the tool output (R6.2, R8.2);
      * records the entry as unavailable, with NO fabricated event_risk /
        event_recommendation / event_date, when no usable Event_Assessment is
        present — none in history, or only an error / Unavailable_Marker result
        (R6.3, R8.3, R12.4).

    It is a pure read of tool output and never touches the committed decision's
    action or execution levels (R6.4, R12.3-R12.5); event awareness is a
    filter / defensibility surface, not a gate.
    """
    evt = results.get("get_event_risk")

    # No event result at all, a non-dict result, or an explicit
    # Unavailable_Marker → unavailable. We carry the marker's own reason when
    # present, but NEVER populate event_risk/event_recommendation/event_date/
    # days_until_event with substitute values (AD-3, R8.3).
    if not isinstance(evt, dict):
        return {"available": False, "reason": "no get_event_risk result present in message history"}
    if evt.get("unavailable") is True:
        return {"available": False, "reason": evt.get("reason") or "event risk unavailable"}

    event_risk = evt.get("event_risk")
    event_recommendation = evt.get("event_recommendation")

    # A usable Event_Assessment must carry an event_risk and an
    # event_recommendation drawn from their fixed enums, plus an event_date
    # string identifying the reference Scheduled_Event; anything missing means we
    # have no usable assessment, and we must not fabricate one (R8.3, R12.4).
    if (
        event_risk not in EVENT_RISK_STATES
        or event_recommendation not in EVENT_RECOMMENDATIONS
        or not isinstance(evt.get("event_date"), str)
    ):
        return {"available": False, "reason": "no usable get_event_risk assessment present in message history"}

    # Copy the four assessment fields verbatim (days_until_event is already a
    # finite number or null per the tool contract); never infer a value not
    # reported (R6.2, R8.2).
    entry = {
        "available": True,
        "event_risk": event_risk,
        "days_until_event": evt.get("days_until_event"),
        "event_date": evt.get("event_date"),
        "event_recommendation": event_recommendation,
    }
    # Carry symbol/holding_horizon context verbatim when present.
    for k in ("symbol", "holding_horizon"):
        if k in evt:
            entry[k] = evt[k]
    return entry


def _forecast_entry(results) -> dict:
    """Build the defensibility forecast entry from the most recent get_forecast
    result already present in message history (R9.1-R9.3).

    ``results`` is the ``_latest_tool_results`` map, so ``results['get_forecast']``
    is the most-recent successfully-parsed, non-error forecast result (a usable
    Forecast_Label or an Unavailable_Marker). This function:

      * copies the Projected_Direction, Up_Probability, Expected_Move_ATR,
        Forecast_Confidence, Forecast_Alignment, and the named Forecast_Measures
        VERBATIM from that result — it never infers or substitutes a value not
        present in the tool output (R9.2);
      * records the entry as unavailable, with NO fabricated projected_direction/
        up_probability/expected_move_atr/forecast_confidence/forecast_alignment,
        when no usable Forecast_Label is present — none in history, or only an
        error / Unavailable_Marker result (R9.3).

    It is a pure read of tool output and never touches the committed decision
    (R15.4, R15.5); the forecast is a predictive cross-check / defensibility
    surface, not a gate.
    """
    fc = results.get("get_forecast")

    # No forecast result at all, a non-dict result, or an explicit
    # Unavailable_Marker → unavailable. We carry the marker's own reason when
    # present, but NEVER populate projected_direction/up_probability/
    # expected_move_atr/forecast_confidence/forecast_alignment or measures with
    # substitute values.
    if not isinstance(fc, dict):
        return {"available": False, "reason": "no get_forecast result present in message history"}
    if fc.get("unavailable") is True:
        return {"available": False, "reason": fc.get("reason") or "forecast unavailable"}

    projected_direction = fc.get("projected_direction")
    up_probability = fc.get("up_probability")
    expected_move_atr = fc.get("expected_move_atr")
    forecast_confidence = fc.get("forecast_confidence")
    forecast_alignment = fc.get("forecast_alignment")

    # A usable Forecast_Label must carry a projected_direction and a
    # forecast_alignment drawn from their fixed enums, plus finite numeric
    # up_probability and forecast_confidence in [0.0, 1.0]; anything missing
    # means we have no usable label, and we must not fabricate one (R9.3).
    if (
        projected_direction not in FORECAST_DIRECTIONS
        or forecast_alignment not in ALIGNMENT_VALUES
        or not _is_finite_num(up_probability)
        or not _is_finite_num(forecast_confidence)
    ):
        return {"available": False, "reason": "no usable get_forecast label present in message history"}

    # Copy the named Forecast_Measures verbatim (each is already a finite number
    # or null per the tool contract); never infer a measure not reported.
    src_measures = fc.get("measures")
    measures = {}
    if isinstance(src_measures, dict):
        for field in _FORECAST_MEASURE_FIELDS:
            measures[field] = src_measures.get(field)

    entry = {
        "available": True,
        "projected_direction": projected_direction,
        "up_probability": up_probability,
        # expected_move_atr is finite-number-or-null per the contract; copy it
        # verbatim (including null) without substituting a value.
        "expected_move_atr": expected_move_atr,
        "forecast_confidence": forecast_confidence,
        "forecast_alignment": forecast_alignment,
        "measures": measures,
    }
    # Carry symbol/timeframe/candles_used context verbatim when present.
    for k in ("symbol", "timeframe", "candles_used"):
        if k in fc:
            entry[k] = fc[k]
    return entry


def _prediction_entry(results) -> dict:
    """Build the SECONDARY prediction entry from the most recent get_prediction
    result already in message history (Bug 6, R2.17 / R3.14).

    ``results['get_prediction']`` is the most-recent successfully-parsed, non-error
    prediction result. The naive OLS Predictive_Engine reports a
    ``projected_direction`` in {Up, Down, Flat}, a numeric ``projected_value``, and
    a numeric ``confidence``. This pure read:

      * copies ``projected_direction`` / ``projected_value`` / ``confidence``
        VERBATIM into a normalized entry (``direction`` lower-cased to
        up/down/flat so the Best_Current_Read fallback can read it uniformly), and
      * records the entry as unavailable — with NO fabricated direction — when no
        usable prediction is present (none in history, a non-dict, or an explicit
        Unavailable_Marker).

    It NEVER touches the committed decision and is NOT a tier signal (it is not
    consumed by ``opportunity.evaluate_tier``); it exists ONLY as a graceful,
    secondary directional fallback for the interim Best_Current_Read when the
    PRIMARY forecast is unavailable. When both forecast and prediction are
    unavailable the read stays neutral (no fabricated direction).
    """
    pr = results.get("get_prediction")
    if not isinstance(pr, dict):
        return {"available": False, "reason": "no get_prediction result present in message history"}
    if pr.get("unavailable") is True:
        return {"available": False, "reason": pr.get("reason") or "prediction unavailable"}

    direction = pr.get("projected_direction")
    projected_value = pr.get("projected_value")
    confidence = pr.get("confidence")
    if (
        not isinstance(direction, str)
        or direction not in ("Up", "Down", "Flat")  # Predictive_Engine projection enum
        or not _is_finite_num(projected_value)
        or not _is_finite_num(confidence)
    ):
        return {"available": False, "reason": "no usable get_prediction projection present in message history"}

    entry = {
        "available": True,
        "projected_direction": direction,
        "direction": direction.strip().lower(),  # up | down | flat
        "projected_value": projected_value,
        "confidence": confidence,
    }
    for k in ("symbol", "timeframe"):
        if k in pr:
            entry[k] = pr[k]
    return entry


def _management_entry(decision, action, levels, results, atr_14) -> Optional[dict]:
    """Build the defensibility management entry for a committed directional trade
    (R9.1-R9.3).

    Only a committed BUY/SELL with usable execution levels carries a management
    entry; a HOLD or a decision with no usable entry/stop levels yields ``None``
    (no ``management`` key in the record), so the trade-management verification
    step maps it to ``not-evaluable`` (R10.4). The committed plan is sourced from
    the declared decision:

      * the declare_trade ``management_plan`` when present — a JSON string is
        reconstructed via ``trade_manager.plan_from_json``; a dict is coerced via
        the SAME ``_coerce_management_plan`` merge ``declare_trade`` used to
        validate it (so the entry cites the exact committed plan) — yielding a
        managed plan; otherwise
      * the degenerate single-target plan built from the committed bracket via
        ``trade_manager.single_target_plan`` (R9.3) — recorded as single-target
        WITHOUT fabricating scale-out legs.

    Where candles are available in scope (a ``get_candles`` tool result in
    history), the plan is scored by ``trade_manager.simulate_plan`` and the
    resulting Exit_Breakdown + Realized_R are cited VERBATIM (no fabrication,
    R9.2); otherwise only the plan (legs / breakeven / trailing / style) is
    recorded. Mirrors the shape ``backtest._management_defensibility_entry``
    writes and the journal management-style derivation reads, and — like the
    sibling regime / relative-strength / forecast entries — never touches the
    committed decision's action or execution levels.
    """
    # A HOLD (or any non-directional action) is never managed.
    if action not in ("BUY", "SELL"):
        return None
    if not isinstance(levels, dict):
        return None
    entry_px = levels.get("entry")
    stop_px = levels.get("stop_loss")
    take_px = levels.get("take_profit")
    # The committed bracket needs at least a finite entry and initial stop to
    # express any plan; without them there are no usable levels to manage.
    if not (_is_finite_num(entry_px) and _is_finite_num(stop_px)):
        return None

    # ── Source the committed plan: declared management_plan, else single-target ─
    raw_plan = (decision or {}).get("management_plan")
    plan = None
    if isinstance(raw_plan, str):
        plan = trade_manager.plan_from_json(raw_plan)
    elif isinstance(raw_plan, dict):
        plan = _coerce_management_plan(raw_plan, action, entry_px, stop_px, atr_14)
    if plan is None:
        # No usable declared plan -> the degenerate single-target plan. This needs
        # a finite take-profit; without one there is nothing to record.
        if not _is_finite_num(take_px):
            return None
        plan = trade_manager.single_target_plan(entry_px, stop_px, take_px)

    # ── Simulate ONLY where candles are available (R9.1) ─────────────────────
    # The simulator is the single source of truth for the exit math; we feed it
    # the candle series the agent already fetched (if any) and cite its output
    # verbatim. With no candles in scope we record the plan only — never a
    # fabricated exit (R9.2).
    candles = results.get("get_candles")
    sim_result = None
    if isinstance(candles, list) and candles:
        sim_result = trade_manager.simulate_plan(
            plan, candles, trade_manager.resolve_trade_manager_config()
        )

    legs = [{"target": leg.target, "fraction": leg.fraction} for leg in (plan.legs or ())]
    breakeven = None
    if plan.breakeven is not None:
        breakeven = {"price": plan.breakeven.price, "r_multiple": plan.breakeven.r_multiple}
    trailing = None
    if plan.trailing is not None:
        trailing = {"atr_multiple": plan.trailing.atr_multiple, "r_increment": plan.trailing.r_increment}

    entry = {
        "available": True,
        # The single fixed-enumeration management-style value (R11.2); the journal
        # namespaces it as ``tm:<style>`` at its fixed tag position. A single-target
        # plan collapses to ``single`` so it is recorded as single-target (R9.3).
        "style": trade_manager.management_style_tag(plan),
        "action": plan.action,
        "entry": plan.entry,
        "initial_stop": plan.initial_stop,
        "legs": legs,
        "breakeven": breakeven,
        "trailing": trailing,
        "atr_14": plan.atr_14,
    }
    # Where the plan was simulated, cite the real Exit_Breakdown + Realized_R
    # verbatim from the Trade_Manager output (no fabrication, R9.2).
    if sim_result is not None:
        entry["status"] = sim_result.status
        entry["realized_r"] = sim_result.realized_r
        entry["residual_fraction"] = sim_result.residual_fraction
        entry["exit_breakdown"] = [
            {
                "index": f.index,
                "price": f.price,
                "fraction": f.fraction,
                "leg_r": f.leg_r,
                "timestamp_ms": f.timestamp_ms,
                "kind": f.kind,
            }
            for f in sim_result.fills
        ]
    return entry


def _debate_entry(decision, mode, action):
    """Build the debate sub-entry for a DEBATE-mode decision (R7.1-R7.4), or None.

    Reads ONLY the stored Bull/Bear stances and the Judge verdict that
    ``judge_node`` threads onto the decision under the private ``_debate``
    carrier before finalization (``bull_stance`` / ``bear_stance`` /
    ``consensus`` / ``conviction``). Nothing is invented:

      * A missing/garbled stance is represented as ``{"available": False}`` rather
        than a fabricated stance (R7.2, R12.2); a present stance is mirrored from
        its stored serialized form verbatim.
      * ``conviction_basis`` is a faithful projection of the stored consensus and
        the two stance strengths — it states how they set the conviction and
        invents no new evidence (R7.1, R7.2).
      * ``committed_against_contested`` is included ONLY when a directional
        BUY/SELL was committed against a ``contested`` consensus (R7.4).

    Returns ``None`` for any non-DEBATE run or when no debate data was threaded,
    so ``build_defensibility_record`` adds NO ``debate`` key (R7.3) — mirroring
    the way the ``management`` entry is added only when applicable.
    """
    if mode != DEBATE_MODE:
        return None
    raw = decision.get("_debate") if isinstance(decision, dict) else None
    if not isinstance(raw, dict):
        return None

    # Mirror the stored stances verbatim; a missing stance is marked unavailable
    # rather than fabricated (R7.2, R12.2).
    bull_raw = raw.get("bull_stance")
    bear_raw = raw.get("bear_stance")
    bull_entry = bull_raw if isinstance(bull_raw, dict) else {"available": False}
    bear_entry = bear_raw if isinstance(bear_raw, dict) else {"available": False}

    consensus = raw.get("consensus")
    if consensus not in DEBATE_CONSENSUS_VALUES:
        consensus = "unknown"

    conviction = raw.get("conviction")
    try:
        conviction = int(conviction) if conviction is not None else None
    except (TypeError, ValueError):
        conviction = None

    # Read the strengths back from the stored stances purely for the basis
    # statement (an unavailable stance is reported as such, never invented).
    def _stance_strength(entry):
        if isinstance(entry, dict) and entry.get("available") is not False:
            s = entry.get("strength")
            if isinstance(s, bool):
                return None
            if isinstance(s, (int, float)):
                return int(s)
        return None

    bull_strength = _stance_strength(bull_entry)
    bear_strength = _stance_strength(bear_entry)
    bull_desc = str(bull_strength) if bull_strength is not None else "unavailable"
    bear_desc = str(bear_strength) if bear_strength is not None else "unavailable"

    conviction_basis = (
        f"Consensus '{consensus}' with bull strength {bull_desc} vs bear strength "
        f"{bear_desc} set the Judge conviction to "
        f"{conviction if conviction is not None else 'n/a'}."
    )
    if consensus == "contested":
        conviction_basis += (
            " A contested consensus attenuates conviction toward caution."
        )

    entry = {
        "bull_stance": bull_entry,
        "bear_stance": bear_entry,
        "consensus": consensus,
        "conviction": conviction,
        "conviction_basis": conviction_basis,
    }

    # R7.4: only when a directional BUY/SELL was committed against a contested
    # debate, add an explicit statement that the trade fought a contested verdict.
    if consensus == "contested" and action in ("BUY", "SELL"):
        entry["committed_against_contested"] = (
            f"COMMITTED AGAINST A CONTESTED DEBATE: the Judge committed a directional "
            f"{action} even though the Bull and Bear cases were comparably strong and "
            f"opposed (bull strength {bull_desc} vs bear strength {bear_desc}, "
            f"consensus=contested)."
        )

    return entry


def build_defensibility_record(messages, decision, mode=None, manual_trade=None, profile=None) -> dict:
    """Assemble the trade defensibility record from tool results in history (R7).

    Gathers the evidence behind a committed decision from the Analysis_Tool
    results already present in ``messages`` — multi-timeframe bias
    (get_multi_tf_trend), key support/resistance levels (get_support_resistance),
    the volatility basis for the stop (atr_14 from get_consensus_report), named
    high-confidence chart patterns >0.6 (get_chart_patterns), and a forward
    projection (get_prediction or the consensus OLS/VWEPR slope) — then derives
    the Risk_Reward_Ratio, a predictive-conflict statement, and a
    macro-trend-conflict statement. In VERIFY mode it also reports the outcome of
    every Trade_Validator check for the user-proposed trade (R7.4).

    The record cites only values returned by Analysis_Tools (R5.4); nothing is
    fabricated. It is attached to the committed decision as
    ``decision["defensibility"]``.
    """
    mode = (mode or "FIND").upper()
    results = _latest_tool_results(messages)
    patterns = _collect_high_confidence_patterns(messages)
    action, levels = _resolve_action_and_levels(decision, mode, manual_trade)

    multi_tf = results.get("get_multi_tf_trend")
    multi_tf = multi_tf if isinstance(multi_tf, dict) else None
    sr = results.get("get_support_resistance")
    sr = sr if isinstance(sr, dict) else None
    atr = _latest_atr(results)

    # ── Volatility basis for the stop (R7.1) ─────────────────────────────────
    if atr is not None:
        vol_basis = (
            f"Stop sized against ATR(14)={atr:.4f}; risk-manager floor is "
            f"1.5x ATR = {1.5 * atr:.4f}."
        )
        if isinstance(levels, dict) and _is_finite_num(levels.get("entry")) and _is_finite_num(levels.get("stop_loss")):
            stop_dist = abs(levels["entry"] - levels["stop_loss"])
            meets = stop_dist >= 1.5 * atr
            vol_basis += f" Actual stop distance = {stop_dist:.4f} ({'>=' if meets else '<'} 1.5x ATR)."
    else:
        vol_basis = "ATR(14) unavailable from consensus; volatility basis for the stop could not be confirmed."

    # ── Risk_Reward_Ratio (R7.2) ─────────────────────────────────────────────
    risk_reward = None
    if isinstance(levels, dict) and all(_is_finite_num(levels.get(k)) for k in ("entry", "stop_loss", "take_profit")):
        risk = abs(levels["entry"] - levels["stop_loss"])
        if risk > 0:
            risk_reward = round(abs(levels["take_profit"] - levels["entry"]) / risk, 4)

    # ── Directional bias from the action ─────────────────────────────────────
    agent_dir = {"BUY": "Up", "SELL": "Down", "HOLD": "Flat"}.get(action)

    # ── Predictive-conflict statement (R12.3) ────────────────────────────────
    proj_dir, proj_src = _predictive_direction(results)
    if proj_dir and agent_dir and agent_dir != "Flat":
        if {proj_dir, agent_dir} == {"Up", "Down"}:
            predictive_conflict = (
                f"CONFLICT: predictive projection is {proj_dir} (source: {proj_src}) "
                f"but the trade bias is {agent_dir}."
            )
        else:
            predictive_conflict = (
                f"No predictive conflict: projection {proj_dir} aligns with trade bias "
                f"{agent_dir} (source: {proj_src})."
            )
    elif proj_dir:
        predictive_conflict = (
            f"Predictive projection is {proj_dir} (source: {proj_src}); "
            f"no directional trade bias to compare."
        )
    else:
        predictive_conflict = "Predictive projection unavailable; no conflict assessment possible."

    # ── Macro-trend-conflict statement (R13.3) ───────────────────────────────
    bias_1d_raw = _extract_1d_bias(multi_tf)
    bias_1d = _bias_sign(bias_1d_raw)
    if bias_1d in ("Bullish", "Bearish") and agent_dir in ("Up", "Down"):
        opposes = (agent_dir == "Up" and bias_1d == "Bearish") or (agent_dir == "Down" and bias_1d == "Bullish")
        if opposes:
            macro_conflict = f"MACRO CONFLICT: {action} opposes the 1D trend bias ({bias_1d_raw})."
        else:
            macro_conflict = f"Aligned with the 1D trend bias ({bias_1d_raw})."
    elif bias_1d_raw:
        macro_conflict = f"1D trend bias is {bias_1d_raw}; trade direction is {action or 'n/a'}."
    else:
        macro_conflict = "1D trend bias unavailable; macro-trend alignment could not be assessed."

    news = results.get("get_news_context")
    news_sentiment = news.get("sentiment_summary") if isinstance(news, dict) else None

    # ── Volume Profile auction evidence (Phase 1 → cited here) ───────────────
    vp = results.get("get_volume_profile")
    vp = vp if isinstance(vp, dict) else None
    volume_profile = None
    if vp:
        volume_profile = {
            "poc": vp.get("poc"),
            "vah": vp.get("vah"),
            "val": vp.get("val"),
            "price_vs_value_area": vp.get("price_vs_value_area"),
            "hvn_levels": vp.get("hvn_levels"),
            "lvn_levels": vp.get("lvn_levels"),
        }

    # ── Realized track record used to calibrate conviction (Phase 2) ─────────
    perf = results.get("get_trade_performance")
    track_record = None
    if isinstance(perf, dict):
        track_record = {
            "overall": perf.get("overall"),
            "low_sample": perf.get("low_sample"),
        }

    named = ", ".join(
        f"{p['pattern_type']} (conf {p['confidence']:.2f})" for p in patterns
    ) or "none >0.6"

    # ── Regime entry (R4.4, R7.1-R7.4) ───────────────────────────────────────
    # Mirror the most-recent get_market_regime result verbatim (or record it as
    # unavailable). The regime is a defensibility surface only: it NEVER modifies,
    # overrides, or blocks the committed decision's action or execution levels
    # (R12.5, R12.6) — we merely add an explicit opposition statement when an
    # unfavorable regime is committed against with a directional (BUY/SELL) trade
    # (R7.4).
    regime = _regime_entry(results)
    if (
        regime.get("available")
        and regime.get("favorability") == "unfavorable"
        and action in ("BUY", "SELL")
    ):
        regime["trade_opposes_regime"] = (
            f"REGIME CONFLICT: the committed {action} trade opposes the regime "
            f"assessment (favorability=unfavorable, trend_state="
            f"{regime.get('trend_state')}, volatility_state="
            f"{regime.get('volatility_state')})."
        )

    # ── Relative-strength entry (R8.1-R8.4, R13.4-R13.5) ─────────────────────
    # Mirror the most-recent get_relative_strength result verbatim (or record it
    # as unavailable). Relative strength is a defensibility surface only: it
    # NEVER modifies, overrides, or blocks the committed decision's action or
    # execution levels (R13.4, R13.5) — we merely add an explicit opposition
    # statement when a misaligned context is committed against with a directional
    # (BUY/SELL) trade (R8.4).
    relative_strength = _relative_strength_entry(results)
    if (
        relative_strength.get("available")
        and relative_strength.get("alignment") == "misaligned"
        and action in ("BUY", "SELL")
    ):
        relative_strength["trade_opposes_relative_strength"] = (
            f"RELATIVE STRENGTH CONFLICT: the committed {action} trade fights the "
            f"index or trades a laggard against its benchmark "
            f"({relative_strength.get('benchmark')}) — index_direction="
            f"{relative_strength.get('index_direction')}, relative_strength_state="
            f"{relative_strength.get('relative_strength_state')}, alignment=misaligned."
        )

    # ── Forecast entry (R9.1-R9.4, R15.4-R15.5) ──────────────────────────────
    # Mirror the most-recent get_forecast result verbatim (or record it as
    # unavailable). The forecast is a predictive cross-check / defensibility
    # surface only: it NEVER modifies, overrides, or blocks the committed
    # decision's action or execution levels (entry, stop-loss, take-profit)
    # (R15.4, R15.5) — we merely add an explicit opposition statement when a
    # misaligned forecast is committed against with a directional (BUY/SELL)
    # trade (R9.4).
    forecast = _forecast_entry(results)
    if (
        forecast.get("available")
        and forecast.get("forecast_alignment") == "misaligned"
        and action in ("BUY", "SELL")
    ):
        forecast["trade_opposes_forecast"] = (
            f"FORECAST CONFLICT: the committed {action} trade opposes the forecast "
            f"(projected_direction={forecast.get('projected_direction')}, "
            f"up_probability={forecast.get('up_probability')}, "
            f"forecast_alignment=misaligned)."
        )

    # ── Session entry (R8.1-R8.4, R13.4-R13.5) ───────────────────────────────
    # Mirror the most-recent get_session_context result verbatim (or record it as
    # unavailable). Session awareness is a filter / defensibility surface only: it
    # NEVER modifies, overrides, or blocks the committed decision's action or
    # execution levels (R13.4, R13.5) — we merely add an explicit statement that
    # the committed trade is taken in an unfavorable time window when the
    # Time_Favorability is `unfavorable` and a directional (BUY/SELL) trade is
    # committed (R8.4).
    session = _session_entry(results)
    if (
        session.get("available")
        and session.get("time_favorability") == "unfavorable"
        and action in ("BUY", "SELL")
    ):
        session["trade_in_unfavorable_window"] = (
            f"UNFAVORABLE TIME WINDOW: the committed {action} trade is taken in an "
            f"unfavorable time window (session_phase={session.get('session_phase')}, "
            f"is_expiry_day={session.get('expiry_context', {}).get('is_expiry_day')}, "
            f"time_favorability=unfavorable)."
        )

    # ── Options entry (R6.1-R6.4, R10.3) ─────────────────────────────────────
    # Mirror the most-recent get_options_analytics result verbatim (or record it
    # as unavailable). Options context is a filter / defensibility surface only:
    # it NEVER modifies, overrides, or blocks the committed decision's action or
    # execution levels (entry, stop-loss, take-profit) (R10.3) — we merely add an
    # explicit opposition statement when a misaligned options bias is committed
    # against with a directional (BUY/SELL) trade (R6.4).
    options = _options_entry(results)
    if (
        options.get("available")
        and options.get("alignment") == "misaligned"
        and action in ("BUY", "SELL")
    ):
        options["trade_opposes_options"] = (
            f"OPTIONS CONFLICT: the committed {action} trade fights the prevailing "
            f"options positioning (options_bias_state={options.get('options_bias_state')}, "
            f"alignment=misaligned, chain_context={options.get('chain_context')})."
        )

    # ── Event entry (R6.1-R6.5, R8.1-R8.4, R12.3-R12.5) ──────────────────────
    # Mirror the most-recent get_event_risk Event_Assessment verbatim (or record
    # it as unavailable). Scheduled-event risk is a filter / defensibility surface
    # only: it NEVER modifies, overrides, or blocks the committed decision's
    # action or execution levels (entry, stop-loss, take-profit) (R12.3-R12.5) —
    # we merely add an explicit statement that the committed directional (BUY/SELL)
    # trade is held THROUGH a scheduled event when event_risk == "through_event"
    # (R6.5, R8.4).
    event = _event_entry(results)
    if (
        event.get("available")
        and event.get("event_risk") == "through_event"
        and action in ("BUY", "SELL")
    ):
        event["trade_held_through_event"] = (
            f"EVENT RISK: the committed {action} trade is held THROUGH a scheduled "
            f"event (event_risk=through_event, event_date={event.get('event_date')}, "
            f"days_until_event={event.get('days_until_event')}, "
            f"event_recommendation={event.get('event_recommendation')})."
        )

    # ── Management entry (R9.1-R9.3) ─────────────────────────────────────────
    # For a committed directional (BUY/SELL) trade with usable levels, cite the
    # committed Management_Plan — the declared multi-leg plan, or the degenerate
    # single-target plan built from the bracket — and, where candles are available
    # in scope, the simulated Exit_Breakdown + Realized_R from the Trade_Manager
    # (populated ONLY from the declared plan and simulator output, never
    # fabricated). A HOLD or a decision with no usable levels yields no management
    # entry, so the trade-management verification step maps to not-evaluable.
    management = _management_entry(decision, action, levels, results, atr)

    record = {
        "mode": mode,
        "action": action,
        "multi_tf_bias": multi_tf,
        "trend_1d": bias_1d_raw,
        "support_resistance": sr,
        "volatility_basis": vol_basis,
        "atr_14": atr,
        "levels": levels,
        "risk_reward": risk_reward,
        "patterns": patterns,
        "volume_profile": volume_profile,
        "track_record": track_record,
        "predictive_conflict": predictive_conflict,
        "macro_trend_conflict": macro_conflict,
        "news_sentiment": news_sentiment,
        "regime": regime,
        "relative_strength": relative_strength,
        "forecast": forecast,
        "session": session,
        "options": options,
        "event": event,
        "summary": (
            f"Multi-TF 1D bias: {bias_1d_raw or 'n/a'}. "
            f"RR: {risk_reward if risk_reward is not None else 'n/a'}. "
            f"High-confidence patterns: {named}. "
            f"Regime: {regime.get('favorability') if regime.get('available') else 'unavailable'}. "
            f"Relative strength: "
            f"{relative_strength.get('alignment') if relative_strength.get('available') else 'unavailable'}. "
            f"Forecast: "
            f"{forecast.get('forecast_alignment') if forecast.get('available') else 'unavailable'}. "
            f"Session: "
            f"{session.get('time_favorability') if session.get('available') else 'unavailable'}. "
            f"Options: "
            f"{options.get('alignment') if options.get('available') else 'unavailable'}. "
            f"Event: "
            f"{event.get('event_risk') if event.get('available') else 'unavailable'}. "
            f"{macro_conflict} {predictive_conflict}"
            + (f" {regime['trade_opposes_regime']}" if regime.get("trade_opposes_regime") else "")
            + (
                f" {relative_strength['trade_opposes_relative_strength']}"
                if relative_strength.get("trade_opposes_relative_strength")
                else ""
            )
            + (
                f" {forecast['trade_opposes_forecast']}"
                if forecast.get("trade_opposes_forecast")
                else ""
            )
            + (
                f" {session['trade_in_unfavorable_window']}"
                if session.get("trade_in_unfavorable_window")
                else ""
            )
            + (
                f" {options['trade_opposes_options']}"
                if options.get("trade_opposes_options")
                else ""
            )
            + (
                f" {event['trade_held_through_event']}"
                if event.get("trade_held_through_event")
                else ""
            )
        ),
    }

    # Attach the management entry only when present — an absent key is what the
    # trade-management verification step reads as "not-evaluable" (R10.4); a HOLD
    # or a decision with no usable levels therefore carries no management key.
    if management is not None:
        record["management"] = management

    # Attach the debate entry only for a DEBATE-mode decision that carries the
    # threaded Bull/Bear stances + Judge verdict (multi-agent-debate, R7.1).
    # A non-DEBATE run (or a DEBATE run with no threaded debate data) yields
    # None, so NO ``debate`` key is added (R7.3) — exactly like the management
    # entry above. The debate-consensus stream step reads an absent key as
    # "not-evaluable".
    debate = _debate_entry(decision, mode, action)
    if debate is not None:
        record["debate"] = debate

    # VERIFY mode must report every Trade_Validator check outcome (R7.4). The
    # risk-reward check uses the profile-aware floor (1.3 intraday / 2.0 else) so
    # VERIFY agrees with FIND and the Rust validator instead of misreporting a
    # valid intraday bracket as a risk-reward failure.
    if mode == "VERIFY":
        record["validator_checks"] = _verify_mode_validator_checks(
            action, levels, atr, min_risk_reward=min_risk_reward_for_profile(profile)
        )

    # Mirror the committed Opportunity_Tier into the defensibility record so the
    # trade carries its tier alongside the other evidence entries, and so
    # ``journal.derive_setup_tags`` can read it for the ``tier:`` fingerprint
    # (adaptive-opportunity-engine R9.1). Set by ``_stamp_opportunity_tier`` before
    # this record is built; a decision without a stamped tier omits the key.
    _opp_tier = (decision or {}).get("opportunity_tier")
    if isinstance(_opp_tier, str) and _opp_tier:
        record["opportunity_tier"] = _opp_tier

    return record


def _apply_weight_map_to_conviction(decision: dict, symbol=None) -> None:
    """Opt-in Feature-Attribution Weight_Map consultation (feature-attribution-pruning).

    GUARDED + INERT BY DEFAULT. When ``ATTRIBUTION_WEIGHT_MAP_ENABLED`` is false —
    the default — this returns before touching the ``decision`` in any way, so the
    finalize path is BYTE-FOR-BYTE identical to today and the pass has zero effect
    on the running agent (R6.2, R6.3, R9.4). The only way the branch below runs is
    an explicit opt-in.

    When enabled it runs HERE, inside ``_finalize_decision`` — i.e. strictly AFTER
    the hard risk rules / Trade_Validator have already accepted the trade (a
    declare_trade the validator rejected never reaches finalize: it leaves the
    decision unset and the bounded loop continues — see ``_decision_from_declare``
    / ``_declare_was_rejected``). It then:

      * loads the Weight_Map READ-ONLY from the journal (R9.3) — never writing,
        and degrading to neutral weights on any failure;
      * scales the conviction CONTRIBUTION of each fingerprint dimension present
        in this decision by that dimension's weight (a dimension absent from the
        map defaults to weight ``1.0`` — no change). Because the committed
        conviction is a single aggregate score rather than a stored per-dimension
        breakdown, scaling each equal contribution by its weight and re-aggregating
        is the sample-weighted-neutral mean of the present dimensions' weights;
      * records ``weight_map_applied: true`` with the resolved per-dimension
        weights (and the before/after conviction) in the decision's defensibility
        record so the committed decision stays auditable (R6.5).

    The weight ONLY scales conviction; every derivable weight lies in ``(0.0, 1.0]``
    so it can only attenuate, never amplify. It never touches the action or the
    execution levels, so it can never, of itself, commit, block, override, or relax
    a hard risk rule (R6.4) — the validator already ran and is independent of the
    conviction score. TOTAL: any failure degrades to a no-op and never raises into
    the run.
    """
    # Lazy import so the attribution module is only loaded on the opt-in path.
    try:
        import attribution
        config = attribution.resolve_attribution_config()
    except Exception:
        return

    # The single guard: disabled (default) => skip the entire branch (R6.2/6.3/9.4).
    if not getattr(config, "weight_map_enabled", False):
        return

    try:
        record = decision.get("defensibility")
        if not isinstance(record, dict):
            return

        # READ-ONLY Weight_Map from the journal (R9.3); degrade to neutral on any
        # failure (missing/locked DB, or the I/O helper not yet available).
        try:
            weight_map = attribution.weight_map_from_journal(symbol)
        except Exception:
            weight_map = {}
        if not isinstance(weight_map, dict):
            weight_map = {}

        # The fingerprint dimensions present in THIS committed decision, derived
        # from the same low-cardinality setup tags the journal records.
        tags = journal.derive_setup_tags(decision)
        key = journal.setup_key_from_tags(tags)
        parsed = attribution.parse_setup_key(key)

        # Weight applied per present dimension (absent => 1.0 / no change, R6.1).
        applied = {dim: float(weight_map.get(dim, 1.0)) for dim in parsed}

        # Record the consultation for auditability (R6.5), even when neutral.
        record["weight_map_applied"] = True
        record["weight_map"] = applied

        conviction = decision.get("conviction_score")
        if (
            applied
            and isinstance(conviction, (int, float))
            and not isinstance(conviction, bool)
        ):
            mean_weight = sum(applied.values()) / len(applied)
            scaled = int(round(conviction * mean_weight))
            # conviction_score is an int in [0, 100]; clamp defensively. Weights
            # in (0,1] can only attenuate, so this never raises conviction.
            scaled = 0 if scaled < 0 else (100 if scaled > 100 else scaled)
            record["conviction_before_weight_map"] = conviction
            record["conviction_after_weight_map"] = scaled
            decision["conviction_score"] = scaled
    except Exception as e:
        # Never let an opt-in analytics consultation break a committed decision.
        print(f"[Attribution] WARN: Weight_Map consultation skipped: {e}")


def _max_pattern_confidence(results) -> float:
    """The highest structural pattern confidence in the latest get_chart_patterns
    result, in [0.0, 1.0]; 0.0 when no pattern result is present.

    Tolerates both the bare list contract and a ``{"patterns": [...]}`` wrapper.
    Pure read of tool output; never raises.
    """
    patterns = results.get("get_chart_patterns")
    if isinstance(patterns, dict):
        patterns = patterns.get("patterns")
    best = 0.0
    if isinstance(patterns, list):
        for p in patterns:
            if isinstance(p, dict):
                c = p.get("confidence")
                if _is_finite_num(c):
                    best = max(best, float(c))
    return max(0.0, min(1.0, best))


def _macro_signal_for_tier(results, action) -> dict:
    """The macro (1D trend vs proposed direction) alignment signal for the ladder.

    Reads the 1D bias from the latest get_multi_tf_trend result and compares it to
    the proposed action: a BUY with a bullish (SELL with a bearish) 1D bias is
    ``aligned``; the opposite is ``misaligned``; a HOLD / neutral bias / absent
    trend reads ``neutral``. Shaped like the other evidence signals so
    ``opportunity.evaluate_tier`` consumes it directly.
    """
    bias = _bias_sign(_extract_1d_bias(results.get("get_multi_tf_trend")))
    if bias is None:
        return {"available": False, "alignment": "neutral"}
    act = (action or "").upper()
    if bias == "Neutral" or act not in ("BUY", "SELL"):
        return {"available": True, "alignment": "neutral"}
    if (act == "BUY" and bias == "Bullish") or (act == "SELL" and bias == "Bearish"):
        return {"available": True, "alignment": "aligned"}
    return {"available": True, "alignment": "misaligned"}


def _levels_are_structural(decision, mode, manual_trade) -> bool:
    """True when the resolved execution levels are structurally sourced — the
    validated ``declare_trade`` args (all three of entry/stop_loss/take_profit
    finite) or the user-proposed VERIFY ``manual_trade`` — rather than recovered
    from free-form plan prose (R4.1, R4.4).

    Mirrors the branch ``_resolve_action_and_levels`` takes to decide whether it
    returns the structured triple or falls back to the prose parse, so a
    prose-parsed number is never treated as a defensible price. Pure; never raises.
    """
    if mode == "VERIFY" and manual_trade:
        return True
    d = decision or {}
    return all(_is_finite_num(d.get(k)) for k in ("entry", "stop_loss", "take_profit"))


def _reference_levels(results, watch_args) -> dict:
    """Assemble structured reference price levels for the Best_Current_Read.

    Priority order (R4.1): (1) the nearest support/resistance bracketing the last
    price from ``get_support_resistance``, (2) VWAP from ``get_consensus_report``
    plus the value-area edges (VAH/VAL) from ``get_volume_profile``, (3) the
    registered watch's ``price_level`` / ``invalidation_level``. The first source
    group that yields at least one finite, structurally-sourced price wins; a field
    with no defensible price is omitted (R4.4). Returns ``{}`` when no source is
    defensible. Pure; never raises.
    """
    results = results if isinstance(results, dict) else {}

    consensus = results.get("get_consensus_report")
    consensus = consensus if isinstance(consensus, dict) else {}
    ref_price = consensus.get("current_price")
    if not _is_finite_num(ref_price):
        ref_price = None

    # (1) get_support_resistance — nearest support/resistance around the anchor.
    sr = results.get("get_support_resistance")
    if isinstance(sr, dict) and not sr.get("unavailable"):
        supports = [float(sr[k]) for k in ("s1", "s2", "s3") if _is_finite_num(sr.get(k))]
        resistances = [float(sr[k]) for k in ("r1", "r2", "r3") if _is_finite_num(sr.get(k))]
        pivot = sr.get("pivot")
        anchor = ref_price if ref_price is not None else (
            float(pivot) if _is_finite_num(pivot) else None
        )
        levels: dict = {}
        if supports:
            below = [s for s in supports if anchor is None or s <= anchor]
            levels["support"] = max(below) if below else min(
                supports, key=lambda s: abs(s - (anchor if anchor is not None else s))
            )
        if resistances:
            above = [r for r in resistances if anchor is None or r >= anchor]
            levels["resistance"] = min(above) if above else min(
                resistances, key=lambda r: abs(r - (anchor if anchor is not None else r))
            )
        if levels:
            return levels

    # (2) get_consensus_report VWAP + get_volume_profile value-area edges.
    levels = {}
    if _is_finite_num(consensus.get("vwap")):
        levels["vwap"] = float(consensus["vwap"])
    vp = results.get("get_volume_profile")
    if isinstance(vp, dict):
        if _is_finite_num(vp.get("vah")):
            levels["value_area_high"] = float(vp["vah"])
        if _is_finite_num(vp.get("val")):
            levels["value_area_low"] = float(vp["val"])
    if levels:
        return levels

    # (3) registered watch price_level / invalidation_level.
    wa = watch_args if isinstance(watch_args, dict) else {}
    levels = {}
    if _is_finite_num(wa.get("price_level")):
        levels["watch_price"] = float(wa["price_level"])
    if _is_finite_num(wa.get("invalidation_level")):
        levels["invalidation"] = float(wa["invalidation_level"])
    return levels


def _evidence_for_tier(state: AgentState, decision: dict):
    """Assemble the ``opportunity.evaluate_tier`` evidence dict from the same tool
    results the defensibility record cites (adaptive-opportunity-engine R1).

    Reuses the existing defensibility ``_*_entry`` readers (each already emits the
    ``available`` + favorability/alignment shape the ladder consumes) plus the
    committed decision's own validated levels (the defensible triple) and the
    strongest structural pattern confidence. Returns ``(evidence, action)``. Pure
    read; the ladder never relaxes the Trade_Validator (the levels here have
    ALREADY passed it), it only classifies the setup quality.

    Also carries a structured ``reference_levels`` dict (support/resistance, VWAP,
    value-area, or the registered watch levels) and a ``levels_structural`` flag so
    ``opportunity.best_current_read`` can prefer real, structurally-sourced prices
    over any prose-parsed number (R4.1, R4.4).
    """
    messages = state.get("messages") or []
    results = _latest_tool_results(messages)
    action, levels = _resolve_action_and_levels(
        decision, state.get("mode"), state.get("manual_trade")
    )
    levels = levels or {}
    evidence = {
        "pattern_confidence": _max_pattern_confidence(results),
        "entry": levels.get("entry"),
        "stop": levels.get("stop_loss"),
        "target": levels.get("take_profit"),
        "regime": _regime_entry(results),
        "session": _session_entry(results),
        "relative_strength": _relative_strength_entry(results),
        "forecast": _forecast_entry(results),
        # Bug 6 (R2.17 / R3.14): the SECONDARY prediction is surfaced into the
        # evidence purely as a graceful directional fallback for the interim
        # Best_Current_Read. It is an ADDITIVE key that `opportunity.evaluate_tier`
        # / `_signal_states` do NOT read, so the committed-trade tier + Size_Factor
        # (and thus the Trade_Validator path) stay byte-identical; only
        # `_best_current_read` consults it, and only when the forecast is absent.
        "prediction": _prediction_entry(results),
        "options": _options_entry(results),
        "macro": _macro_signal_for_tier(results, action),
        "reference_levels": _reference_levels(results, _latest_watch_args(messages)),
        "levels_structural": _levels_are_structural(
            decision, state.get("mode"), state.get("manual_trade")
        ),
    }
    return evidence, action


def _best_current_read(evidence, tier_eval) -> dict:
    """Compute the interim Best_Current_Read and surface a directional read from
    the forecast/prediction path whenever the agent waits (Bug 6, R2.17 / R3.14).

    Delegates to the existing ``opportunity.best_current_read`` (which already
    derives a coarse bias from the confluence-signal net — INCLUDING the PRIMARY
    forecast signal — plus the defensible-triple direction), then applies a single
    additive, non-fabricating augmentation:

      * ONLY when the primary read is ``neutral`` AND the PRIMARY forecast is
        unavailable, fall back to the SECONDARY ``get_prediction`` projection: if
        it carries a genuine directional call (up/down) the bias is set from it and
        the rationale notes the fallback source. A ``flat``/absent prediction, or a
        prediction present alongside an available forecast, changes nothing.

    When BOTH the forecast and the prediction are unavailable, the read stays
    neutral — no direction is fabricated (R3.14). This never touches the committed
    decision, the Opportunity_Tier, the Size_Factor, or the Trade_Validator; it
    only enriches the non-committal interim read. Best-effort and total: any
    failure degrades to the un-augmented ``opportunity.best_current_read`` output.
    """
    read = opportunity.best_current_read(evidence, tier_eval)
    try:
        if not isinstance(read, dict) or read.get("bias") != "neutral":
            return read
        ev = evidence if isinstance(evidence, dict) else {}
        forecast = ev.get("forecast")
        forecast_available = isinstance(forecast, dict) and forecast.get("available") is True
        if forecast_available:
            # The forecast already fed the primary bias; do not override it.
            return read
        prediction = ev.get("prediction")
        if not (isinstance(prediction, dict) and prediction.get("available") is True):
            return read  # both forecast + prediction unavailable → stay neutral
        direction = prediction.get("direction")
        if direction == "up":
            read["bias"] = "bullish"
        elif direction == "down":
            read["bias"] = "bearish"
        else:
            return read  # 'flat' → no directional fallback, stay neutral
        why = read.get("why_standing_aside") or ""
        note = "Interim direction from the secondary prediction (forecast unavailable)."
        read["why_standing_aside"] = f"{why} {note}".strip() if why else note
    except Exception as e:  # noqa: BLE001 - interim read must never break a finalize/pulse
        print(f"[Deep Quant] WARN: best_current_read prediction fallback failed: {e}")
    return read


def _stamp_opportunity_tier(state: AgentState, decision: dict) -> None:
    """Stamp the evidence-derived Opportunity_Tier, Size_Factor, and (for a
    stand-aside HOLD) the Best_Current_Read onto the committed decision (R1.5, R8.1).

    A committed directional (BUY/SELL) decision is tagged with the tier its evidence
    supports; because the trade was validated and taken, a directional decision
    whose structural evidence falls below even the scalp bar is still tagged the
    lowest tradeable tier (``scalp``) rather than ``stand_aside`` — a taken trade is
    never labelled a stand-aside. A HOLD is always ``stand_aside`` and carries the
    non-committal Best_Current_Read. The Size_Factor is recorded metadata only: it
    never alters the (already-validated) entry/stop/target, so the Trade_Validator
    is evaluated identically for every tier (R10.2). Best-effort; never raises.
    """
    try:
        evidence, action = _evidence_for_tier(state, decision)
        tier_eval = opportunity.evaluate_tier(evidence, _OPPORTUNITY_CFG)
        act = (action or "").upper()
        if act in ("BUY", "SELL"):
            tier = tier_eval.tier if tier_eval.tier != "stand_aside" else "scalp"
        else:
            tier = "stand_aside"
        decision["opportunity_tier"] = tier
        decision["size_factor"] = opportunity.size_factor(tier, _OPPORTUNITY_CFG)
        if tier == "stand_aside":
            # Bug 6 (R2.17): surface a directional interim read on any stand-aside,
            # falling back to the secondary prediction when the forecast is absent.
            decision.setdefault(
                "best_current_read", _best_current_read(evidence, tier_eval)
            )
    except Exception as e:  # noqa: BLE001 - tagging must never break a finalize
        print(f"[Deep Quant] WARN: opportunity tier stamping failed: {e}")


# ── Per-thread commit guard (Bug 5, Layer 1) ─────────────────────────────────
# Best-effort, process-level dedupe of the finalize chokepoint keyed on the
# LangGraph thread_id. Maps a committed thread_id to the FIRST defensibility
# record it produced, so a second finalize for the same thread is a journaling
# no-op that returns the original record instead of writing another journal row.
# This is the fast in-process backstop; the journal's own idempotent insert
# (record_decision, task 6.1) is the durable one. Never raises into a node.
_COMMITTED_THREAD_RECORDS: dict = {}


def _thread_id_from_config(config) -> Optional[str]:
    """Extract the LangGraph thread_id from a node ``config``, or None.

    LangGraph passes ``config`` to any node callable that declares a ``config``
    parameter; the thread_id lives at ``config["configurable"]["thread_id"]``.
    Every access is guarded so a missing / non-dict / mistyped config degrades to
    None and this helper NEVER raises into a node.
    """
    try:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict):
            tid = configurable.get("thread_id")
            if tid is not None:
                return str(tid)
    except Exception:
        pass
    return None


def _finalize_decision(state: AgentState, decision: dict, thread_id: Optional[str] = None) -> dict:
    """Attach the defensibility record AND persist the decision to the journal.

    Single chokepoint for every finalize path (validated declare_trade, the
    data-gating HOLD, the forced HOLD, and the bounded-hunt force_terminal) so each
    committed decision is both defensible (R7) and recorded for the measurement
    feedback loop (Phase 2). The Adaptive Opportunity Engine tier is stamped here
    too, so every committed decision carries its Opportunity_Tier (R1.5, R9.1).
    Journaling is best-effort and never raises into the run.

    Per-thread idempotency (Bug 5): when a non-NULL ``thread_id`` is supplied and
    that thread already committed in this process, a second finalize is a
    journaling NO-OP — the first committed defensibility record is returned and
    ``journal.record_decision`` is NOT called again. ``state["decision"]`` already
    routes to ``end`` in ``should_continue``; this guard closes the residual
    re-entry / same-turn duplicate-journal path. The guard is best-effort and
    never raises; the journal's idempotent insert is the durable backstop.
    """
    # Bug 5 Layer 1: short-circuit a re-entered commit for an already-committed
    # thread. Return the FIRST defensibility record unchanged and skip journaling.
    if thread_id is not None:
        try:
            if thread_id in _COMMITTED_THREAD_RECORDS:
                prior = _COMMITTED_THREAD_RECORDS.get(thread_id)
                decision["defensibility"] = prior
                return prior
        except Exception as e:  # noqa: BLE001 - guard must never break a finalize
            print(f"[Deep Quant] WARN: per-thread commit guard lookup failed: {e}")
    # Stamp the evidence-derived Opportunity_Tier BEFORE building the defensibility
    # record so build_defensibility_record can mirror it into the record (R9.1).
    _stamp_opportunity_tier(state, decision)
    decision["defensibility"] = build_defensibility_record(
        state["messages"],
        decision,
        mode=state.get("mode"),
        manual_trade=state.get("manual_trade"),
        profile=state.get("profile"),
    )
    # Degraded-data label (R6.3, Property 15). Every terminal decision funnels
    # through this chokepoint, so this is the single place to detect a commit made
    # while core acquisition is still unresolved. When `_core_acquisition_resolved`
    # is False, stamp `data_degraded: True` on the decision and append a note to
    # the defensibility record naming the still-unresolved core tools. A decision
    # committed with ALL core tools resolved is NOT labeled (key absent/False, no
    # note). Best-effort and total: never raise into the run.
    try:
        if not _core_acquisition_resolved(state["messages"]):
            unresolved = sorted(CORE_DATA_TOOL_NAMES - _resolved_core_tools(state["messages"]))
            note = (
                "decision reached on degraded data (core tools unresolved: "
                f"{', '.join(unresolved) if unresolved else 'unknown'})"
            )
            decision["data_degraded"] = True
            record = decision["defensibility"]
            if isinstance(record, dict):
                record["data_degraded"] = True
                record["data_degraded_note"] = note
                summary = record.get("summary")
                if isinstance(summary, str):
                    record["summary"] = f"{summary} {note}."
    except Exception as e:
        print(f"[Deep Quant] WARN: data_degraded stamping failed: {e}")
    # Opt-in Feature-Attribution Weight_Map consultation (feature-attribution-pruning,
    # R6.2-6.5, R9.3-9.4). Runs AFTER the defensibility record is built (and thus
    # after the Trade_Validator that gates which declare_trade reaches finalize),
    # and is a no-op unless ATTRIBUTION_WEIGHT_MAP_ENABLED is explicitly set — so
    # by default this line is byte-for-byte inert.
    _apply_weight_map_to_conviction(decision, symbol=state.get("symbol"))
    try:
        journal.record_decision(
            decision,
            symbol=state.get("symbol"),
            timeframe=state.get("timeframe"),
            mode=state.get("mode"),
            thread_id=thread_id,
        )
    except Exception as e:
        print(f"[Deep Quant] WARN: journal.record_decision failed: {e}")
    # Bug 5 Layer 1: record this thread's first commit so any re-entry short-
    # circuits above. Best-effort — a guard failure never breaks the finalize.
    if thread_id is not None:
        try:
            _COMMITTED_THREAD_RECORDS[thread_id] = decision["defensibility"]
        except Exception as e:  # noqa: BLE001 - guard must never break a finalize
            print(f"[Deep Quant] WARN: per-thread commit guard update failed: {e}")
    return decision["defensibility"]


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

    # ── VERIFY-mode devil's advocate (multi-agent-debate, R11) ───────────────
    # Before the risk-manager forms its verdict, run the Bear_Agent as an explicit
    # devil's advocate against the user-proposed trade and inject its stance into
    # the conversation the model is about to see, so the verdict path weighs it
    # (R11.1, R11.2). It runs EXACTLY ONCE per VERIFY run and only after the
    # Shared_Evidence is available, cites only that evidence (R11.4), and NEVER
    # commits or blocks — it is read-only bound and only appends a message, so the
    # existing VERIFY verdict path stays the sole decision authority (R11.3).
    # Inert for every non-VERIFY run (FIND / DEBATE / QA unchanged).
    devils_advocate_msg = None
    if _should_run_verify_devils_advocate(state, mode, messages):
        print("[Deep Quant Agent] VERIFY run -> invoking Bear devil's advocate against the proposed trade.")
        devils_advocate_msg = run_verify_devils_advocate(state, messages)
        if devils_advocate_msg is not None:
            # The model sees the devil's-advocate stance when forming its verdict.
            messages = list(messages) + [devils_advocate_msg]

    # ── Deterministic session-context pruning (adaptive-opportunity-engine R7) ─
    # Bound the context SENT to the LLM this turn so a long, many-resume hunt does
    # not grow unbounded in cost. `prune_messages` is a no-op until the history
    # exceeds the configured ceiling, and it always retains the system message, the
    # latest usable result of every tool, and the most-recent turns — so the
    # defensibility record and Q&A grounding stay intact (R7.2). It never mutates
    # the checkpointed state (only what is sent this turn).
    messages = opportunity.prune_messages(messages, _OPPORTUNITY_CFG)

    # Compact large tool payloads (chart_patterns near-duplicates, long news
    # headline lists, oversized arrays) to their decision-relevant fields BEFORE
    # the LLM call. This markedly cuts the token count — and the latency — of the
    # heavy synthesis turn that was stalling the run, on any model. It only shrinks
    # what is SENT this turn; the checkpointed history (and thus the defensibility
    # record / Q&A grounding) keep the full original payloads.
    messages = compact_tool_payloads(messages)

    # Profile-gated binding: the F&O workspace can call the F&O-only tools
    # (options analytics); every other workspace is bound to the active-symbol
    # tool set WITHOUT them, so options / broad-market data is never pulled on a
    # non-F&O run and the analysis stays on the operator's selected symbol.
    response = _llm_for_profile(state).invoke(messages)

    print(f"[Deep Quant Agent] Model responded. Content length: {len(response.content or '')}")

    # Single structured extraction pass: native structured calls are primary;
    # otherwise custom-token markup is parsed and classified per-call.
    extraction = extract_tool_calls(response)

    if extraction.used_text_extraction:
        print(f"[Deep Quant Agent] Extracted {len(extraction.calls)} tool call(s) from content markup.")

    ok_calls = [c for c in extraction.calls if c.status == "ok"]
    failed_calls = [c for c in extraction.calls if c.status != "ok"]

    # The assistant message carries every discovered call (so each id is paired
    # with a tool response and nothing is dropped), but only `ok` calls are
    # actually fed to the ToolNode. Failures are answered with synthetic results.
    response.tool_calls = [
        {"name": c.name, "args": c.args or {}, "id": c.id}
        for c in extraction.calls
    ]
    response.additional_kwargs["_extraction_status"] = {
        c.id: c.status for c in extraction.calls
    }
    response.additional_kwargs["_synthetic_results"] = {
        c.id: _synthetic_failure_content(c) for c in failed_calls
    }

    if extraction.calls:
        print(
            f"[Deep Quant Agent] Tool calls -> ok: {[c.name for c in ok_calls]}, "
            f"failed: {[(c.name, c.status) for c in failed_calls]}"
        )
    else:
        snippet = (response.content or "").strip().replace('\n', ' ')
        print(f"[Deep Quant Agent] Model output snippet: {snippet[:200]}...")

    # ── Deterministic loop bookkeeping (R2.3, R2.5) ──────────────────────────
    # `reasoning_turns` counts consecutive reasoning-only turns. Any turn that
    # issues tool calls (ok or failed — both route to the tools node) resets the
    # counter so that pending work always takes precedence over the cap (R2.4).
    if extraction.calls:
        reasoning_turns = 0
    else:
        reasoning_turns = state.get("reasoning_turns", 0) + 1

    # `market_data_seen` latches true once any market-data tool has returned
    # usable data in this run. (Gating on this flag is implemented separately.)
    market_data_seen = bool(state.get("market_data_seen")) or _market_data_seen(messages)

    update = {
        "messages": [response],
        "reasoning_turns": reasoning_turns,
        "market_data_seen": market_data_seen,
    }

    # ── Adaptive Opportunity Engine bookkeeping (adaptive-opportunity-engine) ──
    # Maintained exactly like `reasoning_turns` above: every model turn charges the
    # Session_Budget (`session_turns`); a turn that arms a `watch_price_condition`
    # registers a Watch_Cycle (`watch_cycles`, which converges on the Watch_Cap);
    # and the first turn of a `/run` stamps the wall-clock start so the
    # Session_Budget wall-clock bound can fire. These counters are what
    # `should_continue` consults to close the unbounded watch/re-watch loop.
    update["session_turns"] = int(state.get("session_turns") or 0) + 1
    if state.get("session_started_at") is None:
        update["session_started_at"] = time.time()
    if any(c.name == "watch_price_condition" for c in ok_calls):
        # A NEW or CHANGED watch registration is a fresh Watch_Cycle. A pure
        # continuation re-arm of the SAME thesis (identical symbol / timeframe /
        # direction / level as the currently-armed watch — e.g. after a heartbeat
        # decides to keep waiting) is NOT counted as a fresh cycle (R6.1), so a
        # heartbeat-driven continuation does not burn the Watch_Cap.
        proposed_watch = next(
            (c.args for c in ok_calls if c.name == "watch_price_condition"), {}
        ) or {}
        prior_watch = _latest_watch_args(messages)
        is_continuation = bool(prior_watch) and (
            opportunity.thesis_fingerprint(prior_watch)
            == opportunity.thesis_fingerprint(proposed_watch)
        )
        if not is_continuation:
            update["watch_cycles"] = int(state.get("watch_cycles") or 0) + 1

    # Persist the devil's-advocate stance into the verification reasoning, ordered
    # BEFORE the model's verdict response (it is a plain AIMessage with no tool
    # calls, so message-ordering / tool-pairing invariants are preserved and
    # ``messages[-1]`` remains the verdict response for the loop). Latch the
    # one-shot flag so it runs exactly once per VERIFY run (R11.1-R11.3).
    if devils_advocate_msg is not None:
        update["messages"] = [devils_advocate_msg, response]
        update["verify_devils_advocate_done"] = True

    # ── DEBATE Research_Phase entry (multi-agent-debate, R2.1) ───────────────
    # A DEBATE-mode run enters through the `agent` node (route_entry maps the
    # DEBATE research-entry string to `agent`). The first time the model is
    # invoked for that run, latch `phase = "research"` so the downstream
    # `tool_node` data-gate suppresses any `declare_trade` while evidence is
    # gathered (the Research_Phase "stops before any trade is declared"). The
    # flag is set ONLY for DEBATE and ONLY when not already set, so FIND /
    # VERIFY / QA runs never populate `phase` and are completely unchanged.
    if (mode or "").strip().upper() == DEBATE_MODE and not state.get("phase"):
        print("[Deep Quant Agent] DEBATE run -> entering Research_Phase (phase=research).")
        update["phase"] = "research"

    return update

# Base ToolNode used to execute only the well-formed (`ok`) tool calls.
_base_tool_node = ToolNode(tools)


def tool_node(state: AgentState, config=None):
    """Execute only `ok` tool calls; answer failed calls with synthetic results.

    Every tool call present on the assistant message receives a ToolMessage so
    no call is left unanswered: `ok` calls are dispatched to the real tools while
    `parse_failure`/`invalid_tool` calls are answered with a synthetic feedback
    message so the model can self-correct and the loop continues.

    First-turn data-acquisition gating (R3.1-R3.3): a ``declare_trade`` issued
    before any market-data Analysis_Tool has returned usable data is NOT allowed
    to finalize. If no market-data tool has even been attempted, the declaration
    is rejected with feedback and the loop continues so the agent gathers data.
    If market-data tools were attempted but yielded no usable directional data,
    the run finalizes with a HOLD that states the data limitation (R5.3).
    """
    # Bug 5: the LangGraph thread_id (if provided by the runtime) keys the
    # per-thread idempotency guard at the finalize chokepoint. Best-effort → None.
    thread_id = _thread_id_from_config(config)
    last_message = state["messages"][-1]
    all_calls = list(getattr(last_message, "tool_calls", None) or [])
    statuses = (last_message.additional_kwargs or {}).get("_extraction_status", {})
    synthetic = (last_message.additional_kwargs or {}).get("_synthetic_results", {})

    ok_calls = [tc for tc in all_calls if statuses.get(tc["id"], "ok") == "ok"]
    failed_calls = [tc for tc in all_calls if statuses.get(tc["id"], "ok") != "ok"]

    # ── DEBATE Research_Phase declaration gate (multi-agent-debate, R2.1) ────
    # While `phase == "research"` the Research_Phase gathers the Shared_Evidence
    # only and MUST stop before any trade is declared. Reusing the proven
    # data-gate mechanism, any `declare_trade` issued during research is held
    # back here: it is answered with synthetic feedback and is NEVER committed
    # (no `state["decision"]` is set), so the Research_Phase can never finalize a
    # trade. A `declare_trade` is also the model signalling it is done gathering,
    # so it hands control off to the debate roles by transitioning the phase to
    # "debate" (consumed by `route_after_tools` -> `bull`). Non-declare tool
    # calls (the actual evidence gathering) execute normally below. This branch
    # is inert for every non-DEBATE run because `phase` is only ever set for a
    # DEBATE run (see `call_model`).
    research_phase = (state.get("phase") == "research")
    research_blocked_declares: List[dict] = []
    if research_phase:
        retained = []
        for tc in ok_calls:
            if tc.get("name") == "declare_trade":
                research_blocked_declares.append(tc)
            else:
                retained.append(tc)
        ok_calls = retained

    # ── First-turn data-acquisition gate (R3.1-R3.3) ─────────────────────────
    # `market_data_seen` is maintained by call_model from the messages that
    # preceded this turn, so it reflects whether usable market data was returned
    # in a PRIOR turn. While it is false, no declare_trade may finalize: hold
    # back any declare_trade calls so they are not committed.
    market_data_seen = bool(state.get("market_data_seen"))
    blocked_declares: List[dict] = []
    if not market_data_seen:
        retained = []
        for tc in ok_calls:
            if tc.get("name") == "declare_trade":
                blocked_declares.append(tc)
            else:
                retained.append(tc)
        ok_calls = retained

    # ── Heartbeat core-acquisition gate (R6.1, R6.2) ─────────────────────────
    # A heartbeat pulse must not precipitate a premature stand-aside before the
    # Core_Data_Tools have finished their first-pass acquisition. `market_data_seen`
    # latches True on ANY single market-data tool (e.g. a usable consensus read),
    # so it cannot express "all core tools have finished their first pass" — that
    # is `_core_acquisition_resolved`. When ALL of the following hold, hold the
    # non-directional (HOLD / stand-aside) declare back so it is answered with
    # feedback and the loop continues to finish acquiring the core tools:
    #   • the most recent resume was a heartbeat (`last_resume_kind`), AND
    #   • the core acquisition is still unresolved, AND
    #   • no bounded-hunt cap has fired (`opportunity.termination_reason`).
    # A directional BUY/SELL is NEVER gated, and a fired cap (Watch_Cap /
    # Session_Budget) retains ABSOLUTE precedence — in either case the declare
    # passes straight through to the normal finalize path (Property 14, 16).
    # Symmetric to the `blocked_declares` gate above; inert for every
    # non-heartbeat resume and once core acquisition has resolved.
    heartbeat_gated_declares: List[dict] = []
    if (
        state.get("last_resume_kind") == opportunity.RESUME_HEARTBEAT
        and not _core_acquisition_resolved(state["messages"])
        and opportunity.termination_reason(state, _OPPORTUNITY_CFG, time.time()) is None
    ):
        retained = []
        for tc in ok_calls:
            if tc.get("name") == "declare_trade" and not _declare_is_directional(tc.get("args") or {}):
                heartbeat_gated_declares.append(tc)
            else:
                retained.append(tc)
        ok_calls = retained

    # ── Invalidation post-mortem re-arm gate (adaptive-opportunity-engine R4) ──
    # After an invalidation resume set `postmortem_pending` + `prior_thesis` (see
    # the invalidation-detection bookkeeping below). While a post-mortem is pending,
    # a proposed `watch_price_condition` re-arm that is the SAME thesis as the
    # just-invalidated one (`opportunity.is_rearm_unchanged`) is SUPPRESSED — it is
    # answered with feedback demanding a changed structure/timeframe/tier or a
    # stand-aside, and is NOT registered — so the agent cannot blindly re-arm the
    # thesis that just failed (R4.2). A genuinely different re-arm passes through,
    # and its invalidation level is re-sized to sit at least a volatility floor away
    # so a noise-level stop cannot immediately re-trip on resume (R4.3). Symmetric
    # to the `blocked_declares` gate above; inert when no post-mortem is pending.
    prior_thesis = state.get("prior_thesis")
    rearm_suppressed: List[dict] = []
    if state.get("postmortem_pending") and prior_thesis:
        atr = _latest_atr(_latest_tool_results(state["messages"]))
        retained = []
        for tc in ok_calls:
            if tc.get("name") != "watch_price_condition":
                retained.append(tc)
                continue
            proposed = tc.get("args") or {}
            if opportunity.is_rearm_unchanged(prior_thesis, proposed, atr, _OPPORTUNITY_CFG):
                rearm_suppressed.append(tc)
                continue
            # A changed thesis is allowed — re-size its invalidation level to the
            # volatility floor so a noise-level stop does not immediately re-trip.
            floored = opportunity.volatility_floored_invalidation(
                proposed.get("direction"),
                proposed.get("price_level"),
                proposed.get("invalidation_level"),
                atr,
            )
            if floored is not None:
                proposed = dict(proposed)
                proposed["invalidation_level"] = floored
                tc = dict(tc)
                tc["args"] = proposed
            retained.append(tc)
        ok_calls = retained

    out_messages: List[BaseMessage] = []

    if ok_calls:
        temp_message = AIMessage(content="", tool_calls=ok_calls)
        result = _base_tool_node.invoke({"messages": [temp_message]})
        out_messages.extend(result["messages"])

    for tc in failed_calls:
        content = synthetic.get(
            tc["id"],
            f"Tool-call error: '{tc.get('name')}' could not be executed.",
        )
        print(f"[Deep Quant Tools] Synthetic feedback for failed call '{tc.get('name')}' ({statuses.get(tc['id'])}).")
        out_messages.append(
            ToolMessage(content=content, tool_call_id=tc["id"], name=tc.get("name") or "unknown_tool")
        )

    update = {"messages": out_messages}

    # ── Answer any suppressed same-thesis re-arm (adaptive-opportunity-engine R4.2)
    # A re-arm the post-mortem gate suppressed is answered with feedback and NOT
    # registered, so the model must change its approach or stand aside. No decision
    # is set, so the bounded loop continues (and the Watch_Cap still bounds it).
    for tc in rearm_suppressed:
        note = (
            "watch_price_condition suppressed: this re-arms the SAME thesis (symbol / "
            "timeframe / direction / level) that was just invalidated. Do NOT blindly "
            "re-arm the failed setup — change the structure, timeframe, or tier, or "
            "stand aside. Re-analyze before proposing a materially different watch."
        )
        print("[Deep Quant Tools] Suppressed unchanged watch re-arm (invalidation post-mortem).")
        out_messages.append(
            ToolMessage(content=note, tool_call_id=tc["id"], name="watch_price_condition")
        )

    # ── Invalidation-resume detection & post-mortem arming (R4.1, R4.4) ────────
    # When a resumed watch returns the "Setup INVALIDATED" marker, the price-only
    # invalidation tripped. Arm the post-mortem for the NEXT turn (`postmortem_pending`
    # + `prior_thesis` fingerprint of the just-invalidated watch) so the re-arm gate
    # above forces a strategic pivot, count the invalidation toward the Watch_Cap
    # (R4.4), and record the classified resume kind for the cheap Delta_Recheck.
    for m in out_messages:
        if not (
            _is_tool_message(m)
            and getattr(m, "name", None) == "watch_price_condition"
            and isinstance(getattr(m, "content", None), str)
        ):
            continue
        if m.content.startswith("Setup INVALIDATED"):
            invalidated_args = _latest_watch_args(state["messages"])
            update["postmortem_pending"] = True
            update["prior_thesis"] = opportunity.thesis_fingerprint(invalidated_args)
            update["invalidation_count"] = int(state.get("invalidation_count") or 0) + 1
            # Each invalidation counts toward the Watch_Cap (R4.4).
            update["watch_cycles"] = int(state.get("watch_cycles") or 0) + 1
            update["last_resume_kind"] = opportunity.RESUME_INVALIDATION
            print("[Deep Quant Tools] Invalidation resume -> post-mortem armed (Watch_Cycle counted).")
            break
        if m.content.startswith("Heartbeat check"):
            # A bounded mid-wait pulse (R5.2): charge one heartbeat against the
            # ceiling + the Session_Budget via the pure accountant, and record the
            # resume kind for the cheap Delta_Recheck. It does NOT count toward the
            # Watch_Cap (it is not a new watch registration).
            account = opportunity.account_heartbeat(state, _OPPORTUNITY_CFG)
            update["heartbeat_count"] = account.heartbeat_count
            update["session_turns"] = account.session_turns
            update["last_resume_kind"] = opportunity.RESUME_HEARTBEAT
            # Surface an UPDATED, non-committal Best_Current_Read on this pulse so
            # the glass box shows a fresh read during the wait even when the agent
            # keeps waiting rather than committing a stand-aside (Requirement 8.2).
            # Built from the evidence gathered so far (no committed decision, so no
            # execution levels). Best-effort — a read failure never breaks the loop.
            try:
                _hb_evidence, _ = _evidence_for_tier(state, {})
                _hb_tier = opportunity.evaluate_tier(_hb_evidence, _OPPORTUNITY_CFG)
                # Bug 6 (R2.17): surface a directional interim read on each wait
                # pulse, falling back to the secondary prediction when the forecast
                # is unavailable (neutral stays neutral when both are absent).
                update["best_current_read"] = _best_current_read(_hb_evidence, _hb_tier)
            except Exception as _hb_read_err:  # noqa: BLE001 - read must never break the loop
                print(f"[Deep Quant Tools] WARN: heartbeat best_current_read failed: {_hb_read_err}")
            print(f"[Deep Quant Tools] Heartbeat resume (accepted={account.accepted}, count={account.heartbeat_count}).")
            break

    # ── Resolve gated Research_Phase declare_trade calls (R2.1) ──────────────
    # A `declare_trade` issued while gathering the Shared_Evidence is suppressed:
    # it is answered with feedback and NOT committed. The Research_Phase then
    # hands off to the debate roles — signalled by transitioning the phase to
    # "debate", which `route_after_tools` routes to the `bull` node. No
    # `state["decision"]` is ever set here, so research can never finalize a
    # trade (Property 7).
    if research_blocked_declares:
        for tc in research_blocked_declares:
            note = (
                "declare_trade suppressed during the research phase: the Research_Phase "
                "gathers the shared evidence base only and never commits a trade. The "
                "Judge will commit the trade after the Bull/Bear debate weighs this "
                "evidence. Stop declaring and conclude your evidence gathering."
            )
            print(
                "[Deep Quant Tools] Gated declare_trade during research phase "
                "(suppressed, not committed) -> handing off to debate roles."
            )
            out_messages.append(
                ToolMessage(content=note, tool_call_id=tc["id"], name="declare_trade")
            )
        # Mark the research → debate handoff. The Shared_Evidence is exactly the
        # ToolMessages accumulated in state["messages"] (consumed verbatim by the
        # Bull, Bear, and Judge roles); nothing is re-gathered.
        update["phase"] = "debate"
        return update

    # ── Resolve gated declare_trade calls ────────────────────────────────────
    if blocked_declares:
        # Has any market-data tool been called this run? Errors/unavailable
        # results still count as an *attempt* — they just did not yield usable
        # directional data.
        attempted = _market_data_attempted(state["messages"])
        for tc in blocked_declares:
            if attempted:
                note = (
                    "declare_trade not committed: required market data for a directional "
                    "decision is unavailable (market-data tools were called but failed or "
                    "returned no usable data). Finalizing HOLD due to the data limitation."
                )
            else:
                note = (
                    "declare_trade rejected: no market-data tool has returned data yet in "
                    "this run. You MUST call at least one market-data tool (e.g. "
                    "get_multi_tf_trend or get_consensus_report) and review its result "
                    "before declaring a trade. Continue your analysis."
                )
            print(
                f"[Deep Quant Tools] Gated declare_trade (market_data_seen=False, "
                f"attempted={attempted}) -> {'HOLD' if attempted else 'block+loop'}."
            )
            out_messages.append(
                ToolMessage(content=note, tool_call_id=tc["id"], name="declare_trade")
            )

        if attempted:
            # R5.3: directional data was sought but is unavailable. Terminate
            # with an explicit HOLD that states the limitation rather than
            # looping or fabricating a setup.
            hold_decision = {
                "action": "HOLD",
                "conviction_score": 0,
                "reason": "directional-data-unavailable",
                "setup_validation": (
                    "Required market data for a directional decision is unavailable; "
                    "market-data tools were called but failed or returned no usable data. "
                    "Holding to preserve capital rather than trade on assumptions."
                ),
                "execution_plan": "HOLD — no trade taken due to a data limitation.",
                "source": "data_gating",
            }
            hold_decision["defensibility"] = _finalize_decision(state, hold_decision, thread_id=thread_id)
            out_messages.append(
                AIMessage(
                    content=json.dumps(
                        {
                            "conviction_score": hold_decision["conviction_score"],
                            "setup_validation": hold_decision["setup_validation"],
                            "execution_plan": hold_decision["execution_plan"],
                        }
                    )
                )
            )
            update["decision"] = hold_decision
        # else: premature finalize with no data acquired at all → no decision is
        # set, so route_after_tools returns the agent to gather data (R3.3). The
        # bounded reasoning cap still guarantees eventual termination.
        return update

    # ── Resolve heartbeat-gated declare_trade calls (R6.1, R6.2) ─────────────
    # A stand-aside HOLD the heartbeat acquisition gate held back is answered
    # with feedback naming the still-unresolved core tools and is NOT committed
    # (no `state["decision"]` is set), so route_after_tools returns the agent to
    # finish acquiring (or explicitly resolving as unavailable) the core tools
    # before a terminal HOLD may be committed. The bounded reasoning cap and the
    # Watch_Cap / Session_Budget still guarantee eventual termination — and once
    # a cap fires, `termination_reason` is non-None so this gate no longer
    # triggers and the terminal decision is permitted (Property 14, 16).
    if heartbeat_gated_declares:
        unresolved = sorted(CORE_DATA_TOOL_NAMES - _resolved_core_tools(state["messages"]))
        for tc in heartbeat_gated_declares:
            note = (
                "declare_trade not committed: this stand-aside HOLD was proposed on a "
                "heartbeat wake before your core data acquisition finished. Core tools "
                f"still unresolved: {', '.join(unresolved)}. Finish acquiring these core "
                "tools (or confirm each is explicitly unavailable) before committing a "
                "terminal HOLD, then re-declare. Continue your analysis."
            )
            print(
                "[Deep Quant Tools] Gated heartbeat declare_trade "
                f"(core unresolved={unresolved}) -> block+loop."
            )
            out_messages.append(
                ToolMessage(content=note, tool_call_id=tc["id"], name="declare_trade")
            )
        return update

    # ── Normal finalize path ─────────────────────────────────────────────────
    # A validated declare_trade (only reachable once market data has been seen)
    # is the authoritative completion signal: record its structured result as
    # state["decision"] so should_continue terminates the run without ever
    # matching keywords in reasoning prose (R2.2, R2.7). If the Rust
    # Trade_Validator REJECTED the declaration (R6.7), do NOT finalize — leave
    # the decision unset so the bounded loop lets the agent revise the levels
    # and re-declare.
    decision = _decision_from_declare(ok_calls)
    if decision is not None and _declare_was_rejected(out_messages):
        print("[Deep Quant Tools] declare_trade was REJECTED by the validator; continuing loop for revision.")
        decision = None
    if decision is not None:
        print(f"[Deep Quant Tools] declare_trade committed decision: action={decision.get('action')}")
        # Attach the defensibility record assembled from the tool results seen
        # so far so the committed trade carries the evidence behind it (R7), and
        # record it to the Trade_Journal for the measurement loop (Phase 2).
        _finalize_decision(state, decision, thread_id=thread_id)
        update["decision"] = decision

    return update

def should_continue(state: AgentState) -> str:
    """Deterministic routing for the ReAct loop (Requirement 2).

    Precedence (strict order):
      1. Pending tool calls on the latest message → execute them (R2.1, R2.4).
         A watch_price_condition call routes to the tools node as well, where
         its interrupt() suspends the run in a resumable state rather than
         terminating it (R2.6) — surfaced here via the distinct "suspend" route.
      2. A finalized decision in state["decision"] → terminate (R2.2).
      3. Reasoning budget remaining → loop for more reasoning (R2.3).
      4. Reasoning budget exhausted → force a HOLD decision (R2.5).

    Completion is read ONLY from state["decision"]; reasoning prose is never
    keyword-matched (R2.7).
    """
    messages = state["messages"]
    last_message = messages[-1]

    print("\n[Deep Quant Routing] === Checking Routing Decision ===")
    print(f"[Deep Quant Routing] Last message type: {type(last_message).__name__}")

    all_calls = list(getattr(last_message, "tool_calls", None) or [])
    statuses = (getattr(last_message, "additional_kwargs", None) or {}).get("_extraction_status", {})
    ok_calls = [tc for tc in all_calls if statuses.get(tc.get("id"), "ok") == "ok"]

    # ── Precedence 1: pending tool calls always take priority ────────────────
    # Both `ok` calls (executed) and failed calls (answered with synthetic
    # feedback) must reach the tools node so every call is resolved and the
    # loop continues — never terminated by the reasoning cap while work pends.
    if all_calls:
        if any(tc.get("name") == "watch_price_condition" for tc in ok_calls):
            # ── Adaptive Opportunity Engine bounded-hunt gate (R3.1-3.3, 3.5) ─
            # Before arming yet another watch, consult the Watch_Cap and
            # Session_Budget. When either bound is reached, the unbounded
            # analyze -> watch -> invalidate -> re-watch loop is CLOSED here: route
            # to `force_terminal` (which answers the pending watch call and commits
            # a terminal decision) instead of `suspend`. Otherwise suspend exactly
            # as before. `termination_reason` covers both the Watch_Cap and the
            # Session_Budget with the documented precedence.
            if opportunity.termination_reason(state, _OPPORTUNITY_CFG, time.time()) is not None:
                print(
                    "[Deep Quant Routing] Watch pending but bounded hunt exhausted "
                    f"({opportunity.termination_reason(state, _OPPORTUNITY_CFG, time.time())}). "
                    "Routing to -> force_terminal"
                )
                return "force_terminal"
            print("[Deep Quant Routing] Pending watch_price_condition call. Routing to -> suspend (tools/interrupt)")
            return "suspend"
        print(
            f"[Deep Quant Routing] Pending tool call(s): {[tc.get('name') for tc in all_calls]}. Routing to -> tools"
        )
        return "continue"

    # ── Precedence 2: a finalized, validated decision terminates the run ─────
    if state.get("decision"):
        print(f"[Deep Quant Routing] Finalized decision present ({state['decision'].get('action')}). Routing to -> end")
        return "end"

    # ── Precedence 2b: Session_Budget exhausted with no pending work (R3.2/3.3) ─
    # A session can spend its turn / wall-clock budget without a watch pending
    # (e.g. a long reasoning run that never arms a watch). Terminate it with a
    # committed decision so the budget is a hard bound regardless of the watch path.
    # The Watch_Cap is NOT checked here (it only bites when a watch is being armed).
    if opportunity.session_budget_exhausted(state, _OPPORTUNITY_CFG, time.time()):
        print("[Deep Quant Routing] Session budget exhausted (no pending work). Routing to -> force_terminal")
        return "force_terminal"

    # ── Precedence 3: bounded reasoning loop ─────────────────────────────────
    # The budget is resolved from the environment on each check so a deployment
    # can tune DEEP_QUANT_MAX_REASONING_TURNS without a code change; it degrades
    # to the documented default (6) for any invalid value (R1.1-1.4). The
    # reset-on-tool-call semantics in `call_model` are unaffected.
    reasoning_budget = resolve_max_reasoning_turns()
    reasoning_turns = state.get("reasoning_turns", 0)
    print(f"[Deep Quant Routing] Consecutive reasoning turns: {reasoning_turns}/{reasoning_budget}")
    if reasoning_turns < reasoning_budget:
        print("[Deep Quant Routing] Reasoning budget remaining. Routing to -> loop_agent")
        return "loop_agent"

    # ── Precedence 4: reasoning exhausted ────────────────────────────────────
    # In a DEBATE Research_Phase, exhausting the reasoning budget means the
    # model gathered evidence without ever (usably) declaring; hand the gathered
    # Shared_Evidence off to the debate roles instead of forcing a HOLD (R2.1).
    if state.get("phase") in ("research", "debate"):
        print("[Deep Quant Routing] Research budget reached. Routing to -> debate (bull)")
        return DEBATE_HANDOFF

    # ── Precedence 4 (non-DEBATE): reasoning exhausted → forced HOLD ──────────
    print("[Deep Quant Routing] Reasoning budget exhausted. Routing to -> force_hold")
    return "force_hold"


def force_hold(state: AgentState, config=None):
    """Inject a HOLD decision when the reasoning budget is exhausted (R2.5).

    The agent produced no validated decision and no pending tool call within
    the allowed reasoning turns, so the loop terminates with an explicit HOLD
    whose stated reason is `no-decision-reached` rather than fabricating a trade.
    """
    print("[Deep Quant Routing] Injecting forced HOLD decision (reason: no-decision-reached).")
    decision = {
        "action": "HOLD",
        "conviction_score": 0,
        "reason": "no-decision-reached",
        "setup_validation": (
            "Reasoning budget exhausted without a validated A+ setup. "
            "Holding to preserve capital rather than force a low-conviction trade."
        ),
        "execution_plan": "HOLD — no trade taken.",
        "source": "forced_hold",
    }
    # _finalize_decision -> _stamp_opportunity_tier stamps opportunity_tier
    # (stand_aside for this HOLD action), size_factor, and the Best_Current_Read,
    # identically to force_terminal (R3.1/R3.2). A reasoning-exhaustion HOLD is thus
    # as actionable as a bounded-hunt one, and no directional entry/stop/target is
    # fabricated for the stand-aside.
    decision["defensibility"] = _finalize_decision(state, decision, thread_id=_thread_id_from_config(config))
    # Bug 6 (R2.13 / R2.17): make the reasoning-cap HOLD an ACTIONABLE stand-aside
    # rather than a content-free one. The Best_Current_Read (bias, key levels, why
    # waiting) was already computed by _stamp_opportunity_tier during finalize; fold
    # it into the surfaced setup_validation / execution_plan so a directional read
    # accompanies the wait. This does NOT fabricate a trade — best_current_read
    # levels are structural REFERENCE reads, never an entry/stop/target for a
    # committed position, and the termination guarantee (finite MAX_REASONING_TURNS)
    # is unchanged. Best-effort: any failure leaves the base HOLD text intact.
    try:
        _read = decision.get("best_current_read")
        if isinstance(_read, dict):
            _bias = _read.get("bias")
            _levels = _read.get("levels") if isinstance(_read.get("levels"), dict) else {}
            _why = _read.get("why_standing_aside")
            _read_bits = []
            if isinstance(_bias, str) and _bias:
                _read_bits.append(f"directional bias {_bias}")
            if _levels:
                _lv = ", ".join(f"{k}={v}" for k, v in _levels.items())
                _read_bits.append(f"key reference levels ({_lv})")
            if isinstance(_why, str) and _why.strip():
                _read_bits.append(_why.strip())
            if _read_bits:
                _read_text = "Best_Current_Read: " + "; ".join(_read_bits) + "."
                decision["setup_validation"] = decision["setup_validation"] + " " + _read_text
                decision["execution_plan"] = (
                    "HOLD — no trade taken; standing aside with a directional read "
                    f"({_bias if isinstance(_bias, str) and _bias else 'neutral'}). No entry/stop/target committed."
                )
    except Exception as e:  # noqa: BLE001 - surfacing must never break the forced HOLD
        print(f"[Deep Quant] WARN: force_hold best_current_read surfacing failed: {e}")
    final_message = AIMessage(
        content=json.dumps(
            {
                "conviction_score": decision["conviction_score"],
                "setup_validation": decision["setup_validation"],
                "execution_plan": decision["execution_plan"],
                # Surface the interim directional read alongside the reasoning-cap
                # HOLD so the glass box shows an actionable stand-aside (Bug 6).
                "best_current_read": decision.get("best_current_read"),
            }
        )
    )
    return {"decision": decision, "messages": [final_message]}


def force_terminal(state: AgentState, config=None):
    """Commit a terminal decision when the bounded hunt is exhausted (R3, R8).

    Reached from ``should_continue`` when the Watch_Cap or Session_Budget is met —
    the safety net that closes the unbounded analyze -> watch -> invalidate ->
    re-watch loop the Adaptive Opportunity Engine exists to bound. This node:

      * answers any PENDING ``watch_price_condition`` tool call with a
        ``ToolMessage`` so the assistant call/response pairing invariant is
        preserved (an unanswered function call would trip strict providers), and
      * commits a terminal ``stand_aside`` HOLD via the ``_finalize_decision``
        chokepoint. The HOLD is the honest, conservative outcome at a forced
        boundary: it never fabricates a directional trade the model did not
        actually declare with validated levels (R10.1 / R10.4). The decision cites
        the ``opportunity.termination_reason`` (``watch-cap-reached`` /
        ``session-budget-exhausted``) and carries the Opportunity_Tier and the
        Best_Current_Read (both stamped by ``_finalize_decision``).
    """
    reason = opportunity.termination_reason(state, _OPPORTUNITY_CFG, time.time()) or "bounded-hunt-exhausted"
    print(f"[Deep Quant Routing] Forcing terminal decision (reason: {reason}).")

    out_messages: List[BaseMessage] = []

    # Answer a pending watch_price_condition call so no function call is orphaned.
    last_message = state["messages"][-1]
    all_calls = list(getattr(last_message, "tool_calls", None) or [])
    statuses = (getattr(last_message, "additional_kwargs", None) or {}).get("_extraction_status", {})
    for tc in all_calls:
        if tc.get("name") == "watch_price_condition" and statuses.get(tc.get("id"), "ok") == "ok":
            note = (
                f"watch_price_condition not registered: the bounded hunt is exhausted "
                f"({reason}). Committing a terminal stand-aside decision with the current "
                f"best read rather than arming another watch."
            )
            out_messages.append(
                ToolMessage(content=note, tool_call_id=tc.get("id"), name="watch_price_condition")
            )

    decision = {
        "action": "HOLD",
        "conviction_score": 0,
        "reason": reason,
        "setup_validation": (
            "Bounded hunt exhausted (" + reason + ") without a committed A+/tiered setup. "
            "Standing aside to preserve capital rather than re-arm another watch."
        ),
        "execution_plan": "HOLD — stand aside; bounded hunt reached its Watch_Cap / Session_Budget.",
        "source": "force_terminal",
    }
    # _finalize_decision stamps opportunity_tier (stand_aside), size_factor, and the
    # Best_Current_Read, then attaches the defensibility record and journals it.
    decision["defensibility"] = _finalize_decision(state, decision, thread_id=_thread_id_from_config(config))

    final_message = AIMessage(
        content=json.dumps(
            {
                "conviction_score": decision["conviction_score"],
                "setup_validation": decision["setup_validation"],
                "execution_plan": decision["execution_plan"],
            }
        )
    )
    out_messages.append(final_message)
    return {"decision": decision, "messages": out_messages}


def route_after_tools(state: AgentState) -> str:
    """After tool execution, terminate if a decision was committed, else loop.

    declare_trade commits a decision into state["decision"] during tool
    execution; when present the run ends immediately rather than spending an
    extra model turn (R2.2). Otherwise control returns to the agent.
    """
    if state.get("decision"):
        print("[Deep Quant Routing] Decision committed during tool execution. Routing to -> end")
        return "end"
    # DEBATE Research_Phase handoff: `tool_node` transitions `phase` to "debate"
    # once research is complete (a suppressed declare_trade signalled the model
    # is done gathering). Route to the debate roles (`bull`) rather than looping
    # the research agent again (R2.1). Inert for non-DEBATE runs (`phase` unset).
    if state.get("phase") == "debate":
        print("[Deep Quant Routing] Research complete (phase=debate). Routing to -> debate (bull)")
        return DEBATE_HANDOFF
    return "agent"


# ── Trade Q&A Mode (Requirement 18) ──────────────────────────────────────────
# A conversational follow-up mode in which the trader asks free-form questions
# about a completed analysis and the committed Declared_Trade. It REUSES the
# same thread_id and the MemorySaver checkpointer, so the persisted state for
# that thread — the Session_Analysis_Context: the committed `decision`, its
# defensibility record (multi-TF bias, S/R levels, indicators, patterns,
# sentiment, levels/RR/volatility basis), and the accumulated tool results in
# `messages` — is available to ground the answer (R18.1, R18.5).
#
# Hard guarantees enforced structurally here, independent of what the model does:
#   * The committed trade is IMMUTABLE in Q&A: ``qa_node`` never returns a
#     ``decision`` update and ``declare_trade`` / ``watch_price_condition`` are
#     refused rather than executed, so no Q&A turn can commit/alter a trade or
#     suspend the run (R18.6).
#   * The Session_Analysis_Context is PRESERVED: answers only append messages;
#     no prior context channel is cleared (R18.5).
#   * Missing data is handled honestly: the model may call a read-only
#     market-data Analysis_Tool to obtain it, or state it is unavailable — it is
#     instructed never to fabricate (R18.4).

QA_MODE = "QA"

# Multi-Agent Debate analysis mode (multi-agent-debate, R1.2). A request with
# ``mode == "DEBATE"`` is the ONLY trigger for the adversarial bull/bear/judge
# debate; nothing runs it implicitly (R1.4). Every other mode value (FIND,
# VERIFY, QA, or any arbitrary string) follows the unchanged legacy routing.
DEBATE_MODE = "DEBATE"

# Routing target string returned by ``route_entry`` for a DEBATE-mode run. The
# Research_Phase reuses the existing ``agent`` analysis loop (declaration is
# suppressed downstream via ``state["phase"] == "research"``), so this routing
# key is mapped to the ``agent`` node in the conditional entry point. Using a
# DISTINCT return string keeps FIND/VERIFY/QA routing byte-identical while
# making the DEBATE entry distinguishable at the routing layer.
DEBATE_RESEARCH_ENTRY = "research"

# Maximum number of Q&A model turns (each may issue read-only tool fetches)
# before the Q&A loop is forced to end. Bounds the tool-fetch loop (R18.4).
MAX_QA_TURNS = 3

# Tools that would mutate/commit a trade or suspend the run. They are disabled
# in Q&A mode so the committed Declared_Trade can never be altered (R18.6).
QA_FORBIDDEN_TOOLS = {"declare_trade", "watch_price_condition"}


def _is_system_message(message) -> bool:
    """True when ``message`` is a system instruction message."""
    if isinstance(message, SystemMessage):
        return True
    return (
        getattr(message, "role", None) == "system"
        or getattr(message, "type", None) == "system"
    )


def build_qa_context(state: AgentState) -> dict:
    """Assemble the Session_Analysis_Context that grounds a Q&A answer (R18.1).

    Reads the persisted state for the thread (the MemorySaver-checkpointed
    `decision` + its defensibility record, plus the latest Analysis_Tool results
    in `messages`) and projects it into a compact, JSON-serializable context. The
    context cites only recorded values — nothing is fabricated (R18.4).

    ``has_declared_trade`` is True only for an actionable BUY/SELL committed via
    ``declare_trade``; a HOLD or an absent decision means no trade has been
    declared yet, which the prompt must disclose (R18.3).
    """
    messages = state.get("messages") or []
    decision = state.get("decision")
    decision = decision if isinstance(decision, dict) else None
    record = decision.get("defensibility") if decision else None
    record = record if isinstance(record, dict) else {}
    results = _latest_tool_results(messages)

    action = _normalize_action(decision.get("action")) if decision else None
    has_declared_trade = bool(
        decision
        and decision.get("source") == "declare_trade"
        and action in ("BUY", "SELL")
    )

    levels = record.get("levels")

    # ── Fall back to the GATHERED tool data when the defensibility record is
    #    absent or thin (R18.1). The defensibility record is only populated by a
    #    committed declare_trade; when the run ended in a HOLD / stand-aside / no
    #    trade (or every context tool was unavailable), that record is empty even
    #    though the analysis tools DID return usable data into `messages`. Q&A
    #    must still be able to answer about the S/R levels, patterns, multi-TF
    #    trend, regime, relative strength, forecast, volume profile, options,
    #    order flow, session, and news that were actually gathered. So each named
    #    field prefers the recorded value and falls back to the latest tool
    #    result, and the full gathered analysis is surfaced under
    #    `gathered_analysis` as the ground truth the answer may cite.
    def _first_present(*vals):
        for v in vals:
            if v not in (None, {}, []):
                return v
        return None

    multi = results.get("get_multi_tf_trend")
    multi = multi if isinstance(multi, dict) else {}

    trend_1d = _first_present(record.get("trend_1d"), multi.get("trend_1d"))
    multi_tf_bias = record.get("multi_tf_bias")
    if not multi_tf_bias and multi:
        multi_tf_bias = {
            k: multi.get(k) for k in ("trend_1h", "trend_4h", "trend_1d") if k in multi
        } or None

    support_resistance = _first_present(
        record.get("support_resistance"), results.get("get_support_resistance")
    )

    patterns = record.get("patterns")
    if not patterns:
        # Gather every high-confidence pattern seen across timeframes this run.
        patterns = _collect_high_confidence_patterns(messages)

    news_sentiment = record.get("news_sentiment")
    if news_sentiment is None:
        news = results.get("get_news_context")
        if isinstance(news, dict):
            news_sentiment = news.get("sentiment_summary") or news.get("label")

    # The full latest-per-tool analysis payloads, so Q&A grounds in the real
    # gathered data regardless of whether a trade was declared. `get_candles` is
    # excluded from the embedded blob (raw OHLCV is bulky and re-fetchable); its
    # presence is still reflected in `available_tool_results`.
    gathered_analysis = {
        name: payload
        for name, payload in results.items()
        if name != "get_candles"
    }

    return {
        "has_declared_trade": has_declared_trade,
        "action": action,
        "conviction_score": decision.get("conviction_score") if decision else None,
        "execution_plan": decision.get("execution_plan") if decision else None,
        "setup_validation": decision.get("setup_validation") if decision else None,
        # Recorded level rationale used to answer "why this level?" (R18.2).
        "levels": levels if isinstance(levels, dict) else None,
        "risk_reward": record.get("risk_reward"),
        "volatility_basis": record.get("volatility_basis"),
        "atr_14": _first_present(
            record.get("atr_14"),
            (results.get("get_consensus_report") or {}).get("indicators", {}).get("atr_14")
            if isinstance(results.get("get_consensus_report"), dict) else None,
        ),
        "trend_1d": trend_1d,
        "multi_tf_bias": multi_tf_bias,
        "support_resistance": support_resistance,
        "patterns": patterns or [],
        "predictive_conflict": record.get("predictive_conflict"),
        "macro_trend_conflict": record.get("macro_trend_conflict"),
        "news_sentiment": news_sentiment,
        "defensibility_summary": record.get("summary"),
        # Which tools have already returned usable data this thread — the model
        # may re-call any of these (read-only) to fill a gap (R18.4).
        "available_tool_results": sorted(results.keys()),
        # The actual latest data each Analysis_Tool returned this session (consensus
        # indicators, regime, relative strength, forecast, volume profile, options,
        # order flow, session, S/R, patterns, multi-TF trend). This is ground truth
        # the Q&A answer may cite even when no trade was declared.
        "gathered_analysis": gathered_analysis,
    }


def build_qa_system_prompt(context: dict) -> str:
    """Build the grounding system prompt for a Trade_QA_Mode turn (R18.1-R18.6).

    The prompt embeds the recorded Session_Analysis_Context and the answering
    rules: answer from the recorded context (R18.1); when asked why a specific
    level was chosen, cite the recorded entry/SL/TP, Risk_Reward_Ratio and
    volatility basis (R18.2); when no trade has been declared, answer from
    context and say so (R18.3); when data is missing, call the relevant read-only
    tool or state it is unavailable — never fabricate (R18.4); and never alter
    the committed trade (R18.6).
    """
    try:
        context_json = json.dumps(context, indent=2, default=str)
    except Exception:
        context_json = str(context)

    if context.get("has_declared_trade"):
        trade_clause = (
            "A Declared_Trade EXISTS for this session. When the user asks why a "
            "specific level (entry, stop-loss, or take-profit) was chosen, you MUST "
            "cite the recorded entry/stop-loss/take-profit, the Risk_Reward_Ratio, "
            "and the volatility basis (ATR) from the context above — do not invent "
            "new numbers."
        )
    else:
        trade_clause = (
            "The session's committed outcome was a HOLD / stand-aside (no BUY or SELL "
            "was committed). This does NOT mean the analysis is empty: the "
            "`gathered_analysis` block above holds the actual data the tools returned "
            "this session (consensus indicators, market regime, relative strength, "
            "forecast, volume profile, support/resistance, chart patterns, "
            "multi-timeframe trend, order flow, session context, and news). "
            "ANSWER THE USER'S ACTUAL QUESTION FIRST AND DIRECTLY, grounded in "
            "`gathered_analysis` and the recorded fields, citing concrete values. Do "
            "NOT preface or pad your answer with a 'no trade has been declared' / "
            "'the decision was HOLD' disclaimer UNLESS the user is specifically asking "
            "about the trade, the decision, the entry/stop/target, or why no trade was "
            "taken. For a question about anything else (e.g. which tool gave which "
            "value, what a level was, what an indicator read), just answer that "
            "question — the trade status is irrelevant and must not lead the reply. "
            "Only say a specific datum is 'not recorded' if it is genuinely absent from "
            "BOTH the recorded fields AND `gathered_analysis`."
        )

    return (
        "You are Alpha-Quant in Trade Q&A mode. The user is asking follow-up "
        "questions about a COMPLETED analysis for this session. Your job is to "
        "explain and defend the recorded analysis — NOT to run a new analysis or "
        "change anything.\n\n"
        "RECORDED SESSION ANALYSIS CONTEXT (the only ground truth you may cite):\n"
        f"{context_json}\n\n"
        "RULES:\n"
        "1. Answer ONLY from the recorded context above, which includes both the "
        "committed-trade fields AND the `gathered_analysis` block (the real data "
        "every Analysis_Tool returned this session). Ground every factual claim "
        "(levels, RR, ATR, trend bias, patterns, sentiment, regime, relative "
        "strength, forecast, volume profile, order flow, session, options) in that "
        "context. Do NOT report a value as missing if it is present in "
        "`gathered_analysis`.\n"
        f"2. {trade_clause}\n"
        "3. If the user asks something that is NOT in the context, you may call "
        "ONE relevant read-only market-data tool (get_consensus_report, "
        "get_candles, get_multi_tf_trend, get_chart_patterns, "
        "get_support_resistance, get_news_context) to fetch it. If you cannot "
        "obtain it, say the data is unavailable. NEVER fabricate an answer.\n"
        "4. The committed trade is IMMUTABLE here. Do NOT call declare_trade or "
        "watch_price_condition — they are disabled in Q&A mode. You cannot change "
        "the committed decision.\n"
        "5. Be concise and specific. Quote the recorded numbers when relevant. "
        "Answer the SPECIFIC question the user asked — do NOT open the reply with the "
        "trade/decision status (e.g. 'no trade has been declared / HOLD') unless the "
        "question is actually about the trade or the decision."
    )


def qa_node(state: AgentState):
    """Answer a Trade_QA_Mode question grounded in the Session_Analysis_Context.

    Builds the grounding prompt from the persisted context (R18.1), invokes the
    model, and classifies any tool calls it emits. Read-only market-data calls
    are allowed (so a gap can be filled, R18.4); ``declare_trade`` and
    ``watch_price_condition`` are reclassified as forbidden and answered with a
    synthetic refusal so they are NEVER executed (R18.6).

    Crucially, this node returns ONLY a ``messages`` update (plus the bounded
    ``qa_turns`` counter); it never returns a ``decision`` update, so the
    committed Declared_Trade in state is left untouched (R18.5, R18.6).
    """
    messages = state.get("messages") or []
    symbol = state.get("symbol", "N/A")
    print(f"\n[Deep Quant Q&A] === Trade Q&A Turn (Symbol: {symbol}) ===")

    context = build_qa_context(state)
    system_prompt = build_qa_system_prompt(context)

    # Drop any FIND/VERIFY system message left in history; ground this turn on
    # the Q&A system prompt while preserving the full conversation (R18.5).
    convo = [m for m in messages if not _is_system_message(m)]
    # Flatten the PRIOR analysis tool exchange to plain text so a paused thread's
    # orphaned function call (e.g. watch_price_condition) can never be replayed
    # to a strict OpenAI-compatible provider — which would otherwise reject it
    # with "400 No tool output found for function call ...". The current turn's
    # in-turn tool call/output pair is preserved intact.
    convo = flatten_prior_tool_history(convo)
    llm_messages = [SystemMessage(content=system_prompt)] + list(convo)

    # Honor a user-selected model override for the Q&A turn (full tool binding so
    # read-only Analysis_Tools remain available; declare_trade / watch are
    # refused downstream by qa_tool_node). Falls back to the default binding.
    _qa_model = state.get("model")
    incoming_qa_turns = state.get("qa_turns") or 0

    # On the FINAL permitted Q&A turn, force an answer-only turn: invoke the base
    # model with NO tools bound plus an explicit synthesize-now directive. Without
    # this, the model could spend the last budgeted turn on yet another tool call;
    # `qa_should_continue` then routes to "end" with that call still pending, so
    # the graph terminates on an AIMessage that carries a tool call but NO
    # natural-language content. That streamed only a `TOOL_CALL_START`
    # (`> get_candles…`) and no REASONING answer — leaving the UI frozen on the
    # tool-activity line with an empty answer. A no-tools binding guarantees this
    # last turn produces text and no further tool call, so the Q&A ALWAYS ends
    # with a real answer grounded in whatever was already fetched.
    is_final_qa_turn = incoming_qa_turns >= MAX_QA_TURNS - 1

    if is_final_qa_turn:
        final_directive = SystemMessage(
            content=(
                "You have reached the end of the Q&A tool-fetch budget. Do NOT request "
                "any more tools. Answer the user's question NOW, in natural language, "
                "using the information already gathered in this conversation. If some "
                "data was unavailable, say so honestly and answer with what you have."
            )
        )
        # No-tools binding (per-run creds) so the model must respond with text.
        response = _base_llm_for_run().invoke(llm_messages + [final_directive])
    else:
        _qa_llm = (
            _build_profile_llm_for_model(_qa_model.strip(), is_fno=True)
            if isinstance(_qa_model, str) and _qa_model.strip()
            else _build_profile_llm_for_model(model_name, is_fno=True)
        )
        response = _qa_llm.invoke(llm_messages)

    extraction = extract_tool_calls(response)

    statuses: dict = {}
    synthetic: dict = {}
    for c in extraction.calls:
        status = c.status
        if status == "ok" and c.name in QA_FORBIDDEN_TOOLS:
            # Refuse trade-mutating / run-suspending tools in Q&A mode (R18.6).
            status = "qa_forbidden"
            synthetic[c.id] = (
                f"'{c.name}' is disabled in Trade Q&A mode: the committed trade is "
                f"immutable here. Answer the user's question using the recorded "
                f"analysis context instead."
            )
        elif status != "ok":
            synthetic[c.id] = _synthetic_failure_content(c)
        statuses[c.id] = status

    response.tool_calls = [
        {"name": c.name, "args": c.args or {}, "id": c.id} for c in extraction.calls
    ]
    response.additional_kwargs["_extraction_status"] = statuses
    response.additional_kwargs["_synthetic_results"] = synthetic

    allowed = [c for c in extraction.calls if statuses.get(c.id) == "ok"]
    refused = [c for c in extraction.calls if statuses.get(c.id) != "ok"]
    if extraction.calls:
        print(
            f"[Deep Quant Q&A] Tool calls -> allowed (read-only): {[c.name for c in allowed]}, "
            f"refused/failed: {[(c.name, statuses.get(c.id)) for c in refused]}"
        )

    qa_turns = (state.get("qa_turns") or 0) + 1
    # NEVER return a "decision" update — the Declared_Trade is immutable (R18.6).
    return {"messages": [response], "qa_turns": qa_turns}


def qa_tool_node(state: AgentState):
    """Execute only read-only tool calls for a Q&A turn; refuse the rest (R18.4, R18.6).

    Allowed (``ok``) market-data calls are dispatched to the real tools so the
    model can fill a context gap. Forbidden / malformed calls are answered with a
    synthetic feedback message so every call is resolved and the loop continues.
    This node NEVER sets ``state["decision"]`` — the committed trade cannot be
    altered while answering questions (R18.6).
    """
    last_message = state["messages"][-1]
    all_calls = list(getattr(last_message, "tool_calls", None) or [])
    statuses = (last_message.additional_kwargs or {}).get("_extraction_status", {})
    synthetic = (last_message.additional_kwargs or {}).get("_synthetic_results", {})

    ok_calls = [tc for tc in all_calls if statuses.get(tc["id"], "ok") == "ok"]
    other_calls = [tc for tc in all_calls if statuses.get(tc["id"], "ok") != "ok"]

    out_messages: List[BaseMessage] = []

    if ok_calls:
        temp_message = AIMessage(content="", tool_calls=ok_calls)
        result = _base_tool_node.invoke({"messages": [temp_message]})
        out_messages.extend(result["messages"])

    for tc in other_calls:
        content = synthetic.get(
            tc["id"],
            f"Tool-call '{tc.get('name')}' could not be executed in Q&A mode.",
        )
        print(f"[Deep Quant Q&A] Synthetic feedback for refused/failed call '{tc.get('name')}' ({statuses.get(tc['id'])}).")
        out_messages.append(
            ToolMessage(content=content, tool_call_id=tc["id"], name=tc.get("name") or "unknown_tool")
        )

    # No "decision" key in the update: the committed trade is immutable (R18.6).
    return {"messages": out_messages}


def qa_should_continue(state: AgentState) -> str:
    """Route a Q&A turn: fetch read-only data if requested, else end (R18.4).

    If the latest Q&A message issued any tool calls and the Q&A turn budget is
    not yet exhausted, route to the Q&A tools node to resolve them; otherwise the
    answer is final and the run ends. Bounded by ``MAX_QA_TURNS`` so the
    tool-fetch loop always terminates.
    """
    messages = state["messages"]
    last_message = messages[-1]
    all_calls = list(getattr(last_message, "tool_calls", None) or [])
    qa_turns = state.get("qa_turns", 0)

    print(f"\n[Deep Quant Q&A Routing] qa_turns={qa_turns}/{MAX_QA_TURNS}, pending calls={[tc.get('name') for tc in all_calls]}")

    if all_calls and qa_turns < MAX_QA_TURNS:
        print("[Deep Quant Q&A Routing] Routing to -> qa_tools (resolve read-only fetches)")
        return "tools"
    print("[Deep Quant Q&A Routing] Q&A answer final. Routing to -> end")
    return "end"


def route_entry(state: AgentState) -> str:
    """Select the entry node: the Q&A handler in QA mode, the Research_Phase in
    DEBATE mode, else the normal FIND/VERIFY analysis loop.

    A request with ``mode == "QA"`` reuses the same thread_id and answers from
    the persisted Session_Analysis_Context without re-running analysis (R18.1).

    A request with ``mode == "DEBATE"`` enters the Research_Phase (R1.2) — which
    reuses the existing ``agent`` analysis loop with declaration suppressed —
    returning a DISTINCT routing string (``DEBATE_RESEARCH_ENTRY``) so the
    DEBATE entry is distinguishable at the routing layer. The conditional entry
    point maps that string to the ``agent`` node. DEBATE is the ONLY trigger for
    the debate; nothing runs it implicitly (R1.4).

    FIND / VERIFY resolve to the ``agent`` analysis loop and QA to ``qa_agent``
    exactly as before — these legacy branches are byte-identical (R1.3, R5.4).
    """
    mode = (state.get("mode") or "").strip().upper()
    if mode == QA_MODE:
        print("[Deep Quant Routing] mode=QA -> entering Trade Q&A handler.")
        return "qa_agent"
    if mode == DEBATE_MODE:
        print("[Deep Quant Routing] mode=DEBATE -> entering Research_Phase.")
        return DEBATE_RESEARCH_ENTRY
    return "agent"


# ── Graph Assembly ──────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

# Add the main agent and tool execution nodes
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)
workflow.add_node("force_hold", force_hold)
# Adaptive Opportunity Engine bounded-hunt terminal (adaptive-opportunity-engine).
# Reached from `should_continue` when the Watch_Cap / Session_Budget is exhausted;
# commits a terminal stand-aside decision instead of arming another watch.
workflow.add_node("force_terminal", force_terminal)

# ── DEBATE debate-role nodes (multi-agent-debate) ────────────────────────────
# The Research_Phase (the reused `agent`+`tools` loop with declaration
# suppressed) hands off to the Bull/Bear/Judge debate via the `bull` node.
#
# TASK 7.1 (this task) implements the real Bull_Agent and Bear_Agent: each
# consumes the Shared_Evidence (the ToolMessages already in state["messages"] —
# no re-gathering), is bound to the READ-ONLY tool set (so it cannot commit or
# suspend a trade), and emits a structured Debate_Stance via
# ``debate.parse_stance``. Neither ever sets ``state["decision"]`` — only the
# Judge commits (R3.5, R12.1).
#
# STILL OUTSTANDING: TASK 8.1 adds the Judge node + `route_debate` and the
# round-looping (bull → bear → judge → [next round | finalize]); TASK 15.1
# finalizes the edge wiring (replacing the temporary `bear → __end__` edge).
# TASK 7.1 (this task) replaces the placeholder body in place with the real
# Bull_Agent and adds a `bear_node`. TASK 8.1 adds `route_debate` + the Judge
# node and the round-looping (bull → bear → judge → [next round | finalize]);
# TASK 15.1 finalizes the edge wiring. Replace the function body in place — do
# NOT re-`add_node("bull", ...)` (LangGraph rejects a duplicate node name).


# ── Debate role helpers (multi-agent-debate, R2.2/R2.3/R3.1-R3.6) ─────────────
def _collect_shared_evidence(messages) -> List[str]:
    """Collect the Shared_Evidence from the gathered ToolMessages, in order.

    The Bull/Bear roles consume the evidence already gathered in the
    Research_Phase (the ToolMessages in ``state["messages"]``) verbatim — they do
    NOT re-run the tool-gathering loop (R2.3). Each usable ToolMessage is rendered
    as a ``<tool_name>: <content>`` line so it can be threaded into the role
    prompt as text (avoiding tool/assistant message-ordering constraints).
    """
    evidence: List[str] = []
    for m in messages or []:
        if not _is_tool_message(m):
            continue
        name = getattr(m, "name", None) or "tool"
        content = getattr(m, "content", "")
        if content is None:
            content = ""
        text = content if isinstance(content, str) else str(content)
        text = text.strip()
        if text:
            evidence.append(f"{name}: {text}")
    return evidence


def _extract_stance_payload(raw_content):
    """Extract the stance JSON object from a role response's content.

    The role is asked to emit a single JSON object (lean / strength / arguments /
    biggest_risk). Real models often wrap it in prose or markdown fences, so we
    extract the first brace-balanced object and hand THAT to ``parse_stance``;
    when none is found we pass the raw content through (``parse_stance`` then
    degrades to an unavailable stance). Never raises.
    """
    if not isinstance(raw_content, str):
        return raw_content
    extracted = _extract_balanced_json(raw_content, 0)
    return extracted if extracted is not None else raw_content


_BULL_ROLE_PROMPT = """You are the BULL analyst in an adversarial trading debate.

Your job: argue the STRONGEST possible LONG case for the symbol using ONLY the shared evidence provided below. Do NOT fabricate any market data — cite only values that appear in the evidence. If the evidence is weak for a long, say so honestly and score your strength low; never invent support.

You are an analyst, not an executor: you must NOT attempt to commit, declare, or schedule a trade. Output your stance as a SINGLE JSON object and nothing else:
{
  "lean": "long" | "short" | "neutral",
  "strength": <integer 0-100, how compelling the long case is>,
  "arguments": ["concise evidence-grounded point", ...],
  "biggest_risk": "the single biggest risk to YOUR long thesis"
}"""

_BEAR_ROLE_PROMPT = """You are the BEAR analyst in an adversarial trading debate.

Your job: argue the STRONGEST possible SHORT / NO-TRADE case for the symbol using ONLY the shared evidence provided below. Do NOT fabricate any market data — cite only values that appear in the evidence. If the evidence actually favors a long, say so honestly and score your strength low; never invent bearish support.

You are an analyst, not an executor: you must NOT attempt to commit, declare, or schedule a trade. Output your stance as a SINGLE JSON object and nothing else:
{
  "lean": "long" | "short" | "neutral",
  "strength": <integer 0-100, how compelling the short / no-trade case is>,
  "arguments": ["concise evidence-grounded point", ...],
  "biggest_risk": "the single biggest risk to YOUR short / no-trade thesis"
}"""


def _run_debate_role(role: str, state: AgentState, system_prompt: str) -> dict:
    """Run one Bull/Bear turn over the Shared_Evidence and return a state update.

    Shared by ``bull_node`` and ``bear_node``: it gathers the Shared_Evidence,
    threads in the opposing prior-round stance when more than one round is run
    (R3.6), invokes the read-only-bound role LLM, parses the structured stance
    via ``debate.parse_stance``, stores ``stance_to_dict(stance)`` into
    ``bull_stance`` / ``bear_stance``, and increments ``debate_turns``. It NEVER
    sets ``state["decision"]`` — only the Judge commits (R3.5, R12.1).
    """
    role_norm = (role or "").strip().lower()
    evidence = _collect_shared_evidence(state.get("messages"))
    evidence_block = (
        "\n".join(f"- {line}" for line in evidence)
        if evidence else "(no usable shared evidence was gathered)"
    )

    # Round threading (R3.6): from round 2 onward, give each role the opposing
    # side's most recent stance to rebut. The Bull rebuts the prior Bear stance;
    # the Bear rebuts the (just-produced) Bull stance.
    debate_round = state.get("debate_round") or 1
    threading_block = ""
    if role_norm == "bull":
        prior = state.get("bear_stance")
        if prior and debate_round > 1:
            threading_block = (
                "\n\nThe BEAR argued the following in the prior round — rebut its "
                f"strongest points where the evidence lets you:\n{json.dumps(prior)}"
            )
    elif role_norm == "bear":
        prior = state.get("bull_stance")
        if prior:
            threading_block = (
                "\n\nThe BULL argued the following — rebut its strongest points "
                f"where the evidence lets you:\n{json.dumps(prior)}"
            )

    human = HumanMessage(
        content=(
            f"SHARED EVIDENCE (gathered once in the research phase — argue over "
            f"this, do not request more):\n{evidence_block}{threading_block}\n\n"
            f"Now emit your stance as a single JSON object."
        )
    )

    role_llm = get_role_llm(role_norm)
    try:
        response = role_llm.invoke([SystemMessage(content=system_prompt), human])
        raw_content = getattr(response, "content", "") or ""
    except Exception as e:
        # A role failure must not crash the debate: emit an unavailable stance so
        # the Judge can proceed on the remaining evidence (R12.2). parse_stance(
        # role, None) yields available=False.
        print(f"[Deep Quant Debate] {role_norm} role invocation failed: {e}")
        raw_content = None

    payload = _extract_stance_payload(raw_content)
    stance = parse_stance(role_norm, payload)
    stance_dict = stance_to_dict(stance)
    print(
        f"[Deep Quant Debate] {role_norm} stance — lean={stance.lean} "
        f"strength={stance.strength} available={stance.available} "
        f"(round {debate_round})."
    )

    update: dict = {
        "debate_turns": (state.get("debate_turns") or 0) + 1,
        # Stay in the debate phase; the Judge node (task 8.1) will finalize.
        "phase": "debate",
    }
    # Surface this role's reasoning as a distinct, role-tagged REASONING event in
    # the glass-box stream (multi-agent-debate, R8.1). The Bull/Bear are READ-ONLY
    # analysts, so the appended message carries NO executable tool_calls — it is a
    # pure-reasoning AIMessage whose content is the role's stance text, tagged with
    # the producing role in ``additional_kwargs["role"]`` so ``stream_events`` can
    # label it ("bull" / "bear"). A failed invocation (``raw_content is None``)
    # appends no message, leaving the message history unchanged. Because the
    # message has no tool_calls it needs no paired ToolMessage and stays valid for
    # the ``add_messages`` reducer (R3.5, R12.1 — it never sets a decision).
    if isinstance(raw_content, str) and raw_content.strip():
        update["messages"] = [
            AIMessage(content=raw_content, additional_kwargs={"role": role_norm})
        ]
    if role_norm == "bull":
        update["bull_stance"] = stance_dict
    elif role_norm == "bear":
        update["bear_stance"] = stance_dict
    # NEVER set update["decision"] here — only the Judge commits (R3.5, R12.1).
    return update


def bull_node(state: AgentState):
    """Bull_Agent — argue the strongest long case over the Shared_Evidence (R3.1).

    Consumes the ToolMessages already gathered in the Research_Phase (no
    re-gathering), emits a structured Debate_Stance parsed by
    ``debate.parse_stance``, stores it in ``bull_stance``, and increments
    ``debate_turns``. Bound to the read-only tool set, so it cannot call
    ``declare_trade`` / ``watch_price_condition`` and never sets a decision
    (R3.5, R12.1).
    """
    # Initialize the 1-based round counter on first entry into the debate so the
    # round-threading logic and the Judge's round-looping (task 8.1) have a
    # defined starting round (R3.6, R6.1).
    #
    # The round index is derived DETERMINISTICALLY from the bounded turn counter
    # so the round-looping (`route_debate` -> back to `bull`) is idempotent:
    # before the Bull turn of round k, exactly (k-1) full Bull+Bear rounds (each
    # TURNS_PER_ROUND turns) have completed, so
    # ``round = (debate_turns // TURNS_PER_ROUND) + 1``. This is correct for the
    # first entry (debate_turns == 0 -> round 1) and every subsequent round.
    turns_done = state.get("debate_turns") or 0
    current_round = (turns_done // TURNS_PER_ROUND) + 1
    # Reflect the resolved round for this turn's threading decisions (R3.6).
    state = {**state, "debate_round": current_round}
    update: dict = {"debate_round": current_round}
    update.update(_run_debate_role("bull", state, _BULL_ROLE_PROMPT))
    return update


def bear_node(state: AgentState):
    """Bear_Agent — argue the strongest short / no-trade case (R3.2).

    Consumes the same Shared_Evidence as the Bull (no re-gathering), is given the
    Bull's stance to rebut (R3.6), emits a structured Debate_Stance parsed by
    ``debate.parse_stance``, stores it in ``bear_stance``, and increments
    ``debate_turns``. Bound to the read-only tool set, so it cannot call
    ``declare_trade`` / ``watch_price_condition`` and never sets a decision
    (R3.5, R12.1).
    """
    return _run_debate_role("bear", state, _BEAR_ROLE_PROMPT)


# ── VERIFY-mode devil's advocate (multi-agent-debate, R11) ────────────────────
# In VERIFY (co-pilot verification) mode the existing single-agent risk-manager
# verdict path is AUGMENTED — not replaced — with the Bear_Agent run as an
# explicit DEVIL'S ADVOCATE against the user-proposed trade (R11.1). Its stance
# is surfaced as an AIMessage in the verification reasoning so the verdict path
# weighs it (R11.2); it cites only the gathered Shared_Evidence and is told never
# to fabricate (R11.4). It NEVER itself commits or blocks a trade: it is bound to
# the READ-ONLY tool set via ``get_role_llm("bear")`` (no ``declare_trade`` /
# ``watch_price_condition``) and ``run_verify_devils_advocate`` returns ONLY a
# message — it never sets ``state["decision"]`` — so the existing VERIFY verdict
# path remains the sole decision authority (R11.3). FIND / DEBATE / QA are
# unaffected because this only runs on a VERIFY-mode turn.
_VERIFY_DEVILS_ADVOCATE_PROMPT = """You are the BEAR analyst acting as an explicit DEVIL'S ADVOCATE in co-pilot trade verification.

A trader has proposed the following trade and wants it genuinely stress-tested:
- Side: {side}
- Symbol: {symbol}
- Entry: {entry}
- Stop-loss: {stop_loss}
- Take-profit: {take_profit}
- Trader's notes: {user_analysis}

Your job: argue the STRONGEST possible case that THIS PROPOSED TRADE IS WRONG — that the trader should NOT take it as specified — using ONLY the shared evidence provided below. Attack the weakest links: poor entry location, an unsafe or too-tight stop, an unrealistic target / weak Risk:Reward, conflict with the macro trend, the volume-profile structure, the regime/relative-strength/forecast/session context, or the realized track record. Do NOT fabricate any market data — cite only values that appear in the evidence. If the evidence genuinely supports the trade, say so honestly and score your strength low; never invent objections.

You are an analyst and a devil's advocate, NOT an executor: you must NOT attempt to commit, declare, block, approve, or schedule a trade. The verification verdict is decided by the risk-manager path, not by you. Output your stance as a SINGLE JSON object and nothing else:
{{
  "lean": "long" | "short" | "neutral",
  "strength": <integer 0-100, how compelling the case AGAINST the proposed trade is>,
  "arguments": ["concise evidence-grounded objection to the proposed trade", ...],
  "biggest_risk": "the single biggest risk to YOUR case against the trade (i.e. why the trade might actually be right)"
}}"""


def run_verify_devils_advocate(state: AgentState, messages=None) -> Optional[AIMessage]:
    """Run the Bear_Agent devil's advocate against the user-proposed trade (R11).

    Builds a VERIFY-specific Bear prompt targeted at ``state["manual_trade"]``,
    invokes the READ-ONLY-bound Bear LLM (``get_role_llm("bear")`` — it cannot
    call ``declare_trade`` / ``watch_price_condition``) over the already-gathered
    Shared_Evidence, parses the structured stance via ``debate.parse_stance``,
    and returns an ``AIMessage`` carrying that stance so it is surfaced in the
    verification reasoning and informs the verdict (R11.1, R11.2, R11.4).

    Returns ONLY a message and NEVER sets ``state["decision"]`` — the existing
    VERIFY verdict path remains the sole decision authority (R11.3). Never raises:
    any failure yields an unavailable stance message so verification proceeds.
    """
    src_messages = messages if messages is not None else state.get("messages")
    trade = state.get("manual_trade") or {}

    evidence = _collect_shared_evidence(src_messages)
    evidence_block = (
        "\n".join(f"- {line}" for line in evidence)
        if evidence else "(no usable shared evidence was gathered yet)"
    )

    system_prompt = _VERIFY_DEVILS_ADVOCATE_PROMPT.format(
        side=trade.get("side", "N/A"),
        symbol=state.get("symbol", "N/A"),
        entry=trade.get("entry", "N/A"),
        stop_loss=trade.get("stop_loss", "N/A"),
        take_profit=trade.get("take_profit", "N/A"),
        user_analysis=trade.get("user_analysis", "None"),
    )
    human = HumanMessage(
        content=(
            "SHARED EVIDENCE (gathered during verification — argue over THIS, do "
            f"not request more and do not fabricate):\n{evidence_block}\n\n"
            "Now emit your devil's-advocate stance against the proposed trade as a "
            "single JSON object."
        )
    )

    role_llm = get_role_llm("bear")
    try:
        response = role_llm.invoke([SystemMessage(content=system_prompt), human])
        raw_content = getattr(response, "content", "") or ""
    except Exception as e:
        # A failure must not crash verification: degrade to an unavailable stance
        # so the verdict path proceeds on the remaining evidence (R11.4, R12.2).
        print(f"[Deep Quant Verify] devil's-advocate invocation failed: {e}")
        raw_content = None

    payload = _extract_stance_payload(raw_content)
    stance = parse_stance("bear", payload)
    stance_dict = stance_to_dict(stance)
    print(
        f"[Deep Quant Verify] devil's-advocate stance - lean={stance.lean} "
        f"strength={stance.strength} available={stance.available}."
    )

    # Surface the stance as readable reasoning + the structured JSON so the
    # verdict path can both read it and the glass-box stream can tag it (a `role`
    # tag is attached for the role-tagged-reasoning step, task 12.1). This is a
    # plain AIMessage with NO tool calls, so it never executes/commits anything.
    devils_msg = AIMessage(
        content=(
            "DEVIL'S ADVOCATE (Bear) - the strongest evidence-grounded case AGAINST "
            "the proposed trade, for you to weigh in your verification verdict. This "
            "stance does NOT itself approve, block, or commit the trade.\n"
            f"{json.dumps(stance_dict)}"
        )
    )
    # Tag the message so downstream role-tagged-reasoning surfacing (task 12.1)
    # can distinguish it; harmless to any consumer that ignores additional_kwargs.
    try:
        devils_msg.additional_kwargs["role"] = "bear"
        devils_msg.additional_kwargs["verify_devils_advocate"] = True
    except Exception:
        pass
    return devils_msg


def _should_run_verify_devils_advocate(state: AgentState, mode: str, messages) -> bool:
    """True only on a VERIFY run, once, after the Shared_Evidence is available.

    Gated so the devil's advocate runs EXACTLY ONCE per VERIFY run and only after
    at least one market-data Analysis_Tool has returned data — so it argues over
    real gathered evidence rather than nothing (R11.1, R11.4). Returns False for
    every non-VERIFY run, leaving FIND / DEBATE / QA completely unchanged (R11 is
    VERIFY-only).
    """
    if (mode or "").strip().upper() != "VERIFY":
        return False
    if state.get("verify_devils_advocate_done"):
        return False
    return bool(state.get("market_data_seen")) or _market_data_seen(messages)


# ── Judge node + bounded debate sequencing (multi-agent-debate, task 8.1) ─────
_JUDGE_ROLE_PREAMBLE = """You are the JUDGE in an adversarial Bull/Bear trading debate.

The Research_Phase has ALREADY gathered the shared evidence (provided below as tool results), and the Bull and Bear analysts have each argued their strongest case over THAT SAME evidence. Your job is to WEIGH both stances against the shared evidence and decide.

You are the ONLY role permitted to commit a trade. When you are ready, call `declare_trade` (BUY / SELL / HOLD). You MUST apply the FULL <self_verification_protocol> below before committing — the same hard-risk discipline used for a single-agent decision, and the Trade_Validator remains authoritative on your declaration.

A deterministic synthesis of the two stances (consensus, conviction, advisory bias) is given to you below. A `contested` consensus MUST bias you toward a HOLD or a reduced-size decision; use the computed conviction as the anchor for your conviction_score.

You MAY issue at most {judge_max_tool_calls} targeted READ-ONLY analysis-tool call(s) to resolve a single clarification before declaring — do NOT re-run the whole research loop, and do NOT fabricate data. Do NOT call `watch_price_condition`; if no A+ trade is defensible, declare a HOLD.

"""


def _build_judge_prompt(state: AgentState) -> str:
    """Build the Judge system prompt: a Judge preamble + the unchanged
    self-verification protocol used for a single-agent decision (R4.7).

    Reuses ``format_system_prompt`` so the Judge applies the exact same
    <self_verification_protocol> / declare_trade discipline as the standard
    analysis loop, prefixed with the adversarial-debate framing and the bounded
    read-only tool-call allowance (R2.4).
    """
    try:
        cfg = resolve_debate_config(model_name)
        judge_budget = cfg.judge_max_tool_calls
    except Exception:
        judge_budget = 0
    preamble = _JUDGE_ROLE_PREAMBLE.format(judge_max_tool_calls=judge_budget)
    # format_system_prompt(state) for a DEBATE run (mode != VERIFY) returns the
    # DEEP_QUANT_SYSTEM_PROMPT (which contains the full self-verification
    # protocol + declare_trade rules) plus the timeframe instruction.
    return preamble + format_system_prompt(state)


def judge_node(state: AgentState, config=None):
    """Judge_Agent — weigh both stances, set the verdict, and commit (R4.1-R4.7).

    Reconstructs the stored Bull/Bear ``DebateStance``s, classifies the
    Debate_Consensus and derives the Conviction via the pure ``debate`` core
    (R4.1, R4.4), then invokes the FULL-tool-bound Judge LLM. The Judge may issue
    at most ``judge_max_tool_calls`` targeted READ-ONLY tool calls for a single
    clarification (R2.4) before committing via ``declare_trade``. Its declaration
    flows through the UNCHANGED ``_decision_from_declare`` /
    ``_declare_was_rejected`` / ``_finalize_decision`` path, so the Trade_Validator
    stays authoritative (R4.6, R5.2) and the decision is journaled exactly like a
    single-agent decision (R5.5). If no validated trade is committed within the
    budget, a stated HOLD is finalized (R5.3). Only the Judge commits (R4.5).
    """
    cfg = resolve_debate_config(model_name)
    budget = cfg.judge_max_tool_calls
    # Bug 5: the LangGraph thread_id (if provided by the runtime) keys the
    # per-thread idempotency guard at the finalize chokepoint. Best-effort → None.
    thread_id = _thread_id_from_config(config)

    # Reconstruct the stored stances (parse_stance round-trips stance_to_dict)
    # and run the deterministic synthesis. An unavailable/missing stance is
    # treated as strength 0 by the pure core, never fabricated (R12.2).
    bull = parse_stance("bull", state.get("bull_stance"))
    bear = parse_stance("bear", state.get("bear_stance"))
    consensus = classify_consensus(bull, bear)
    conviction = derive_conviction(bull, bear, consensus)
    bias = judge_directional_bias(bull, bear, consensus)
    print(
        f"[Deep Quant Debate] judge synthesis -> consensus={consensus} "
        f"conviction={conviction} advisory_bias={bias}."
    )

    evidence = _collect_shared_evidence(state.get("messages"))
    evidence_block = (
        "\n".join(f"- {line}" for line in evidence)
        if evidence else "(no usable shared evidence was gathered)"
    )

    human = HumanMessage(
        content=(
            "SHARED EVIDENCE (gathered once in the research phase — weigh this, "
            f"do not re-gather):\n{evidence_block}\n\n"
            f"BULL stance:\n{json.dumps(state.get('bull_stance') or {})}\n\n"
            f"BEAR stance:\n{json.dumps(state.get('bear_stance') or {})}\n\n"
            "DEBATE SYNTHESIS (computed deterministically from the two stances):\n"
            f"- consensus: {consensus}\n"
            f"- conviction: {conviction} (in [0, 100])\n"
            f"- advisory directional bias: {bias}\n\n"
            "Now weigh the cases against the shared evidence, apply your "
            "self-verification protocol, and either call declare_trade (BUY / "
            "SELL / HOLD) or declare a HOLD."
        )
    )

    judge_llm = get_judge_llm()
    system_prompt = _build_judge_prompt(state)

    # Conversation the Judge LLM sees (system prompt prepended at invoke time).
    judge_msgs: List[BaseMessage] = [SystemMessage(content=system_prompt), human]
    # Messages merged back into the shared state so the Judge's reasoning, any
    # read-only clarification results, and its declaration are surfaced in the
    # stream and remain a valid (id-paired) message history.
    new_messages: List[BaseMessage] = []

    decision: Optional[dict] = None
    readonly_used = 0
    # Bounded iterations guarantee termination (R2.4, R6.2): at most `budget`
    # read-only clarification rounds, plus a declaration round, plus slack.
    max_iters = budget + 2

    for _ in range(max_iters):
        try:
            response = judge_llm.invoke(judge_msgs)
        except Exception as e:
            print(f"[Deep Quant Debate] judge invocation failed: {e}")
            break

        extraction = extract_tool_calls(response)
        all_calls = extraction.calls
        # Pair every discovered call with an id on the AIMessage so the follow-up
        # ToolMessages keep the history valid.
        response.tool_calls = [
            {"name": c.name, "args": c.args or {}, "id": c.id} for c in all_calls
        ]
        # Tag the Judge's reasoning AIMessage so its REASONING events are
        # distinguishable as the "judge" role in the glass-box stream (R8.1).
        # Additive: the role tag goes into ``additional_kwargs`` WITHOUT touching
        # ``tool_calls``, so the id-paired ToolMessage follow-ups stay valid and
        # the message history remains well-formed.
        existing_kwargs = getattr(response, "additional_kwargs", None)
        if isinstance(existing_kwargs, dict):
            existing_kwargs["role"] = "judge"
        else:
            response.additional_kwargs = {"role": "judge"}
        judge_msgs.append(response)
        new_messages.append(response)

        ok_calls = [c for c in all_calls if c.status == "ok"]
        failed_calls = [c for c in all_calls if c.status != "ok"]

        # Answer malformed calls with synthetic feedback so the Judge can self-correct.
        for c in failed_calls:
            tmsg = ToolMessage(
                content=_synthetic_failure_content(c),
                tool_call_id=c.id,
                name=c.name or "unknown_tool",
            )
            judge_msgs.append(tmsg)
            new_messages.append(tmsg)

        declare_calls = [c for c in ok_calls if c.name == "declare_trade"]
        suspend_calls = [c for c in ok_calls if c.name == "watch_price_condition"]
        readonly_calls = [
            c for c in ok_calls if c.name not in DEBATE_READONLY_EXCLUDED_TOOLS
        ]

        # The Judge may not suspend the run (only commit or HOLD): refuse any
        # watch_price_condition with feedback rather than executing it.
        for c in suspend_calls:
            tmsg = ToolMessage(
                content=(
                    "watch_price_condition is not available to the Judge: weigh the "
                    "shared evidence and either declare_trade (BUY/SELL) or declare a HOLD."
                ),
                tool_call_id=c.id,
                name="watch_price_condition",
            )
            judge_msgs.append(tmsg)
            new_messages.append(tmsg)

        # ── Declaration: route through the UNCHANGED finalize path ───────────
        if declare_calls:
            call_dicts = [
                {"name": c.name, "args": c.args or {}, "id": c.id} for c in declare_calls
            ]
            temp = AIMessage(content="", tool_calls=call_dicts)
            result = _base_tool_node.invoke({"messages": [temp]})
            declare_tmsgs = list(result["messages"])
            judge_msgs.extend(declare_tmsgs)
            new_messages.extend(declare_tmsgs)

            cand = _decision_from_declare(call_dicts)
            if cand is not None and _declare_was_rejected(declare_tmsgs):
                # The authoritative Trade_Validator rejected the trade: do NOT
                # finalize. Let the Judge revise & re-declare within budget (R4.6).
                print(
                    "[Deep Quant Debate] Judge declare_trade REJECTED by the validator; "
                    "allowing revision within the remaining budget."
                )
            elif cand is not None:
                decision = cand
                print(
                    f"[Deep Quant Debate] Judge committed decision: action={decision.get('action')}."
                )
                break
            # cand is None -> not a usable declaration; fall through and loop.
            continue

        # ── Targeted read-only clarification calls (bounded by the budget) ────
        if readonly_calls:
            for c in readonly_calls:
                if readonly_used >= budget:
                    tmsg = ToolMessage(
                        content=(
                            f"Judge read-only tool budget exhausted ({budget}). Do not "
                            "gather more data — weigh the existing shared evidence and "
                            "declare your decision (declare_trade BUY/SELL or HOLD) now."
                        ),
                        tool_call_id=c.id,
                        name=c.name,
                    )
                    judge_msgs.append(tmsg)
                    new_messages.append(tmsg)
                    continue
                temp = AIMessage(
                    content="", tool_calls=[{"name": c.name, "args": c.args or {}, "id": c.id}]
                )
                result = _base_tool_node.invoke({"messages": [temp]})
                rmsgs = list(result["messages"])
                judge_msgs.extend(rmsgs)
                new_messages.extend(rmsgs)
                readonly_used += 1
            continue

        # No actionable tool calls this turn (pure reasoning, or only failed /
        # suspend calls) -> the Judge declined to commit; stop and finalize HOLD.
        if not ok_calls:
            break

    update: dict = {
        "debate_turns": (state.get("debate_turns") or 0) + 1,
        "phase": "debate",
        "debate_consensus": consensus,
        "debate_conviction": conviction,
        "messages": new_messages,
    }

    if decision is not None:
        # Thread the stored Bull/Bear stances + Judge verdict onto the decision so
        # the single ``_finalize_decision`` chokepoint's ``build_defensibility_record``
        # can build the ``debate`` sub-entry from them (multi-agent-debate, R7.1).
        # The carrier is private (``_debate``) and is popped immediately after the
        # record is built so it never leaks into the journaled / streamed decision.
        decision["_debate"] = {
            "bull_stance": state.get("bull_stance"),
            "bear_stance": state.get("bear_stance"),
            "consensus": consensus,
            "conviction": conviction,
        }
        # Single finalize chokepoint: attach the defensibility record and journal
        # the decision exactly like a single-agent commit (R5.5).
        _finalize_decision(state, decision, thread_id=thread_id)
        decision.pop("_debate", None)
        update["decision"] = decision
        return update

    # ── No validated trade within budget -> stated HOLD (R5.3) ───────────────
    # Reuse the force_hold semantics: a stated HOLD with reason "no-decision-reached"
    # rather than a fabricated trade.
    hold_decision = {
        "action": "HOLD",
        "conviction_score": 0,
        "reason": "no-decision-reached",
        "setup_validation": (
            f"The Bull/Bear debate reached a '{consensus}' consensus (derived conviction "
            f"{conviction}); the Judge committed no validated A+ trade within the bounded "
            "debate budget. Holding to preserve capital rather than force a low-conviction trade."
        ),
        "execution_plan": "HOLD — no trade taken (the debate produced no validated setup).",
        "source": "debate_hold",
    }
    # Thread the stored Bull/Bear stances + Judge verdict onto the HOLD decision
    # too, so the debate sub-entry is built for an exhausted/declined debate as
    # well (multi-agent-debate, R7.1). A HOLD never triggers
    # ``committed_against_contested`` (R7.4 is directional-only). The carrier is
    # popped after the record is built so it never leaks downstream.
    hold_decision["_debate"] = {
        "bull_stance": state.get("bull_stance"),
        "bear_stance": state.get("bear_stance"),
        "consensus": consensus,
        "conviction": conviction,
    }
    hold_decision["defensibility"] = _finalize_decision(state, hold_decision, thread_id=thread_id)
    hold_decision.pop("_debate", None)
    new_messages.append(
        AIMessage(
            content=json.dumps(
                {
                    "conviction_score": hold_decision["conviction_score"],
                    "setup_validation": hold_decision["setup_validation"],
                    "execution_plan": hold_decision["execution_plan"],
                }
            ),
            additional_kwargs={"role": "judge"},
        )
    )
    update["decision"] = hold_decision
    update["messages"] = new_messages
    return update


def route_debate(state: AgentState) -> str:
    """Sequence the debate: bull -> bear -> (loop additional rounds) -> judge.

    Called after the Bear turn. Loops back to the Bull for another round while
    another round is configured AND the turn budget is not exhausted; otherwise
    hands off to the Judge. The strict ``debate_turns < max_turns`` bound
    guarantees termination regardless of configuration (R6.2). The next Bull turn
    derives its (incremented) round index deterministically from ``debate_turns``
    (see ``bull_node``), so no separate round-increment bookkeeping is needed here.
    """
    try:
        cfg = resolve_debate_config(model_name)
        rounds = cfg.rounds
        max_turns = cfg.max_turns
    except Exception:
        rounds, max_turns = 1, TURNS_PER_ROUND + 1

    debate_round = state.get("debate_round") or 1
    debate_turns = state.get("debate_turns") or 0

    if debate_round < rounds and debate_turns < max_turns:
        print(
            f"[Deep Quant Routing] Debate round {debate_round}/{rounds} complete "
            f"(turns={debate_turns}/{max_turns}). Routing to -> bull (next round)."
        )
        return "bull"
    print(
        f"[Deep Quant Routing] Debate rounds complete (round {debate_round}/{rounds}, "
        f"turns={debate_turns}/{max_turns}). Routing to -> judge."
    )
    return "judge"

workflow.add_node("bull", bull_node)
workflow.add_node("bear", bear_node)
workflow.add_node("judge", judge_node)

# Trade Q&A nodes (Requirement 18). They reuse the same compiled graph + the
# MemorySaver checkpointer so a QA request on an existing thread_id sees the
# persisted Session_Analysis_Context. They are wired as a separate, bounded
# sub-loop that never mutates the committed decision (R18.6).
workflow.add_node("qa_agent", qa_node)
workflow.add_node("qa_tools", qa_tool_node)

# Conditional entry: mode=QA enters the Q&A handler; mode=DEBATE enters the
# Research_Phase (which reuses the `agent` analysis loop with declaration
# suppressed); everything else runs the normal FIND/VERIFY analysis loop
# (R18.1, R1.2, R1.3). The DEBATE research-entry string maps to the same `agent`
# node, so the FIND/VERIFY/QA targets are unchanged.
workflow.set_conditional_entry_point(
    route_entry,
    {
        "qa_agent": "qa_agent",
        DEBATE_RESEARCH_ENTRY: "agent",
        "agent": "agent",
    },
)

# Define conditional route from agent to tools, suspend (watch), loop, forced
# HOLD, or terminate. "continue" and "suspend" both reach the tools node; the
# distinct labels keep the watch-suspension path explicit (R2.6).
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "tools",
        "suspend": "tools",
        "loop_agent": "agent",
        "force_hold": "force_hold",
        # Adaptive Opportunity Engine bounded-hunt terminal (Watch_Cap /
        # Session_Budget reached) — commits a terminal stand-aside decision.
        "force_terminal": "force_terminal",
        # DEBATE research-completion handoff (multi-agent-debate, R2.1). Only
        # reachable while `phase` is a DEBATE phase, so non-DEBATE runs never use
        # it. Mapped to the `bull` placeholder until tasks 7.1/8.1 wire the roles.
        DEBATE_HANDOFF: "bull",
        "end": "__end__",
    }
)

# After tools run, terminate if declare_trade committed a decision, else loop.
workflow.add_conditional_edges(
    "tools",
    route_after_tools,
    {
        "agent": "agent",
        # DEBATE research → debate handoff after a suppressed declare_trade.
        DEBATE_HANDOFF: "bull",
        "end": "__end__",
    }
)

# A forced HOLD terminates the run.
workflow.add_edge("force_hold", "__end__")

# A bounded-hunt force_terminal also terminates the run.
workflow.add_edge("force_terminal", "__end__")

# DEBATE round sequencing (multi-agent-debate, task 8.1). The Bull always hands
# off to the Bear within a round; after the Bear, `route_debate` either loops
# back to the Bull for another configured round (R3.6) or hands off to the Judge,
# strictly bounded by `debate_turns < max_turns` so the debate always terminates
# (R6.2). The Judge node finalizes internally — committing a validated
# declare_trade through the unchanged `_decision_from_declare` /
# `_declare_was_rejected` / `_finalize_decision` path, or finalizing a stated
# HOLD on budget exhaustion (R5.3) — then the run ends.
workflow.add_edge("bull", "bear")
workflow.add_conditional_edges(
    "bear",
    route_debate,
    {
        "bull": "bull",
        "judge": "judge",
    },
)
workflow.add_edge("judge", "__end__")

# ── Trade Q&A sub-loop edges (Requirement 18) ─────────────────────────────────
# qa_agent answers from the persisted context; if it requested a read-only data
# fetch, route to qa_tools and back, bounded by MAX_QA_TURNS (R18.4). Otherwise
# the answer is final and the run ends. Neither node ever sets a decision, so
# the committed trade stays immutable (R18.6).
workflow.add_conditional_edges(
    "qa_agent",
    qa_should_continue,
    {
        "tools": "qa_tools",
        "end": "__end__",
    },
)
workflow.add_edge("qa_tools", "qa_agent")

# Initialize in-memory checkpointer to persist thread states
memory = MemorySaver()

# Compile the final ReAct graph
graph = workflow.compile(checkpointer=memory)
