# Strat AI — Marketing & User Acquisition Plan

**Version 1.0 · August 2026**
**Objective:** acquire paying users for TERMINAL now, build the audience that converts to RESEARCH on licence grant, and sign broker platform deals — without a single claim that jeopardises the SEBI application.
**Audit basis:** `stratai.live` and `tradingrw.com` as live on audit date · product features as built at commit `ccf29b5`

> Registration, entity and compliance-engineering work lives in `COMPANY_REGISTRATION_PLAN.md`.
> **[COUNSEL]** = requires securities-law sign-off before publishing.
> *Content from external sources was rephrased for compliance with licensing restrictions.*

---

## Part 1 — The constraint that shapes everything

Marketing a securities product in India is a constrained optimisation problem, not a creative one.
Almost every high-converting message a US fintech would use is prohibited or heavily conditioned here.

| Constraint | Source | What it kills |
| --- | --- | --- |
| **No assured or implied returns**, express or implied | SEBI (Intermediaries) (Amendment) Regulations, 2024 — Reg 16A, effective 29 Aug 2024 | "Beat the market", "consistent profits", "our users averaged X%", P&L screenshots, profit testimonials |
| **No association with unregistered advice or return claims** — direct *or indirect* | Reg 16A | Any affiliate, influencer, reseller or community moderator who names stocks or claims returns |
| **Registration ≠ approval or performance guarantee** | RA Regulations; MITC | "SEBI approved", "SEBI certified", "SEBI-approved algo" (which does not exist in any form) |
| **Advertisement code + record-keeping** | SEBI RA guidelines, 8 Jan 2025 | Untracked creative. Every ad needs an approval trail and a retained copy |
| **Social advertiser verification** | SEBI media release, Mar 2025 — registered intermediaries had to update contact details on the SEBI SI Portal by 30 Apr 2025 | Running securities ads without a verified, registration-backed advertiser identity |
| **Platform financial-services verification** | [Google Ads](https://support.google.com/adspolicy/answer/15332527) requires advertisers to show they are licensed by the relevant regulator, or exempt | Paid search and paid social for anything reading as investment advice, until the INH is in hand |
| **Active AI surveillance of financial social content** | SEBI has flagged ~20,000 fraudulent posts since Nov 2025, targeting guaranteed returns, fraudulent certifications and unregistered advice | Any assumption that a post is unobserved. Assume a machine reads everything you publish |

**Operational consequence: paid acquisition is gated on the licence.** Plan a **content-and-community-led
year one**. This is not a workaround — it is the correct read of the channel landscape, and it happens to
suit a product whose differentiator is intellectual rigour.

---

## Part 2 — Website remediation (Week 1–3, blocking)

### 2.1 `stratai.live` — good, with three fixes

The site is close to model compliance copy already. *"AI that tells you when not to trade"*, an explicit
*"does not execute trades, manage funds, or offer personal financial advice"*, and a Conviction Score FAQ
that states *"It is not a probability, win rate, or return forecast."* Keep all of it.

| # | Fix | Detail |
| --- | --- | --- |
| 1 | **Remove the SEBI-registration claim** | The in-product string *"our SEBI-registered research service"* (`sku.ts:176`) is visible to users. See `COMPANY_REGISTRATION_PLAN.md` RED 1. You are not registered |
| 2 | **Soften "recommendation"** | The FAQ says the guardrail *"forces a HOLD recommendation"*. "Recommendation" is the statutory trigger word. Use *"forces a HOLD outcome"* or *"returns HOLD"* |
| 3 | **Add risk copy and disclaimers** | `/pricing` has no disclaimer, risk warning, terms or registration text. Add the non-registration disclosure, a risk warning, and a link to the published AI disclosure |

### 2.2 `tradingrw.com` — rewrite end to end

This is your largest marketing liability and it undoes the good work on stratai.live.

| Element currently live | Problem | Severity |
| --- | --- | --- |
| **"Success Metrics — Strat AI performance for serious market participants"** | A performance section on a financial services site | **Critical — delete** |
| **"Strike 24,100 CE +142.3%" · "Strike 24,150 CE +89.7%" · "Strike 24,200 PE −12.4%"** | Percentage returns against **named option strikes**. Even as a UI mockup this reads as implied returns on specific instruments | **Critical — delete** |
| **"Sub-millisecond order processing ensures your strategies execute at optimal price levels"** | Claims **order execution** and **optimal pricing**. Contradicts stratai.live's read-only statement **and** your own code, which has no order path and proves it by denylist test | **Critical — rewrite** |
| **"peer-reviewed publications and working papers"** → `/publications` returns **HTTP 404** | Unsubstantiated academic credibility claim linking to nothing | **Critical — publish or delete** |
| "quantitative strategy backtests" | Backtested performance without required disclosures | High |
| "Portfolio ₹14,300.7K" | Displays a portfolio value | High |
| "Industry-leading low latency" · "revolutionized" · "Exceptional visual clarity" | Superlatives and hyperbole | Medium |
| ISIN `INE009A01021` · `NIFTY25JUL` shown with figures beside them | Named securities with numbers attached | Medium |
| "compliance-grade isolation" | Puffery implying a compliance status | Low |

**The cross-site contradiction is what hurts most.** A SEBI reviewer, a broker's compliance head and an
investor's diligence analyst will all read both sites. One says read-only; the other sells order
execution. That does not read as inconsistent marketing — it reads as an **accuracy problem**, on the one
dimension you are asking all three to trust you on.

**Approved replacements:**

| Instead of | Write |
| --- | --- |
| "Sub-millisecond order processing ensures your strategies execute at optimal price levels" | "Exchange ticks parsed in Rust and available to the analysis layer in under a second." |
| "Success Metrics" | "Engineering Benchmarks" — ingestion latency, history depth, chart render rate. No P&L, no returns |
| "Industry-leading low latency" | "Ticks parsed from the exchange binary feed, not polled from a REST API." |
| Strike percentages | The same panel with the percentage columns removed, or synthetic non-numeric placeholders |
| "peer-reviewed publications" | "Engineering and research notes" — and actually publish them at a working URL |

Rewrite against `docs/compliance/BRAND_GUIDELINES.md`, which already exists and is 33KB.

---

## Part 3 — Who you are selling to

Five user segments and one buyer, each mapped to features you have actually built.

### ICP 1 — The Burned Swing Trader ★ **PRIMARY**

**Who:** 28–42, ₹5–25 lakh trading capital, 2–6 years in the market, has had at least one account-halving
event. Holds positions days to weeks. Employed or self-employed; trades around a job.

**Pain:** enters on a decent thesis, sizes it badly, sets a stop inside normal noise, gets shaken out,
re-enters worse. Knows the problem is process, not information.

**Why they buy Strat AI:** VERIFY tells them their stop cannot survive today's ATR **before** they place
it. The Bear Agent tells them what will kill the trade. The Swing profile aligns 1H/4H/1D/1W so they stop
fighting the daily trend. The regime classifier tells them when their momentum setup is in a ranging
market.

**Features to lead with:** VERIFY (1.5× ATR floor, 1:2 reward-to-risk floor), Bear Agent, multi-timeframe
alignment, regime classifier, relative strength vs NIFTY, event-risk gate.

**Approved hook:** *"Your stop is 0.9× ATR. This setup gets stopped out by noise, not by being wrong."*

**Why primary:** SEBI is deliberately compressing intraday F&O participation and succeeding — unique
individual derivatives traders fell from **98.10 lakh in FY25 to 78.60 lakh in FY26**. The swing cohort is
not the target of that compression. Build here.

### ICP 2 — The Serious F&O Intraday Trader

**Who:** 25–38, ₹2–15 lakh, trades index and stock options daily. Already pays for one or two tools.
Fluent in Greeks, OI, VWAP.

**Pain:** wants microstructure edge — where the real orders are, where price gets pinned into expiry.

**Why they buy:** order-flow footprint at 60fps with bid/ask imbalance colouring, tick-level OFI (honestly
marked unavailable when the feed is not there), volume profile with Point of Control and Value Area,
Level-2 depth, options chain with max pain, OI walls, IV skew and futures basis, session-phase awareness.

**Features to lead with:** footprint, OFI, volume profile, options chain analytics, opening-range levels.

**Approved hook:** *"Cumulative volume delta from ticks, not from candle colour. When the tick feed is
unavailable, it says so instead of printing zero."*

**Caveat:** highest ARPU willingness, highest churn, shrinking pool. Serve them, do not build the company
on them.

### ICP 3 — The Quant / Developer ★ **HIGHEST TRUST MULTIPLIER**

**Who:** 24–40, engineer or data scientist who trades. Writes Python. Suspicious of anything marketed.

**Pain:** every retail tool is a black box with a marketing site. Wants to know how the number was computed.

**Why they buy:** Rust binary tick parser, dual sink to Kafka/Redpanda and QuestDB, five years of
partitioned history, VWEPR quadratic curvature documented from first principles, trajectory projections as
plain OLS with a published R², glass-box streaming of every tool call, hash-chained append-only records.

**Features to lead with:** the architecture itself, the open-sourced component, the technical writing.

**Approved hook:** *"Trajectory projections are a 14-period OLS regression on 10-minute closes. Here is the
R². Here is why it refuses to render on any other timeframe."*

**Why they matter disproportionately:** they generate the credibility that converts ICPs 1 and 2, they are
immune to conventional marketing so competitors cannot reach them, and they become public advocates. Every
technical post is aimed at this segment first.

### ICP 4 — The Long-Horizon Investor

**Who:** 32–55, ₹25 lakh–₹2 crore, allocates rather than trades. May be an NRI (relevant to the future
GIFT City phase in `PLAN_OF_ACTION.md`).

**Why they buy:** Investor profile — macro indicators (Fed funds, CPI, 10-year, DXY, VIX), portfolio risk
metrics, sector outlooks, event-risk gating, 1:2 reward-to-risk floor.

**Approved hook:** *"It checks whether you are about to hold a position through an earnings gap."*

**Value:** lowest churn, lowest support burden, best word of mouth. Under-weighted in most competitors'
roadmaps.

### ICP 5 — The Discipline Seeker

**Who:** cuts across ICPs 1–4. Has read the SEBI loss studies. Suspects they are the problem.

**Why they buy:** the discipline dashboard — Setups Audited, Setups Rejected, Forced Holds, plan
adherence (`computeDisciplineMetrics()`, already built). The conflict-forced HOLD. The three unbreakable
rules.

**Approved hook:** *"Last quarter it audited 46 of your setups and rejected 14 on the volatility or
reward-to-risk floor."*

**This is the retention segment.** Not a separate audience — the behaviour you want every user to adopt.

### Buyer 6 — The Broker (B2B2E)

**Two people, and the order matters.** The **compliance head** is the real buyer; the **product head** is
the sponsor. Pitch compliance first. Detail in Part 7.

---

## Part 4 — Message architecture

### 4.1 Core positioning

**Category:** *pre-trade risk adjudication.* Name it and own it — nobody else occupies it.

**Core message:** **"Strat AI's most valuable output is the word no."**

This is compliant by construction (a claim about behaviour, not outcomes), differentiated (every
competitor sells *more* signals), and aligned with the regulator's own agenda. Individual F&O trader net
losses ran to **₹91,685 crore in FY26** against **₹1,11,788 crore in FY25**, with unique traders falling
from 98.10 lakh to 78.60 lakh — **fewer traders, worse average loss per head**. The market has no shortage
of trade ideas. It has a shortage of veto.

Do not dilute this to chase conversion. It is the whole brand.

### 4.2 Message hierarchy — approved copy

| Layer | Approved claim | Why it is safe |
| --- | --- | --- |
| **Primary** | "It tells you when *not* to trade." | Product behaviour, not outcome |
| **Secondary** | "Watch it think. Every tool call, every number, every reason — streamed live." | Verifiable fact; pre-answers SEBI's draft AI transparency principle |
| **Secondary** | "Three rules it cannot break: stop ≥ 1.5× ATR, reward-to-risk floor by profile, correct level ordering." | Describes a control, not a result. Specific and checkable |
| **Secondary** | "When the tape and the news disagree, it holds." | The capital-preservation guardrail |
| **Secondary** | "A separate agent argues against every setup before you see it." | The Bear Agent as a feature, framed as critique not as a call |
| **Supporting** | "Exchange ticks parsed in Rust. Five years of history. Native desktop, not a browser tab." | Pure engineering, zero regulatory surface |
| **Supporting** | "When data is missing, it says so. It never fills the gap with a guess." | Rare, credible, and true — see commit `82e0cb0` |
| **Supporting** | "Every analysis is recorded in an append-only log with the exact model version that produced it." | Genuinely novel in this market. Your strongest trust artefact |
| **Never** | Anything about returns, win rate, accuracy, or profit | — |

### 4.3 The banned list — enforce in review

Never publish: assured / guaranteed / consistent returns · win rate, accuracy or hit rate as a headline ·
P&L screenshots · testimonials referencing profits · "SEBI approved" / "SEBI certified" / "SEBI-approved
algo" · **any claim of SEBI registration until the INH is granted** · backtested or hypothetical
performance without required disclosures · "risk-free" · "sure shot" · "multibagger" · countdown-timer
urgency on a securities subscription · any comparison implying superior returns to a competitor or index ·
order-execution or execution-quality claims (you are read-only, keep it that way).

### 4.4 Review process (stand up in Week 1)

1. Every asset — page, post, creative, email, deck slide, video script — reviewed against §4.3 before publication.
2. **Advertisement register** from the first paid rupee: creative, approver, live dates, retained copy. This is a SEBI obligation and you have no system for it yet (`COMPANY_REGISTRATION_PLAN.md` P10).
3. Two-person rule on anything naming a security.
4. Weekly sweep of community and social for user-generated content that could breach Reg 16A by association.

---

## Part 5 — Channel plan by phase

Phases are gated on registration milestones from `COMPANY_REGISTRATION_PLAN.md`.

### Phase M0 — Remediation (Week 1–3). Pure subtraction, no new campaigns.

☐ Purge all SEBI-registration claims from code, both sites, decks, social bios, app-store listings
☐ Delete tradingrw.com Success Metrics and every strike percentage
☐ Rewrite the order-execution claim
☐ Publish real notes at `/publications` or delete the peer-review claim
☐ Remove superlatives
☐ Add risk copy + non-registration disclosure to both sites and `/pricing`
☐ Publish `docs/compliance/AI_DISCLOSURE.md` as a linked public page
☐ Reconcile the two sites so they describe the same product
☐ Screen every affiliate, influencer and referral partner against Reg 16A; terminate non-compliant ones
☐ Stand up the advertisement register

**Reg 16A cuts both ways.** It is why brokers legally cannot integrate an unregistered provider — your
future advantage. It is also why one influencer naming a stock becomes an enforcement vector into your
licensed entity. Screen ruthlessly.

### Phase M1 — Pre-licence, organic only (Week 3 → grant, ~6 months)

Sell **TERMINAL** only. No directional output in any tier, including free.

#### 5.1 Engineering-credibility content — 2 deep pieces/month

Your unfair advantage. Almost nobody in Indian retail fintech competes on technical depth. Aimed at ICP 3,
converts ICPs 1 and 2.

| # | Piece | Primary ICP |
| --- | --- | --- |
| 1 | How the Rust binary tick parser works, with real latency numbers | 3 |
| 2 | **How the append-only recommendation record works, and why a hash chain matters** | 3, 5 |
| 3 | Why trajectory projections are locked to 10-minute candles, and what R² actually tells you | 1, 3 |
| 4 | The regime classifier: ADX, choppiness, ATR percentile, Bollinger width — and why regime should calibrate aggression, not block trades | 1, 2 |
| 5 | Order flow imbalance: candle-derived proxy vs real tick-level OFI, **and why we mark it unavailable instead of defaulting to neutral** | 2, 3 |
| 6 | VWEPR quadratic curvature from first principles | 3 |
| 7 | **How the personalisation guardrail works** — a deterministic pre-LLM control, with the category table | 3, 5 |
| 8 | Why a 1.5× ATR stop floor, derived rather than asserted | 1, 5 |
| 9 | Volume profile: Point of Control, Value Area, and what a high-volume node actually tells you | 2 |
| 10 | The 19 chart patterns and why confidence below 0.6 is discarded | 1 |
| 11 | Reading the options chain: max pain, OI walls, IV skew, futures basis | 2 |
| 12 | Why the Bull and Bear agents cannot place trades, and how the Judge scores a contested debate | 1, 3 |
| 13 | Five years of ticks in QuestDB: partitioning, and binary transfer instead of JSON | 3 |
| 14 | Session phases on the NSE: why the opening 15 minutes and midday behave differently | 1, 2 |
| 15 | What "honest failure over fabrication" means in code, with the diff | 3, 5 |

Pieces **2 and 7** are the highest-value publications you can make. They are novel, they are true, they
are impossible for a signal shop to imitate, and they read to a regulator as exactly what a responsible
participant sounds like. Publish them early.

#### 5.2 Community

Moderated forum or Discord with one absolute rule: **no stock calls, by anyone, including moderators and
staff.** Reg 16A hygiene dressed as culture. Enforce it publicly and visibly — the enforcement itself
becomes brand evidence. A pinned post explaining *why* the rule exists is itself good marketing.

**Avoid Telegram as a primary channel.** It is where the unregistered tip industry lives; adjacency is a
reputational and Reg 16A risk you do not need.

#### 5.3 Open-source one peripheral component

The tick-parsing utility or the volume-profile renderer. Developer trust converts to paid quant users and
costs nothing strategically — the moat is the calibration dataset and the licence, not the parser.

#### 5.4 Founder-led long-form

YouTube in English, with Hindi subtitles or a parallel Hindi track. **Teach the method, never call the
trade.** *"How to check whether your stop can survive today's ATR"* is compliant, useful, and sells by
demonstration. Twelve-minute screen-recordings of VERIFY rejecting a real setup are the single best
conversion asset available to you pre-licence, because they show the product doing the thing the brand
claims.

#### 5.5 Where these people actually are

| Channel | ICP | Approach |
| --- | --- | --- |
| **TradingView India — published ideas** | 1, 2 | Publish *method* ideas, never directional calls on named securities |
| **r/IndianStreetBets, r/IndiaInvestments** | 1, 5 | Participate genuinely; answer risk-process questions. Never link-drop |
| **Fintwit India (X)** | 1, 2, 3 | Thread the technical content. Highest velocity for ICP 3 |
| **YouTube** | 1, 2, 5 | Long-form method teaching + screen recordings |
| **Hacker News / Lobsters / dev.to** | 3 | The Rust and hash-chain pieces. Also a hiring channel |
| **LinkedIn** | 6 | B2B only — compliance-led narrative for brokers |
| **Zerodha TradingQnA and similar forums** | 1, 5 | Answer process questions with genuine depth |

#### 5.6 Waitlist and closed beta

`stratai.live` currently runs an open waitlist alongside a public pricing page while the research surface
may be reachable. **Reconcile this** — see `COMPANY_REGISTRATION_PLAN.md` RED 2. Either the beta is
genuinely closed and non-paying, or RESEARCH is off. Marketing must match whichever you choose; an open
waitlist advertising research features you cannot legally sell is the worst of both.

### Phase M2 — Post-licence (from grant)

- Wire the **real INH number**, Compliance Officer contact, disclaimer and MITC link into every surface, **prominently**. In a market saturated with anonymous operators these are your strongest trust signals. Feature them; do not footer them
- Complete **Google financial services verification** and **SEBI SI Portal advertiser verification**
- **Paid search on non-promissory intent only:** "ATR stop loss calculator", "risk reward calculator NSE", "volume profile India", "order flow terminal India", "options max pain NSE", "trailing stop calculator". **Never** bid on return-promise language
- Retarget TERMINAL users to RESEARCH through the KYC-aware funnel
- Every creative through the advertisement register
- Publish the first honest, disclosure-compliant accuracy report — **voluntarily, before anyone requires it** **[COUNSEL]**

### Phase M3 — B2B2E (start Week 2, runs forever)

Highest leverage, needs no licence of your own. See Part 7.

---

## Part 6 — Packaging, pricing and funnel

### 6.1 Tier design

Your billing already implements credits (`creditMultiplier`, `creditLogs` in `frontend/src/lib/api/types.ts`).
Keep that model — it prices compute honestly, which fits the brand.

| Tier | Price | Credits | Contains | Licence needed |
| --- | --- | --- | --- | --- |
| **TERMINAL Free** | ₹0 | Small monthly allowance | Charts, indicators, trajectory projections, volume profile, S/R, regime label, single watchlist. **No FIND, no DEBATE, no conviction score** | None |
| **TERMINAL Pro** | ₹1,499/mo · ₹14,999/yr | Full analytics allowance | Everything above + order-flow footprint, Level-2 depth, VWEPR, 19 patterns, options chain analytics, **VERIFY** on your own levels, discipline dashboard | None |
| **RESEARCH** | ₹4,999/mo · ₹49,999/yr | Research-run allowance | Everything above + **FIND**, **DEBATE**, conviction score, QA mode, compliant research reports | **RA (INH)** |
| **PLATFORM** | ₹25L–₹1.5cr/yr + rev-share | Negotiated | White-labelled terminal + research engine | None (licensee's) |

**Critical rule:** the free tier must carry **no directional output whatsoever**. "For a fee" is broadly
construed and consideration can be indirect — a free tier handing out live calls is the fastest route to
an enforcement problem. Route all free users to TERMINAL. **[COUNSEL]**

**Fee-cap headroom:** RESEARCH at ₹49,999/year sits at roughly **one third of the ₹1,51,000 per annum per
family ceiling**. Real headroom — but the cap is **per family across all research services**, so a
household with two subscriptions counts against one ceiling. The enforcement build is
`COMPANY_REGISTRATION_PLAN.md` §6.2 step 5.

**Annual prepay is permitted:** since April 2025 an RA may collect advance fees covering up to one year.
Price annual plans at ~16% discount to drive the cash-flow benefit.

### 6.2 Funnel

```
Technical content / YouTube / community
        ↓  (ICP 3 credibility → ICPs 1,2,5 trust)
   TERMINAL Free  ──── activation event: first VERIFY run on their own trade
        ↓
   TERMINAL Pro   ──── activation event: footprint or options-chain habit forms
        ↓  [LICENCE GATE]
   RESEARCH       ──── activation event: first FIND report they act on
        ↓
   Retention: discipline dashboard, quarterly summary
```

**The single most important conversion moment is a user running VERIFY on a trade they were about to
place, and Strat AI rejecting it.** That is the product's thesis delivered as an experience. Instrument
it, optimise onboarding around reaching it in the first session, and measure time-to-first-VERIFY as your
primary activation metric.

**Onboarding sequence (build this):**
1. Signup → pick a profile (Intraday / Swing / Investor) → this sets the reward-to-risk floor
2. "Paste a trade you are considering" → VERIFY runs → shows the ATR check, the R:R check, level ordering
3. If rejected: show *why*, in the Bear Agent's words
4. Then, and only then, tour the charts

Most tools open on a chart. Opening on a rejection is the whole differentiation.

---

## Part 7 — B2B2E playbook

The highest-leverage channel, and your audit findings are the assets.

### 7.1 Pitch the compliance head first

| # | Argument | Artefact to show |
| --- | --- | --- |
| 1 | **Regulation 16A** — they legally cannot integrate an unregistered advice provider. This eliminates most vendors pitching them | Your INH (post-grant), or the filed application (pre-grant) |
| 2 | **The audit trail.** Under the February 2025 algo framework **the broker alone handles client complaints**. You give them the ability to reconstruct exactly what a client saw and why — with the model and prompt version hash | `reco_store.py` schema + `verify_chain()` output |
| 3 | **Read-only, proven by test** | `frontend/src/components/fno/__tests__/scopeBoundary.test.ts:256-264` — the denylist. Show them the test |
| 4 | **AI governance readiness.** When SEBI's AI guidelines land, integrating you is not a remediation project | `docs/compliance/AI_MODEL_GOVERNANCE.md` (30KB, already written) |
| 5 | **White-box discipline.** Trajectory projections and the risk validators are deterministic; the reasoning layer stays out of the order path by design, so integrating you does not drag them into black-box registration obligations | The OLS derivation and the validator specs |
| 6 | **The RA/IA boundary is enforced in code, not policy** | `personalisation.py` — a deterministic pre-LLM control |

Most vendors pitch features to a product manager. Pitching **provable controls to a compliance officer**
is a different conversation, and one almost nobody else in this market can have.

### 7.2 Target order

1. **Mid-size full-service brokers** needing differentiation against discount players — highest urgency, real budget, weakest in-house tech
2. **Existing SEBI-registered RAs** with distribution but no technology — 1,380 RAs as of August 2024, most with no product
3. **Wealth platforms** serving the Investor profile (ICP 4)

### 7.3 Deal shape

White-label licence, annual fee plus revenue share. **The licensee carries the client-facing regulatory
obligation** — that is what makes this track viable pre-grant and low-compliance-load permanently.

---

## Part 8 — Retention

`computeDisciplineMetrics()` already exists (`frontend/src/hooks/useMacroIndicators.ts`). It reports
Setups Audited, Setups Rejected, Forced Holds and plan adherence, and renders `—` rather than `0` for
anything unmeasured. This is the right architecture and it is built.

**Surface it as a quarterly in-product summary:**

> This quarter Strat AI audited 46 of your setups. 14 failed the volatility or reward-to-risk floor.
> 6 analyses returned HOLD because technical and sentiment signals disagreed.

Factual statement about product usage. No performance claim. The most powerful retention artefact
available to you, because it makes the value of *not trading* visible — which is otherwise invisible.
**[COUNSEL to review exact wording before shipping.]**

**Never** show the model's win rate, expectancy or accuracy to a user. That data stays internal, where it
belongs as model monitoring (`journal.py`). The external surface is the user's **own** discipline, not the
model's scorecard.

**Structural churn reality:** most retail traders lose money and quit. If retention depends on winning,
the business is built on sand. Build it on avoided loss.

---

## Part 9 — Metrics

| Metric | Why | Target (Year 1) |
| --- | --- | --- |
| **Time to first VERIFY run** | The activation moment that delivers the thesis | < 10 minutes from signup |
| Free → TERMINAL Pro conversion | Core monetisation pre-licence | 4–7% |
| TERMINAL Pro monthly churn | Product-market fit signal | < 6% |
| Setups rejected per active user per month | Proves the brand promise is real | > 3 |
| Technical pieces published per month | The engine that feeds everything | 2 |
| Broker conversations → pilots | B2B2E pipeline health | 20 → 2 in Year 1 |
| **Marketing assets failing compliance review** | Process health. Should trend to zero | < 5% after Month 2 |
| Reg 16A partner screenings completed | Enforcement-risk hygiene | 100% weekly |
| LLM cost per FIND run | Unit economics | Track from Day 1 |
| TERMINAL ARR at licence grant | Proves the licence is off the revenue critical path | ₹3.5 cr+ |

---

## Part 10 — 90-day calendar

**Weeks 1–3 · Remediate**
Purge SEBI claims everywhere · rewrite tradingrw.com · publish AI disclosure · add risk copy to `/pricing`
· screen all partners · stand up the advertisement register · publish brand guidelines internally with the
banned list · reconcile the beta/waitlist posture

**Weeks 3–6 · Build the engine**
Technical pieces 1 and 2 (Rust tick parser; the hash-chained record) · launch the moderated no-calls
community · first three YouTube method videos · **TERMINAL Pro paid launch** · open the first five broker
conversations, compliance-led

**Weeks 6–9 · Compound**
Pieces 3–6 · open-source the peripheral component · rebuild onboarding around time-to-first-VERIFY ·
first broker pilot in negotiation · start the discipline-dashboard quarterly summary build

**Weeks 9–13 · Scale organic**
Pieces 7–10 (publish the personalisation-guardrail piece) · Hindi track on YouTube · community at
self-sustaining volume · first broker pilot signed · paid-channel assets built and compliance-reviewed,
**held until grant**

**On grant** → switch to Phase M2. Verification, INH display, paid search, RESEARCH launch.

---

## Part 11 — Sources

- SEBI (Intermediaries) (Amendment) Regulations, 2024 — Regulation 16A, effective 29 August 2024
- SEBI guidelines for Research Analysts, circular dated 8 January 2025
- [SEBI media release on social-media advertiser verification, March 2025](https://economictimes.com/markets/stocks/news/sebi-mandates-advertiser-verification-on-social-media-to-curb-investment-frauds/articleshow/119309602.cms)
- [Google Ads — financial services verification](https://support.google.com/adspolicy/answer/15332527) · [financial products and services policy](https://support.google.com/adspolicy/answer/2464998)
- SEBI circular on relaxation of advance-fee restrictions for IAs and RAs, April 2025
- [Taxmann — SEBI guidance on the RA fee cap (₹1,51,000 per annum per family)](https://www.taxmann.com/post/blog/sebi-guidance-on-ra-fee-cap-for-individual-and-huf-clients)
- [Business Standard — FY26 F&O loss and trader-count data reported to Parliament, August 2026](https://www.business-standard.com/amp/markets/news/sebi-measures-reduce-equity-f-o-losses-for-retail-investors-in-fy26-126081101071_1.html)
- [SEBI study on individual traders in the equity derivatives segment, July 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jul-2025/1751900802566.pdf)
- [SEBI board memo — IA and RA population as of 31 August 2024](https://www.sebi.gov.in/sebi_data/meetingfiles/oct-2024/1728550911419_1.pdf)
- SEBI circular, *Safer participation of retail investors in Algorithmic trading*, 4 February 2025
- Internal: `docs/compliance/BRAND_GUIDELINES.md` · `AI_DISCLOSURE.md` · `AI_MODEL_GOVERNANCE.md`
- Audited: `stratai.live` (home, `/pricing`) · `tradingrw.com` (home, `/publications` → HTTP 404)

*Content from external sources was rephrased for compliance with licensing restrictions.*
