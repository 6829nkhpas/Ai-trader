# Strat AI — Definitive Product Context Blueprint

> **Purpose of this document**: This is a self-contained context file designed to be consumed by another AI model (or a human team) to fully understand what Strat AI is, what its core features do, how its advanced features support the core ones, and why the platform is reliable and trustworthy. No source code is included — only the structural logic, workflows, and design philosophy extracted from deep codebase analysis.

---

## What Is Strat AI?

Strat AI is a quantitative trading terminal for the Indian stock market (NSE). It replaces the way traders make decisions. Traditional trading software shows you what already happened — a chart, some indicators, some news. Strat AI shows you what is likely to happen next, checks whether your trade idea is actually good before you risk money, and fuses every signal (price momentum, news sentiment, institutional order flow) into a single clear number.

The platform is built around three core pillars, each supported by a layer of advanced infrastructure underneath.

---

## The Three Core Features

### Feature 1: Deep Quant Co-Pilot — An AI That Finds, Verifies, and Debates Trades

This is not a chatbot. It is a stateful reasoning engine that acts like a veteran institutional risk officer sitting beside the trader. It has four distinct operating modes:

#### FIND Mode — Autonomous Trade Discovery

The trader activates FIND mode on a stock they are watching. The AI then autonomously runs through a structured evaluation pipeline to determine whether a high-quality trade exists right now. Here is exactly what it does, in order:

1. **Macro Alignment Check**: Reads the trend direction across three timeframes (1-hour, 4-hour, and daily) to establish whether the broader market is moving up, down, or sideways. A trade that fights the daily trend gets flagged immediately.

2. **Microstructure Scan**: Pulls detailed technical indicator readings — not just labels like "overbought" but the actual numeric values: exact RSI, Stochastic, MACD histogram slope, EMA 9/21 crossover status, Bollinger Band squeeze width, ATR volatility, VWAP institutional fair value, and volume flow (OBV, CMF). The AI reads all of these as a coherent picture, not individual signals.

3. **Market Regime Classification**: Determines whether the current market is trending (momentum setups work), ranging (mean-reversion works), volatile (reduce size), or quiet (watch for breakouts). This classification uses ADX directional strength, a choppiness index, ATR volatility percentile ranking, and Bollinger Band width. The regime doesn't block trades — it calibrates how aggressive the AI should be.

4. **Relative Strength vs Benchmark**: Compares the stock's performance against the NIFTY 50 index to determine if it is a leader (outperforming), inline, or a laggard (underperforming). The veteran principle: trade the strongest names with the market, never buy a laggard in a falling market.

5. **Order Flow Reading**: Analyzes who is actually pressing trades — buyers or sellers — using two layers:
   - A candle-derived proxy layer that calculates per-candle volume delta, cumulative volume delta (CVD), and buying pressure ratio.
   - A live tick-level Order Flow Imbalance (OFI) computed from actual tick-by-tick trade data when available.
   If the tick data is not available, the system honestly marks it as unavailable rather than making up a neutral value.

6. **Volume Profile Analysis**: Maps where the most trading volume has occurred at each price level to identify the Point of Control (the highest-volume price level that acts as a magnet), Value Area High and Low (the boundaries of where 70% of volume traded), and High/Low Volume Nodes (support/resistance shelves and rejection gaps).

