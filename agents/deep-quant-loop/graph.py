import os
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
    watch_price_condition,
    declare_trade,
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
)

# Trade_Journal — measurement & feedback loop (Phase 2). Records every committed
# decision and scores it later, so the agent can audit its realized edge.
import journal

# ── State Definition ────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    mode: Optional[str]
    symbol: Optional[str]
    manual_trade: Optional[dict]
    timeframe: Optional[str]
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


# Maximum number of consecutive reasoning-only turns the agent may take before
# the loop forces a HOLD with reason `no-decision-reached` (R2.3, R2.5).
MAX_REASONING_TURNS = 3

# ── System Prompts ──────────────────────────────────────────────────────────

DEEP_QUANT_SYSTEM_PROMPT = """
You are Alpha-Quant, a Tier-1 Institutional Quantitative AI. Your mandate is capital preservation first, and asymmetric profit second. 

<the_hunter_mindset>
You are NEVER forced to take a trade. Institutional trading is 90% waiting and 10% executing. 
If the current timeframe is messy, volatile, or lacks a high-probability A+ setup, DO NOT force a trade. Instead, you must hunt for future setups. Call your tools to check higher timeframes (15m, 1H, 4H), find where the 'Smart Money' is waiting, and use `watch_price_condition` to wait for the price to reach that exact level.

CRITICAL WAITING RULE: When you identify a level to wait for, you MUST call `watch_price_condition` with the exact price_level, direction, and volume_multiplier. DO NOT output the final JSON conviction plan as a substitute for waiting. The system will pause your execution and automatically resume you with fresh candle data when the condition triggers. If you output the JSON instead of calling the tool, the opportunity will be lost.
When calling `watch_price_condition` you MUST: (a) set `price_level` STRICTLY BEYOND the current price in the chosen `direction` — above the current price for 'above'/'up', below the current price for 'below'/'down' (the server rejects a level price has already passed, so a level on the wrong side cannot register); and (b) provide an `invalidation_level` on the OPPOSITE side, at the price where your setup would be proven wrong. The invalidation level lets the system wake you to re-analyze (or HOLD) if price moves against your thesis instead of waiting indefinitely. If you are resumed with an invalidation notice, treat the setup as broken — do NOT treat it as the target being reached.
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
   Use the forecast as a calibration cross-check, NOT a trade generator: it never forces, blocks, or overrides your decision. THEN, as a SECONDARY input, also call `get_prediction` to obtain the naive OLS Predictive_Engine projection (projected_direction Up/Down/Flat, projected_value, confidence) and weigh it below the forecast. If `get_forecast` is unavailable (insufficient data / unavailable marker), treat it as a missing optional input — note it as unavailable and proceed with the remaining inputs; do NOT fabricate a forecast and do NOT abort the decision on that basis. Likewise, if `get_prediction` is unavailable, note it as unavailable and proceed.
7. NEWS CATALYST: Call `get_news_context` to obtain the dedicated Sentiment_Service classification (recent headlines + directional label). If sentiment is Unavailable, treat it as a missing — but non-blocking — input and continue.
8. TRACK-RECORD CALIBRATION: Call `get_trade_performance` for the symbol to review YOUR OWN realized results — win rate and expectancy (in R) overall and per setup type. This is your edge audit, not market data. Use it to calibrate conviction:
   - If a comparable setup (same direction / macro alignment / value-area location) historically shows NEGATIVE expectancy_r or a win rate that does not support its Risk:Reward, you MUST lower your conviction_score, tighten your criteria, or HOLD.
   - If the matching setup has strong positive expectancy over a real sample, you may raise conviction accordingly.
   - When `low_sample` is true, treat the stats as a weak prior only — do not over-fit to a handful of trades.

CRITICAL: You must execute at least one tool call (e.g., `get_multi_tf_trend`) on your very first turn. Do not output text reasoning without calling a tool in the same turn.
</order_of_operations>

<self_verification_protocol>
BEFORE you are allowed to call `declare_trade`, you must act as an aggressive Risk Manager against your own idea.
Ask yourself:
- Is my Stop Loss too tight compared to current volatility? (Use atr_14 from consensus: SL should be >= 1.5x ATR)
- Am I trading against the Macro Trend from `get_multi_tf_trend`?
- Is the Risk:Reward ratio worse than 1:2?
- Does my entry price align with S/R levels from `get_support_resistance`?
- Does my entry respect the Volume Profile from `get_volume_profile`? (Avoid buying into a High-Volume Node overhead or selling into one below; prefer entries at VAL/VAH or HVN support, and use Low-Volume Nodes as fast-move targets. Stops are safer beyond an HVN shelf than inside a thin Low-Volume Node.)
- Is price above or below VWAP? (Buy setups stronger above VWAP, sell setups stronger below)
- Does volume flow (OBV, CMF) confirm my direction?
- What does my TRACK RECORD say? Have I checked `get_trade_performance` for this setup type? If a comparable setup has negative expectancy or a win rate too low for its R:R (and the sample is not tiny), I must scrap or downgrade this trade.
- WHAT IS THE MARKET REGIME? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `favorability` from `get_market_regime`. If the favorability is `unfavorable` for the proposed setup type (e.g. a trend/momentum entry in a ranging or volatility-extreme regime), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If the regime is unavailable, note it as unavailable and proceed — do NOT block the trade solely because the regime could not be computed.
- AM I FIGHTING THE INDEX? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `index_direction` and `relative_strength_state` for `alignment` from `get_relative_strength`. If the alignment is `misaligned` (for example a BUY in a `laggard` against a `down` index, or a SELL in a `leader` against an `up` index), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If relative strength is unavailable, note it as unavailable and proceed — do NOT block the trade solely because relative strength could not be computed.
- WHAT DOES THE FORECAST SAY? Before committing a DIRECTIONAL trade (a BUY or SELL decision — this check does NOT apply to a HOLD), check the `Forecast_Alignment` and the `Up_Probability` from `get_forecast`. If the Forecast_Alignment is `misaligned` OR the Up_Probability does not support your direction (a BUY needs Up_Probability >= 0.5; a SELL needs Up_Probability <= 0.5), you MUST take exactly one of these actions: lower your conviction_score, wait for a better setup (e.g. via `watch_price_condition`), or HOLD. If the forecast is unavailable, note it as unavailable and proceed — do NOT block the trade solely because the forecast could not be computed.
If the answer to ANY of the first 3 checks is YES, you must scrap the trade. You must either analyze a different timeframe to find a better entry, or call `watch_price_condition` to wait for a safer pullback. 
ONLY call `declare_trade` if you are 100% confident you could defend this trade against rigorous critique.
For a BUY or SELL you MUST pass the numeric `entry`, `stop_loss`, and `take_profit` arguments to `declare_trade` (and `atr_14` from the consensus report). The Trade_Validator rejects directional trades that omit these or that fail Risk:Reward >= 1:2 / stop >= 1.5x ATR; if rejected, revise the levels and call `declare_trade` again. A HOLD may omit the numeric levels.
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
Always include the multi-timeframe bias, the key S/R levels used, the volatility (ATR) basis for the stop, and the Risk:Reward ratio in your setup_validation.
</setup_validation_disclosure>

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
3. Do not invent red flags if the trade is genuinely an A+ setup. If it fits the protocol, approve it and defend it.
4. If it fails the protocol, explain exactly why, and suggest a better entry using `watch_price_condition`.

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

def format_system_prompt(state: AgentState) -> str:
    mode = state.get("mode", "FIND")
    tf = state.get("timeframe") or "10m"
    tf_instruction = (
        f"\n\nCRITICAL TIMEFRAME REQUIREMENT:\n"
        f"The user's active chart timeframe is '{tf}'. You MUST conduct your deep quant analysis on the '{tf}' timeframe. "
        f"When calling tools such as `get_consensus_report`, `get_chart_patterns`, and `get_candles`, you MUST use '{tf}' as the timeframe argument."
    )
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
        return base_prompt + tf_instruction
    return DEEP_QUANT_SYSTEM_PROMPT + tf_instruction

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
    watch_price_condition,
    declare_trade
]
llm_with_tools = llm.bind_tools(tools)

# ── Nodes & Routing ─────────────────────────────────────────────────────────

import re
import json
import ast
import math

# Trade_Validator (Python mirror, task 5.2) — reused to derive the
# Risk_Reward_Ratio and to report per-check outcomes in VERIFY mode (R7.4).
from validator import Action

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
_ENTRY_RE = re.compile(r"entry\b[^0-9\-]*" + _LEVEL_NUM, re.IGNORECASE)
_SL_RE = re.compile(r"(?:stop[\s\-]?loss|stop|sl)\b[^0-9\-]*" + _LEVEL_NUM, re.IGNORECASE)
_TP_RE = re.compile(r"(?:take[\s\-]?profit|target|tp)\b[^0-9\-]*" + _LEVEL_NUM, re.IGNORECASE)


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


def _verify_mode_validator_checks(action, levels, atr_14):
    """Report the outcome of EVERY Trade_Validator check independently (R7.4).

    The Trade_Validator short-circuits on the first failure, but VERIFY mode must
    state pass/fail for each check on the user-proposed trade. This evaluates the
    four checks independently so each receives an explicit outcome.
    """
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
            "outcome": "pass" if rr >= 2.0 else "fail",
            "detail": f"RR={rr:.4f}",
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


def build_defensibility_record(messages, decision, mode=None, manual_trade=None) -> dict:
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
        "summary": (
            f"Multi-TF 1D bias: {bias_1d_raw or 'n/a'}. "
            f"RR: {risk_reward if risk_reward is not None else 'n/a'}. "
            f"High-confidence patterns: {named}. "
            f"Regime: {regime.get('favorability') if regime.get('available') else 'unavailable'}. "
            f"Relative strength: "
            f"{relative_strength.get('alignment') if relative_strength.get('available') else 'unavailable'}. "
            f"Forecast: "
            f"{forecast.get('forecast_alignment') if forecast.get('available') else 'unavailable'}. "
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
        ),
    }

    # VERIFY mode must report every Trade_Validator check outcome (R7.4).
    if mode == "VERIFY":
        record["validator_checks"] = _verify_mode_validator_checks(action, levels, atr)

    return record


def _finalize_decision(state: AgentState, decision: dict) -> dict:
    """Attach the defensibility record AND persist the decision to the journal.

    Single chokepoint for every finalize path (validated declare_trade, the
    data-gating HOLD, and the forced HOLD) so each committed decision is both
    defensible (R7) and recorded for the measurement feedback loop (Phase 2).
    Journaling is best-effort and never raises into the run.
    """
    decision["defensibility"] = build_defensibility_record(
        state["messages"],
        decision,
        mode=state.get("mode"),
        manual_trade=state.get("manual_trade"),
    )
    try:
        journal.record_decision(
            decision,
            symbol=state.get("symbol"),
            timeframe=state.get("timeframe"),
            mode=state.get("mode"),
        )
    except Exception as e:
        print(f"[Deep Quant] WARN: journal.record_decision failed: {e}")
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
    response = llm_with_tools.invoke(messages)
    
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

    return {
        "messages": [response],
        "reasoning_turns": reasoning_turns,
        "market_data_seen": market_data_seen,
    }

# Base ToolNode used to execute only the well-formed (`ok`) tool calls.
_base_tool_node = ToolNode(tools)


def tool_node(state: AgentState):
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
    last_message = state["messages"][-1]
    all_calls = list(getattr(last_message, "tool_calls", None) or [])
    statuses = (last_message.additional_kwargs or {}).get("_extraction_status", {})
    synthetic = (last_message.additional_kwargs or {}).get("_synthetic_results", {})

    ok_calls = [tc for tc in all_calls if statuses.get(tc["id"], "ok") == "ok"]
    failed_calls = [tc for tc in all_calls if statuses.get(tc["id"], "ok") != "ok"]

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
            hold_decision["defensibility"] = _finalize_decision(state, hold_decision)
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
        _finalize_decision(state, decision)
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

    # ── Precedence 3: bounded reasoning loop ─────────────────────────────────
    reasoning_turns = state.get("reasoning_turns", 0)
    print(f"[Deep Quant Routing] Consecutive reasoning turns: {reasoning_turns}/{MAX_REASONING_TURNS}")
    if reasoning_turns < MAX_REASONING_TURNS:
        print("[Deep Quant Routing] Reasoning budget remaining. Routing to -> loop_agent")
        return "loop_agent"

    # ── Precedence 4: reasoning exhausted → forced HOLD ──────────────────────
    print("[Deep Quant Routing] Reasoning budget exhausted. Routing to -> force_hold")
    return "force_hold"


def force_hold(state: AgentState):
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
    decision["defensibility"] = _finalize_decision(state, decision)
    final_message = AIMessage(
        content=json.dumps(
            {
                "conviction_score": decision["conviction_score"],
                "setup_validation": decision["setup_validation"],
                "execution_plan": decision["execution_plan"],
            }
        )
    )
    return {"decision": decision, "messages": [final_message]}


def route_after_tools(state: AgentState) -> str:
    """After tool execution, terminate if a decision was committed, else loop.

    declare_trade commits a decision into state["decision"] during tool
    execution; when present the run ends immediately rather than spending an
    extra model turn (R2.2). Otherwise control returns to the agent.
    """
    if state.get("decision"):
        print("[Deep Quant Routing] Decision committed during tool execution. Routing to -> end")
        return "end"
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
        "atr_14": record.get("atr_14"),
        "trend_1d": record.get("trend_1d"),
        "multi_tf_bias": record.get("multi_tf_bias"),
        "support_resistance": record.get("support_resistance"),
        "patterns": record.get("patterns") or [],
        "predictive_conflict": record.get("predictive_conflict"),
        "macro_trend_conflict": record.get("macro_trend_conflict"),
        "news_sentiment": record.get("news_sentiment"),
        "defensibility_summary": record.get("summary"),
        # Which tools have already returned usable data this thread — the model
        # may re-call any of these (read-only) to fill a gap (R18.4).
        "available_tool_results": sorted(results.keys()),
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
            "NO Declared_Trade exists for this session yet (the analysis ended in a "
            "HOLD or no trade was committed). Answer using the available analysis "
            "context, and explicitly state that no trade has been declared yet."
        )

    return (
        "You are Alpha-Quant in Trade Q&A mode. The user is asking follow-up "
        "questions about a COMPLETED analysis for this session. Your job is to "
        "explain and defend the recorded analysis — NOT to run a new analysis or "
        "change anything.\n\n"
        "RECORDED SESSION ANALYSIS CONTEXT (the only ground truth you may cite):\n"
        f"{context_json}\n\n"
        "RULES:\n"
        "1. Answer ONLY from the recorded context above. Ground every factual "
        "claim (levels, RR, ATR, trend bias, patterns, sentiment) in that "
        "context.\n"
        f"2. {trade_clause}\n"
        "3. If the user asks something that is NOT in the context, you may call "
        "ONE relevant read-only market-data tool (get_consensus_report, "
        "get_candles, get_multi_tf_trend, get_chart_patterns, "
        "get_support_resistance, get_news_context) to fetch it. If you cannot "
        "obtain it, say the data is unavailable. NEVER fabricate an answer.\n"
        "4. The committed trade is IMMUTABLE here. Do NOT call declare_trade or "
        "watch_price_condition — they are disabled in Q&A mode. You cannot change "
        "the committed decision.\n"
        "5. Be concise and specific. Quote the recorded numbers when relevant."
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
    llm_messages = [SystemMessage(content=system_prompt)] + list(convo)

    response = llm_with_tools.invoke(llm_messages)

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
    """Select the entry node: the Q&A handler in QA mode, else the analysis loop.

    A request with ``mode == "QA"`` reuses the same thread_id and answers from
    the persisted Session_Analysis_Context without re-running analysis (R18.1).
    """
    if (state.get("mode") or "").strip().upper() == QA_MODE:
        print("[Deep Quant Routing] mode=QA -> entering Trade Q&A handler.")
        return "qa_agent"
    return "agent"


# ── Graph Assembly ──────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

# Add the main agent and tool execution nodes
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)
workflow.add_node("force_hold", force_hold)

# Trade Q&A nodes (Requirement 18). They reuse the same compiled graph + the
# MemorySaver checkpointer so a QA request on an existing thread_id sees the
# persisted Session_Analysis_Context. They are wired as a separate, bounded
# sub-loop that never mutates the committed decision (R18.6).
workflow.add_node("qa_agent", qa_node)
workflow.add_node("qa_tools", qa_tool_node)

# Conditional entry: mode=QA enters the Q&A handler; everything else runs the
# normal FIND/VERIFY analysis loop (R18.1).
workflow.set_conditional_entry_point(
    route_entry,
    {
        "qa_agent": "qa_agent",
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
        "end": "__end__",
    }
)

# After tools run, terminate if declare_trade committed a decision, else loop.
workflow.add_conditional_edges(
    "tools",
    route_after_tools,
    {
        "agent": "agent",
        "end": "__end__",
    }
)

# A forced HOLD terminates the run.
workflow.add_edge("force_hold", "__end__")

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
