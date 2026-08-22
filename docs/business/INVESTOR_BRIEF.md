# Strat AI — Investor Brief

**Version 1.0 · August 2026 · Seed round**

> Internal working document. All market figures are cited; all financial projections are labelled
> assumptions. Read alongside `SEBI_COMPLIANCE_BLUEPRINT.md` — the regulatory structure is a material
> part of this investment case, not an appendix to it.
> *Content from external sources was rephrased for compliance with licensing restrictions.*
>
> **Engineering status · 19 August 2026.** Six of the seven Phase-0 compliance blockers have shipped
> and the seventh is half done, all pre-seed. That changes several rows in §5 and §7 from *intent* to
> *evidence*, and the diligence-relevant residual gaps are named in place rather than smoothed over.
> `PLAN_OF_ACTION.md` §4.2 is the canonical status table; this brief cites it and does not restate it.
> Registration, counsel and certification — the non-code half of Phase 0 — are all still open, and
> they are the critical path.

---

## 1. The one-line pitch

**Strat AI is the pre-trade risk layer for Indian retail traders — an AI that audits a trade idea
before capital is committed, and shows its full reasoning while doing it.**

Not another charting tool. Not another signal service. The product's defining behaviour is that it
**refuses trades**: it rejects a stop that sits inside normal volatility, rejects a reward-to-risk
below the profile floor, spawns an adversarial Bear Agent to attack the thesis, and forces a HOLD when
technical and sentiment signals disagree. Every step is streamed to the screen.

---

## 2. Why now — the market and the regulator are converging on our thesis

### The problem is officially quantified