7. **Support & Resistance Levels**: Calculates pivot-based support/resistance levels (S3/S2/S1/Pivot/R1/R2/R3) plus the Opening Range (first 3 candles' high and low — a key intraday reference).

8. **Chart Pattern Recognition**: Scans for 19 institutional-grade chart patterns across three categories:
   - Reversal patterns (8): Head & Shoulders, Inverse H&S, Double Top/Bottom, Triple Top/Bottom, Rising/Falling Wedge
   - Continuation patterns (6): Bullish/Bearish Flag, Bullish/Bearish Pennant, Cup & Handle, Inverse Cup & Handle
   - Bilateral patterns (4): Symmetrical Triangle, Ascending Triangle, Descending Triangle, Rectangle
   Each pattern has a confidence score (0.0 to 1.0). Only patterns above 0.6 confidence inform the trade thesis.

9. **VWEPR Quadratic Curvature**: A proprietary indicator that fits recent price history to a polynomial curve. Positive curvature means accelerating bullish momentum; negative curvature means momentum exhaustion (a rounding top). This is not a standard indicator — it is unique to Strat AI.

10. **Directional Forecaster**: A volatility-aware probabilistic forecast that produces a calibrated up-probability (0.0 to 1.0), an expected move sized in ATR units, and a forecast confidence score. Unlike simple trend indicators, this forecast conditions its output on the current market regime — it expects trend continuation in trending markets and mean-reversion in ranging markets.

11. **News Sentiment**: Reads recent financial headlines classified by an LLM into sentiment scores. This is a calibration input, not a trade trigger.

12. **Session & Expiry Awareness**: Checks what phase of the NSE trading day it is (pre-open, opening volatility, morning trend, midday chop, afternoon institutional flow, closing). The system knows that the opening 15 minutes are violent and mean-reverting, the midday is thin and choppy, and expiry afternoon flow is distorted. It won't block a trade for session reasons, but it will lower conviction during unfavorable windows.

13. **Options Chain Analysis** (when applicable): Reads institutional options positioning — Put-Call Ratio, max pain (the strike where price tends to be pinned into expiry), OI buildup, OI walls (heavy open interest strikes acting as magnets/barriers), IV skew, and futures basis. This is the single biggest source of intraday edge on the NSE.

14. **Event Risk Gate**: Checks whether there is an upcoming earnings or results announcement. If the trade would be held through a binary event (where the stock can gap 8-12% overnight), the system recommends sizing down, shortening the horizon, or standing aside.

15. **Track Record Calibration**: Before committing, the AI checks its own past performance on comparable setups. If a similar setup type historically has negative expectancy, the AI lowers conviction or refuses the trade. If it has positive expectancy, conviction increases. This creates a self-improving feedback loop.

Only when the AI finds genuine confluence across a majority of these inputs does it commit a trade. A single indicator is never enough. The trade must clear multiple independent checks all pointing in the same direction.

#### VERIFY Mode — Pre-Trade Risk Audit

The trader has their own trade idea and wants the AI to stress-test it before they risk capital. They input their Entry Price, Stop Loss, and Take Profit.

The AI then runs these institutional-grade checks:

- **Stop-Loss Volatility Check**: The stop distance must be at least 1.5× the current ATR (Average True Range). If it's tighter, the trade will get stopped out by normal market noise — the AI rejects it outright.
- **Risk-to-Reward Ratio**: For swing/investor trades, reward must be at least 2× the risk (1:2 minimum). For intraday, the floor is 1:1.3. Below this, the trade doesn't earn its risk — rejected.
- **Level Ordering**: For a BUY, stop loss must be below entry and take profit above. For a SELL, reversed. Sounds obvious, but the system catches mistakes before they cost money.
- **Management Plan Validation**: If the trader attaches a multi-leg exit plan (scale-out targets, breakeven trigger, trailing stop), each leg is validated: fractions must sum correctly, targets must be in correct order, breakeven trigger must sit between entry and first target, and the blended risk-reward must still clear the minimum.

Then the **Bear Agent (Devil's Advocate)** activates. This is a separate AI personality whose sole job is to find reasons NOT to take this trade. It looks for:
- Overhead VWAP resistance that would cap the move
- Heavy call OI walls just above the take profit
- Volume profile gaps that make the entry level unreliable
- Session timing problems (entering during midday chop)
- Macro trend opposition

The Bear Agent produces a structured critique. The trader sees exactly what could go wrong before they commit capital.

#### DEBATE Mode — Multi-Agent Consensus

When the market is ambiguous, the trader can trigger a formal debate:

1. Both a **Bull Agent** and a **Bear Agent** are spawned. Neither can actually place trades — they can only analyze and argue.
2. Each agent presents a structured stance: their directional lean (long/short/neutral), a conviction strength score (0-100), their arguments, and the single biggest risk to their own thesis.
3. They debate for a configurable number of rounds.
4. A **Judge Agent** then evaluates both stances and classifies the consensus:
   - **Strong Agree** (gap ≥ 30 points, winner ≥ 60 strength): One side clearly dominated. High conviction.
   - **Contested** (both ≥ 60 strength, gap ≤ 15 points): Both sides are strong and close. The Judge defaults to caution (HOLD).
   - **Lean** (everything else): A mild edge to one side but not overwhelming.

5. The Judge derives a calibrated conviction score using a weighted formula: 70% of the winning side's strength + 30% of the separation between sides, minus a 25-point penalty if the debate was contested. This prevents contested debates from producing falsely high conviction.

#### QA Mode — Interactive Auditing

After any analysis, the trader can ask follow-up questions in plain language: "Why did you choose that stop level?", "What if the 4H trend was bearish?", "How does the volume profile support this entry?". The AI answers grounded in the exact data it used during the analysis. Critically, committed trade decisions are immutable during Q&A — the trader can probe the reasoning without accidentally altering the plan.

#### Glass-Box Transparency

Every single step of the analysis is streamed live to the trader's screen as it happens: which tool was called, what data came back, what reasoning was applied. The trader watches the AI think in real time. There are no black boxes.

---

### Feature 2: Predictive Ghost Lines — Forward Price Trajectory Projections

Traditional charts only show what already happened. Ghost Lines show what is likely to happen next.

**How it works:**

1. The system aggregates raw exchange ticks into clean 10-minute candle windows (OHLC: Open, High, Low, Close).
2. A predictive engine maintains a rolling window of the last 14 candle closes.
3. It runs Ordinary Least Squares (OLS) Linear Regression across those 14 points to project where the next candle's close is likely to land.
4. It calculates an R² confidence score (Coefficient of Determination) — this tells the trader how reliable the projection is. High R² (near 100) means price has been moving in a clean, predictable trend. Low R² means the projection is uncertain.
5. The projected trajectory appears directly on the live chart as a dashed sky-blue line extending beyond the current candles.

**Why it's reliable:**

- Ghost Lines are rigidly bound to the 10-minute timeframe. The model is trained on 10-minute closes, so it only renders on the 10-minute view. On other timeframes, Ghost Lines are automatically hidden to prevent misleading projections from a model that wasn't calibrated for that timeframe.
- The R² confidence score is always displayed alongside the projection so the trader knows exactly how much to trust it.
- The rendering bypasses the browser's normal UI update cycle and pushes directly to the chart canvas, so projections update with zero visual lag.

---

### Feature 3: Fused Technical & Sentimental Conviction Score (1–100)

Instead of forcing traders to mentally juggle dozens of indicators and news headlines, Strat AI produces a single number: the Conviction Score, ranging from 1 (no conviction) to 100 (maximum conviction).

**How it's built:**

Three independent intelligence agents contribute signals:

1. **Technical Agent**: Computes real-time RSI, MACD, EMA 9/21 momentum ribbons, Bollinger Bands, VWAP, and VWEPR quadratic curvature. Encodes the directional signal and conviction strength.

2. **Sentiment Agent**: Continuously polls financial news from RSS feeds, NewsData.io, and Finnhub company profiles. Each headline is classified by an LLM into a sentiment score (0–100). Results are cached to avoid processing the same headline twice.

3. **Anomaly Agent**: Monitors live price streams for sudden moves (≥2% absolute candle swing). When detected, it invokes a separate LLM (DeepSeek v4 Pro) to generate a headline, detailed market commentary, and a sentiment assessment. This catches breaking developments that haven't appeared in news feeds yet.

**How fusion works:**

The Aggregator Engine takes the technical signal and the sentiment signal and combines them with a 70/30 weighting (70% technical, 30% sentiment) by default.

**The Capital Protection Guardrail:** When technical indicators say BUY but news sentiment says SELL (or vice versa), the Aggregator does not average them into a lukewarm number. Instead, it detects the conflict and automatically forces a HOLD. This protects trader capital during ambiguous market conditions where acting on conflicting signals leads to whipsaw losses.

---

## How the Advanced Features Support the Core

### Sub-Second Live Data Pipeline

The entire system starts with data speed. Strat AI connects directly to the Zerodha Kite exchange WebSocket and parses the raw binary tick packets in Rust — one of the fastest programming languages. Each tick is published simultaneously to two systems: a streaming message bus (Kafka/Redpanda) for real-time agent processing, and a time-series database (QuestDB) for historical storage. This dual-sink architecture means the AI agents always have both live and historical data available.

### Institutional Order Flow & Footprint Visualization

Beyond the AI's order flow analysis, the terminal visually renders institutional order flow directly on the chart canvas. Two modes:

- **Volume Profile Mode**: Shows where the most volume was traded at each price level, highlighting the Point of Control and Value Area boundaries.
- **Footprint Mode**: Plots the actual bid and ask volume at every price tick, colored by imbalance (green when buyers dominate, red when sellers dominate), running at 60 frames per second. This lets traders visually follow institutional money flow in real time.

### Three Tailored Trading Profiles

Different traders have different needs. The terminal offers three one-click workspace profiles:

- **Intraday (Scalping)**: Optimized for fast trades with 1-minute and 5-minute charts, a live Level-2 order book showing bid/ask depth, and volatility heatmaps.
- **Swing Trading**: Multi-day positions with multi-timeframe trend alignment (1H, 4H, 1D, 1W views), a Fear & Greed sentiment gauge, and a scrollable news feed with per-headline sentiment scores.
- **Investor Mode**: Long-term allocation with macro indicators (Fed funds rate, CPI, 10-year Treasury, Dollar Index, VIX), portfolio risk metrics (Sharpe Ratio, Max Drawdown, Beta, Alpha), and AI-generated sector outlooks.

### Native Desktop Application

The terminal runs as a native desktop application (built with Tauri), not just a browser tab. The Rust core handles all data connections and streams results directly to the UI through native inter-process communication, bypassing browser limitations. The result is zero-latency chart rendering — charts update as fast as data arrives.

### 5-Year Historical Data Store

The time-series database stores five years of daily and intraday candle data, partitioned by year for fast queries. When the trader opens a chart, historical data is transferred from the database to the UI in compact binary format (not JSON), minimizing transfer time. The AI's trade journal uses this same historical data to score past trades and track performance.

### Portfolio & Risk Management Engine

Direct integration with the Zerodha Kite broker API for real-time tracking of available margin, active positions with live P&L, order status with color-coded badges, and rejected order explanations. Position data refreshes in real time; margin data is cached for 60 seconds to avoid hitting rate limits.

---

## Why Strat AI Is Reliable, Accurate, and Trustworthy

### 1. Honest Failure Over Fabrication

When any data source fails — an API timeout, a database query error, an LLM that returns garbage — the system returns a structured "unavailable" marker instead of making up data. The AI then acknowledges the missing input in its reasoning and proceeds with what it does have. It never hallucinates values. This is a foundational design principle applied to every single tool and data source.

### 2. Hard Risk Rules That Cannot Be Bypassed

The Trade Validator enforces three hard rules that no amount of AI reasoning can override:
- Stop loss must be ≥ 1.5× ATR
- Risk-to-Reward must meet the profile minimum (1:1.3 intraday, 1:2 swing/investor)
- Price levels must be ordered correctly for the trade direction

These are implemented identically in both the Python AI layer and the Rust backend. A trade that fails any of these checks is rejected, period.

### 3. Capital Preservation Guardrail

When technical and sentiment signals conflict, the system defaults to HOLD. It does not average contradictory signals into a mediocre trade. It protects capital first.

### 4. Self-Improving Track Record

The SQLite-backed trade journal records every committed trade, tracks whether it hit the target or stop, and computes win rate and expectancy (in R-multiples) per setup type. When the AI encounters a similar setup in the future, it checks this track record. If comparable setups have historically lost money, conviction is automatically reduced. The system learns from its own mistakes.

### 5. Full Glass-Box Transparency

Every tool call, every data point, every reasoning step is streamed live to the trader. They can see exactly which indicators the AI checked, what values it found, and why it made its decision. Nothing is hidden.

### 6. Adversarial Self-Critique

Before committing any trade, the system runs an aggressive internal self-verification: Is the stop too tight? Am I fighting the daily trend? Is the R:R below minimum? Does volume flow confirm? Is the market regime unfavorable? Am I fighting the index? What does my own track record say about this setup type? Only after passing all of these checks — and only when the AI can defend the trade against rigorous critique — does it commit.

### 7. Bounded Execution With Guaranteed Termination

The AI reasoning loop has a hard budget: if it takes too many consecutive reasoning turns without calling a tool or making a decision, the system forces a HOLD. Debates have bounded rounds and turn limits. Price watches have cycle caps. The system always terminates — it never spins endlessly.