The Indian government told Parliament in August 2026 that in FY26 net losses of individual traders in
the equity derivatives segment were **₹91,685 crore**, down from **₹1,11,788 crore** in FY25, with
unique individual traders falling from **98.10 lakh to 78.60 lakh**
([Business Standard](https://www.business-standard.com/amp/markets/news/sebi-measures-reduce-equity-f-o-losses-for-retail-investors-in-fy26-126081101071_1.html),
[Business Today](https://www.businesstoday.in/markets/story/unique-individual-investors-in-fo-trading-declines-in-fy26-but-average-per-person-loss-widens-548559-2026-08-11)).
SEBI's own FY25 study put the share of individual derivatives traders making net losses at
**roughly 91%** ([SEBI study, July 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jul-2025/1751900802566.pdf)).

Read the second-order detail: **fewer traders, and the average loss per trader got worse.** ₹91,685
crore across 78.6 lakh traders is about **₹1.17 lakh of loss per person per year**. The regulator's
curbs removed marginal participants; they did not make the survivors better. The survivors are
precisely our customer — they are still trading, they are still losing, and their loss per head now
exceeds a decade of Strat AI subscription fees.

### The regulator is building the moat for us

Three regulatory shifts, all landed in the last 24 months, all favour a licensed, auditable,
explainable player over the grey market:

1. **Regulation 16A** (effective 29 August 2024) forbids SEBI-regulated entities — including every
   broker — from associating, directly or indirectly, with anyone giving unregistered securities
   advice or making return claims. Broker distribution is now **legally closed** to unregistered
   competitors.
2. **The retail algo framework** (circular 4 February 2025, in force **1 April 2026**) makes the
   broker the principal, requires exchange empanelment of providers, bars open APIs, and requires
   **black-box providers to register as Research Analysts and file a research report per algorithm**.
   The compliance floor just rose above what a hobbyist algo shop can clear.
3. **AI/ML governance is coming.** SEBI's consultation paper of 20 June 2025 proposes principles of
   Equality, Accountability, Transparency and Safety & Reliability, plus plain-language disclosure of
   a model's purpose, risks, accuracy and limitations. It is still a draft as of August 2026, and the
   SEBI Chairman has said guidelines will be issued.

**Strat AI's architecture was, accidentally, built to the draft framework's specification.**
Glass-box streaming of every tool call is auditability. "Honest failure over fabrication" — returning
a structured *unavailable* marker instead of inventing a value — is safety and reliability. The
bounded reasoning budget with forced HOLD is reliability. The journal that tracks realised expectancy
per setup type is model monitoring. When those guidelines become binding, our competitors will be
rebuilding; we will be documenting what already exists. **That is the single most defensible thing
about this company and it should lead the pitch.**

### The base is large and growing

- NSE unique PAN-holder investors crossed **128 million by February 2026**, more than 4x the ~31
  million of FY20 ([India Fintech](https://indiafintech.substack.com/p/indias-capital-markets-the-financialization)).
- Demat accounts crossed **190–200 million by FY25** ([Axis MF](https://www.axismf.com/mutual-fund-knowledge-centre/articles/demat-accounts-growth-real-winners)).
- India's securities brokerage market is estimated at **USD 6.98 billion in 2026, reaching USD 13.09
  billion by 2031 (13.33% CAGR)** ([Mordor Intelligence](https://www.mordorintelligence.com/industry-reports/india-securities-brokerage-market)).
- India's online trading platform segment is projected to reach **USD 1.42 billion by 2033 at a 10.5%
  CAGR from 2026** ([Grand View Research](https://www.grandviewresearch.com/horizon/outlook/online-trading-platform-market/india)).

### Supply of licensed research is absurdly thin

As of 31 August 2024, SEBI counted **927 Investment Advisers and 1,380 Research Analysts** serving an
investor base of more than **12 crore** ([SEBI board memo, October 2024](https://www.sebi.gov.in/sebi_data/meetingfiles/oct-2024/1728550911419_1.pdf)).
Roughly one licensed research provider per 87,000 investors. Demand has been met by unregistered
Telegram channels for a decade, and Reg 16A plus SEBI's AI-driven social-media surveillance — which
has flagged around **20,000 fraudulent posts since November 2025** — is now actively removing that
supply. A licensed, software-native entrant is walking into a supply vacuum.

---

## 3. Market sizing

Third-party market reports are directionally useful but measure the wrong unit. Here is the bottom-up
build. **Assumptions are labelled; do not present them as data.**

| Layer | Basis | Figure |
| --- | --- | --- |
| **TAM** — investors reachable | 128M unique NSE PAN investors (Feb 2026, cited) | 128,000,000 |
| **Active-trader core** | 78.6 lakh unique individual EDS traders in FY26 (cited) + an assumed comparable cohort of active intraday/positional cash-segment traders **[ASSUMPTION: 1.0x]** | ≈ 15,700,000 |
| **SAM** — traders with willingness to pay for decision tools | **[ASSUMPTION: 10% of active core]** — benchmarked against typical paid-tool penetration in retail trading | ≈ 1,570,000 |
| **Blended ARPU** | ₹2,500/month across TERMINAL and RESEARCH SKUs, weighted to the lower tier **[ASSUMPTION]** | ₹30,000/year |
| **SAM value** | 1.57M × ₹30,000 | **≈ ₹4,700 crore (≈ USD 540M)** |
| **SOM — 5-year target** | **[ASSUMPTION: 3% of SAM]** | ≈ 47,000 paying users, **≈ ₹140 crore ARR** |

Sanity check: our ₹4,700 crore SAM sits meaningfully above Grand View's ~USD 1.42 billion (≈ ₹12,500
crore) 2033 estimate for the whole India online trading platform market, which suggests our SAM is
roughly a third of that market — plausible for the analytics-and-research slice, but present it as a
range, not a point.

**The headroom on price is unusual.** SEBI caps RA fees at **₹1,51,000 per annum per family** for
individual and HUF clients across all research services of the RA
([Taxmann](https://www.taxmann.com/post/blog/sebi-guidance-on-ra-fee-cap-for-individual-and-huf-clients)).
At ₹2,500/month we are at **20% of the regulatory ceiling**. There is 5x pricing headroom before the
cap binds, and the cap is revised every three years on the Cost Inflation Index. Since April 2025 we
may also collect **up to one year of fees in advance**, so annual prepaid plans — and the cash-flow
profile that comes with them — are permitted.

---

## 4. Business model — three tracks, deliberately sequenced

| Track | Product | Price | Registration | Gross margin | Role |
| --- | --- | --- | --- | --- | --- |
| **A · TERMINAL** | Charts, Ghost Lines, order-flow footprint, volume profile, indicators, regime, VWEPR, S/R, patterns. **No directional calls** | ₹999–₹1,999/mo | **None** | ~85% | Revenue from day one, while the RA licence is pending. Top of funnel |
| **B · PLATFORM (B2B2E)** | White-labelled terminal + research engine licensed to brokers and existing SEBI-registered RAs. The licensee carries the client-facing obligation | ₹25L–₹1.5cr/yr + rev-share | **None for us** | ~90% | Fastest scale, lowest compliance load, best logos. Reg 16A is the sales pitch |
| **C · RESEARCH** | FIND, DEBATE, conviction score, journal — delivered as compliant research reports | ₹3,999–₹7,999/mo | **RA (INH)** | ~75% | Highest ARPU, highest defensibility, gated on licence |

> **The prices above are modelling ranges, not the price sheet.** `GO_TO_MARKET.md` §5 carries the
> operative numbers — TERMINAL Pro ₹1,499/mo · ₹14,999/yr, RESEARCH ₹4,999/mo · ₹49,999/yr — and it
> governs anything customer-facing. Do not take a figure from this table onto a pricing page.
>
> **Tracks A and C are now real code, not a plan.** `TERMINAL` and `RESEARCH` are enforced values in
> `frontend/src/lib/sku.ts` and `agents/deep-quant-loop/entitlements.py`, both failing closed
> (`PLAN_OF_ACTION.md` §4.2, P1). That is what makes the sequencing argument below verifiable rather
> than asserted — but note the remote entitlement endpoint the server gate calls **does not exist
> yet**, so Track C cannot be sold to a real user until the auth deployment provides it.

**Why the sequencing matters to an investor:** the SEBI application takes 3–6 months and is not fully
in our control. Tracks A and B generate revenue during that window, so the licence timeline sits off
the revenue critical path. This is the first question a sophisticated fintech investor will ask, and
we have a structural answer rather than a hope.

### Indicative plan — assumptions, not forecasts

| | Y1 | Y2 | Y3 | Y5 |
| --- | --- | --- | --- | --- |
| TERMINAL paying users | 1,200 | 5,500 | 16,000 | 42,000 |
| RESEARCH paying users | 0 | 900 | 4,200 | 14,000 |
| Platform partners (brokers/RAs) | 2 | 5 | 9 | 16 |
| Direct ARR (₹ cr) | 3.5 | 18 | 62 | 175 |
| Platform ARR (₹ cr) | 1.0 | 4.5 | 11 | 28 |
| **Total ARR (₹ cr)** | **4.5** | **22.5** | **73** | **203** |
| Compliance + regulatory cost (₹ cr) | 0.25 | 0.4 | 0.7 | 1.5 |

Blended CAC assumption ₹1,800–₹3,000 for TERMINAL via content and community, materially higher for
RESEARCH given KYC friction. Payback under 12 months at TERMINAL pricing. **[ALL ASSUMPTIONS]**

---

## 5. Moat

Ranked by durability, not by how impressive it sounds.

1. **The calibration dataset (compounding, and unmatched).** The journal records every committed
   recommendation, whether it hit target or stop, and computes win rate and expectancy in R-multiples
   **per setup type** — then feeds that back to lower conviction on setups with historically negative
   expectancy. Every month of operation makes the next month's output better. No competitor holds a
   labelled dataset of AI-generated Indian-market recommendations tagged by setup with realised
   outcomes, and none can buy one. This is the asset that gets more valuable while we sleep.
   - **Blocker P6 removed the *display* of win rate and expectancy, not the computation.** They stayed
     inside `journal.py` as calibration input, which is where the draft AI framework wants them as
     model monitoring; only the user-facing panel changed. The moat is intact and is now also *defensible*
     — a performance figure on a dashboard is an advertisement, a performance figure feeding a
     confidence adjustment is a control.
   - **Since 19 August 2026 the dataset has a second, stronger layer.** Every committed decision is
     also written to an append-only, hash-chained store with the model id, the prompt hash and the
     full tool-input snapshot that produced it (`PLAN_OF_ACTION.md` §4.2, P2). That makes each row
     *replayable*, not merely labelled — which is what turns the dataset from a training asset into an
     evidentiary one. Caveat worth stating to a diligence team before they find it: the chain has no
     external witness yet, so it proves internal consistency rather than third-party attestation.
2. **Regulatory position.** An INH registration, CSCRF compliance, an AI model governance policy and a
   documented audit trail. Post-April 2026 this is a barrier, not a cost. It also unlocks broker
   distribution that Reg 16A closes to everyone else. **Of the four, two now exist**: the policy
   (`docs/compliance/AI_MODEL_GOVERNANCE.md`) and the audit trail (P2 + P5 hash-chained stores). The
   INH is filed-for-later and CSCRF has not been assessed, so this moat is roughly half-built — the
   half that is engineering is done, the half that is registration is not.
3. **Latency infrastructure.** A Rust binary tick parser reading the exchange WebSocket directly, with
   a dual sink to Kafka/Redpanda for live agents and QuestDB for five years of history, and a native
   Tauri desktop shell streaming to the UI over IPC rather than through a browser. Competitors
   overwhelmingly sit on broker REST polling. This is hard to copy and it is the substrate the whole
   product stands on.
4. **Proprietary signal work.** VWEPR quadratic curvature; a regime classifier combining ADX,
   choppiness, ATR percentile and Bollinger width; 19 chart patterns with confidence scoring; a
   volatility-aware directional forecaster conditioned on regime. Individually replicable, collectively
   a multi-year research effort.
5. **Architecture as trust.** Adversarial self-critique, the Bear Agent, the conflict-forced HOLD, the
   ≥1.5× ATR stop floor, the R:R floor enforced identically in Python and Rust, and the bounded
   execution budget that always terminates. This is the product's brand.

**Honest assessment of what is *not* a moat:** the LLM layer. Model access is a commodity and
getting cheaper. Our defensibility is the data, the pipeline, the licence and the risk architecture —
never the model.

---

## 6. Competition

| Player | Strength | Gap we exploit |
| --- | --- | --- |
| **Sensibull** | Dominant in options, default for Zerodha users, **SEBI-registered Research Analyst**, superb broker distribution | Options-payoff-centric. No AI reasoning layer, no pre-trade adversarial veto, no cross-asset conviction fusion |
| **Streak (Zerodha)** | Massive built-in distribution, no-code strategy builder | Rule-based backtest/deploy. No reasoning, no news fusion, no risk audit of a discretionary idea |
| **Tradetron / AlgoTest** | Strong algo deployment and backtesting | Execution and testing infrastructure, not decision support. Squarely inside the new algo framework's compliance burden |
| **StockEdge / Trendlyne / Definedge** | Deep fundamental and scan data, large audiences | Screening and data, not trade-level risk adjudication |
| **TradingView** | Best-in-class charting, global network effects | Not India-specific, no NSE microstructure or options-chain edge, no SEBI research licence |
| **Unregistered Telegram/WhatsApp signal groups** | Zero friction, enormous reach | Being actively dismantled by Reg 16A and SEBI's AI social-media surveillance. Their users are our inbound pipeline |

**Our category is empty.** Everyone else answers "what should I trade?" or "how do I automate it?"
Nobody answers **"should I take *this* trade, and what will kill it?"** with a full audit trail. The
positioning is *pre-trade risk adjudication*, and we should name and own that category.

---

## 7. Risks, stated plainly

An investor will find these anyway. Naming them first is worth more than hiding them.

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **The regulator is deliberately shrinking our core TAM.** SEBI's curbs cut unique EDS traders from 98.1 lakh to 78.6 lakh in one year, and that is the stated intent, not a side effect | **High** | Do not build a company whose only customer is an F&O scalper. Weight product and GTM toward the **Swing and Investor profiles** — multi-day and allocation horizons that SEBI is not trying to suppress — and toward **B2B2E**, where our revenue tracks broker platform spend rather than retail speculation volume |
| **Regulatory rejection or delay of the RA application** | High | Tracks A and B are revenue-generating without registration. Engage securities counsel pre-filing. Do not put a licence date on the critical path |
| **AI/ML guidelines land stricter than the draft** — e.g. mandatory reproducibility for client-facing models | Medium-High | **Mitigation shipped 19 Aug 2026.** The LLM stays out of the order path — structurally, not by policy: the internal `BrokerProvider` trait has no order method (blueprint §1.3, P14). Every committed output carries a versioned model id and prompt hash in an append-only store, so any output is replayable (P2). The AI disclosure page is drafted but **not yet published** — its blockers are the INH number and three counsel questions, not engineering |
| **Single-broker dependency (Zerodha Kite) for ticks and execution** | Medium-High | Adapter seam shipped (`e1caf32`): `MarketDataProvider` / `BrokerProvider` traits selected by env, so a second feed is a new file rather than a refactor. **Still a genuine single point of failure today** — Kite remains the only implementation. Adding a second feed and a second broker before Series A is now weeks of work, which is the actual change in this row |
| **Model risk — a visibly wrong call at scale** | Medium-High | The risk architecture *is* the mitigation, and it must be enforced, tested and never bypassable — it is: the ≥1.5× ATR stop floor and the per-profile R:R floor are applied by a deterministic validator that the model cannot outvote. Never advertise performance. **Correction to this row's earlier wording:** "publish accuracy honestly" is now an open **[COUNSEL]** question rather than a commitment — the draft AI framework asks for accuracy disclosure while the advertisement code restricts performance claims, and the two pull against each other. Pending resolution we disclose limitations and withhold the figure (blueprint §4.2 item 3) |
| **Structural churn: most retail traders lose money and quit** | Medium-High | Retention has to come from *capital preservation*, not from wins. "We stopped you taking 14 bad trades this quarter" is the retention metric to instrument and surface — **shipped** as the Discipline Metrics panel (`GO_TO_MARKET.md` §4, blocker P6), though the wording of any such summary is still **[COUNSEL]** |
| **LLM inference cost per user** | Medium | Cache aggressively; run cheap deterministic tools first and reserve LLM calls for synthesis; monitor cost per FIND run as a first-class product metric |
| **Reg 16A liability via marketing partners** | Medium | Screen and contractually bind every affiliate and influencer. One unregistered partner making a return claim is an enforcement vector into a licensed entity. **Not started** — and it is the cheapest unaddressed risk on this list |
| **CSCRF audit failure / data breach** | Medium | Gap assessment before launch — not yet commissioned. Secret-hygiene remediation (blueprint §5.1 P7) is **half done**: credential files untracked and a rotation runbook published, but two credentials are still unrotated, git history was deliberately left intact, and no managed secret store is in use. An auditor will ask for the rotation log in `SECRET_ROTATION_RUNBOOK.md` §6, which currently has empty cells |

---

## 8. The ask

**Seed: ₹10 crore (≈ USD 1.15M) for 18–24 months of runway.** Indicative allocation:

| Use | Share | Detail |
| --- | --- | --- |
| Engineering | 40% | Remaining compliance backlog — **P3** report renderer, **P4** KYC + family fee-cap gating, **P8b** analyst-of-record workflow, **P9** personal-trading surveillance, **P10** advertisement register, **P13** grievance module — plus execution hardening, a second data feed, and mobile. P1, P2, P5, P6, P8a and P14 shipped pre-seed (`PLAN_OF_ACTION.md` §4.2) |
| Regulatory & compliance | 12% | Counsel, RA registration, CSCRF, DPDP, Compliance Officer, first audits |
| Go-to-market | 25% | Content, community, broker BD for the B2B2E track |
| Infrastructure & LLM inference | 13% | QuestDB, Kafka, model inference, monitoring |
| Reserve | 10% | |

**Milestones this round buys:** INH registration granted and RAASB enlistment complete; TERMINAL SaaS
at ₹3.5 crore+ ARR; two signed broker platform deals; CSCRF audit passed; the AI governance and
disclosure stack published and defensible; 12 months of labelled recommendation-outcome data in the
journal — which is the asset the Series A story is actually built on.

---

## 9. Sources

- [SEBI study on individual traders in the equity derivatives segment, July 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jul-2025/1751900802566.pdf)
- [Business Standard, FY26 F&O loss and trader-count data as reported to Parliament, August 2026](https://www.business-standard.com/amp/markets/news/sebi-measures-reduce-equity-f-o-losses-for-retail-investors-in-fy26-126081101071_1.html)
- [Business Today, FY26 unique F&O investor decline and per-person loss, August 2026](https://www.businesstoday.in/markets/story/unique-individual-investors-in-fo-trading-declines-in-fy26-but-average-per-person-loss-widens-548559-2026-08-11)
- [India Fintech, NSE unique PAN-holder growth to February 2026](https://indiafintech.substack.com/p/indias-capital-markets-the-financialization)
- [Axis MF, demat account growth through FY25](https://www.axismf.com/mutual-fund-knowledge-centre/articles/demat-accounts-growth-real-winners)
- [Mordor Intelligence, India securities brokerage market](https://www.mordorintelligence.com/industry-reports/india-securities-brokerage-market)
- [Grand View Research, India online trading platform outlook](https://www.grandviewresearch.com/horizon/outlook/online-trading-platform-market/india)
- [SEBI board memo, IA and RA population as of 31 August 2024](https://www.sebi.gov.in/sebi_data/meetingfiles/oct-2024/1728550911419_1.pdf)
- [SEBI Consultation Paper, Guiding Principles for Responsible usage of AI/ML, 20 June 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jun-2025/1750415065695.pdf)
- [Taxmann, SEBI guidance on the RA fee cap](https://www.taxmann.com/post/blog/sebi-guidance-on-ra-fee-cap-for-individual-and-huf-clients)
- [Sensibull — SEBI-registered Research Analyst](https://sensibull.com/index.html)
- SEBI circular, *Safer participation of retail investors in Algorithmic trading*, 4 February 2025
- SEBI (Intermediaries) (Amendment) Regulations, 2024 — Regulation 16A

*Content from external sources was rephrased for compliance with licensing restrictions.*
