# Strat AI — Go-To-Market Under SEBI Constraints

**Version 1.0 · August 2026**

> Marketing a securities product in India is a constrained optimisation problem, not a creative one.
> Almost every high-converting message available to a US fintech — returns, win rates, testimonials,
> backtests — is either prohibited or heavily conditioned here. This document defines what we *can*
> say, and turns the constraint into the positioning.
> *Content from external sources was rephrased for compliance with licensing restrictions.*

---

## 1. The constraint map

Read this before writing a single line of copy.

| Constraint | Source | What it kills |
| --- | --- | --- |
| **No assured or implied returns** — express or implied, in respect of securities | SEBI (Intermediaries) (Amendment) Regulations, 2024; Reg 16A, effective 29 Aug 2024 | "Beat the market", "consistent profits", "our users averaged X%", profit screenshots, P&L testimonials |
| **No association with unregistered advice or return claims** — direct *or indirect* | Reg 16A | Any affiliate, influencer, reseller or community moderator who gives stock calls or claims returns. Terminate and screen all of them |
| **Registration ≠ approval or performance guarantee** | RA Regulations; MITC | "SEBI approved", "SEBI certified", "SEBI-approved algo" (which does not exist in any form) |
| **Advertisement code + record-keeping** | SEBI RA guidelines, 8 Jan 2025 | Untracked creative. Every ad needs an approval trail and a retained record |
| **Advertiser verification on social platforms** | SEBI media release, March 2025 — registered intermediaries had to update contact details on the SEBI SI Portal by 30 April 2025 ([ET](https://economictimes.com/markets/stocks/news/sebi-mandates-advertiser-verification-on-social-media-to-curb-investment-frauds/articleshow/119309602.cms)) | Running securities ads without a verified, registration-backed advertiser identity |
| **Platform-level financial services verification** | [Google Ads financial services verification](https://support.google.com/adspolicy/answer/15332527) requires advertisers to show they are licensed by the relevant regulator, or exempt | Paid search and social for anything that reads as investment advice, until the INH is in hand |
| **Active AI surveillance of financial social content** | SEBI has flagged around 20,000 fraudulent posts since November 2025, targeting guaranteed-return claims, fake certifications and unregistered advice | The assumption that a Telegram post or a Reels caption is unobserved. Assume a machine reads everything |

**The operational consequence:** paid acquisition is effectively gated on the RA registration. Plan a
**content-and-community-led year one**, and treat paid channels as a post-licence unlock. This is not
a workaround; it is the correct read of the channel landscape.

---

## 2. Positioning: sell the refusal

Every competitor sells *more*: more signals, more strategies, more automation. The regulator has
published that in FY26 individual traders lost **₹91,685 crore** and that unique individual F&O
traders fell from 98.10 lakh to 78.60 lakh — fewer traders, worse average loss per head. The market
does not have a shortage of trade ideas. It has a shortage of *veto*.

**Category we are creating:** *pre-trade risk adjudication.*

**Core message:** **"Strat AI's most valuable output is the word no."**

This message is unusual in three useful ways. It is **compliant by construction** — it makes no claim
about returns, only about process. It is **differentiated** — nobody else leads with restraint. And it
is **aligned with the regulator's own stated agenda**, which matters enormously when your licence
application and your marketing are read by the same institution.

### Message hierarchy

| Layer | Claim | Why it is safe |
| --- | --- | --- |
| **Primary** | "It tells you when *not* to trade." | A statement about product behaviour, not outcomes |
| **Secondary** | "Watch it think. Every tool call, every number, every reason — streamed live." | Verifiable product fact. Also pre-answers SEBI's draft AI transparency principle |
| **Secondary** | "Three hard rules it cannot break: stop ≥ 1.5× ATR, reward-to-risk floor by profile, correct level ordering." | Describes a control, not a result. Specific and checkable |
| **Secondary** | "When the tape and the news disagree, it holds." | Describes the capital-preservation guardrail |
| **Supporting** | "Sub-second ticks parsed in Rust. Five years of history. Native desktop, not a browser tab." | Pure engineering claim, zero regulatory surface |
| **Supporting** | "When data is missing, it says so. It never fills the gap with a guess." | The honest-failure principle. Rare and credible |

**This table is the copy spine, and it has been drawn down into shippable text.** See
`docs/compliance/WEBSITE_COPY.md` — every landing-page string, each one traced to the code or commit
that substantiates it, screened against `docs/compliance/BRAND_GUIDELINES.md` §1–§3, and constrained
to pre-licence **TERMINAL-only** positioning. Write website copy from that file, not from this table:
a claim that is safe *as a message* can still be false *of the build*, and only the copy file checks
the second thing.

### The banned list — put this in the brand guidelines and enforce it in review

Never publish: assured/guaranteed/consistent returns · win rate or accuracy as a headline · P&L
screenshots · client testimonials referencing profits · "SEBI approved/certified/approved algo" ·
backtested or hypothetical performance without the disclosures the advertisement code requires ·
"risk-free" · "sure shot" · countdown-timer urgency on a securities subscription · any comparison
implying superior returns to a named competitor or index.

---

## 3. Channel plan

### 3.1 Phase 1 — pre-licence (months 0–6), TERMINAL SKU only

Sell software. Do not publish calls. Do not run paid financial-services ads.

**Engineering-credibility content.** This is our unfair advantage and almost nobody in Indian retail
fintech competes here. Publish deep technical writing:

- How the Rust binary tick parser works, with latency numbers
- Why Ghost Lines are locked to the 10-minute timeframe, and what R² actually tells you
- The regime classifier: ADX, choppiness index, ATR percentile, Bollinger width, and why a regime
  should calibrate aggression rather than block trades
- VWEPR quadratic curvature explained from first principles
- What order flow imbalance measures, and the honest difference between a candle-derived proxy and
  real tick-level OFI — **including that we mark it unavailable rather than defaulting to neutral**
- An open, plain-language AI model disclosure page

This content converts serious traders, earns backlinks, is impossible for a signal-shop to imitate,
and reads to a regulator as exactly what a responsible participant sounds like.

**Community.** A moderated forum or Discord with an absolute rule: **no stock calls, by anyone,
including moderators.** This is a Reg 16A requirement dressed as culture. Enforce it publicly and
loudly — the enforcement itself becomes brand evidence.

**Open-source a peripheral component.** A tick-parsing utility, or the volume-profile renderer.
Developer trust converts into paid quant users, and it costs us nothing strategically because the moat
is the data and the licence, not the parser.

**Founder-led YouTube and long-form.** Teach the method, never call the trade. "Here is how to check
whether your stop can survive today's ATR" is compliant, useful, and sells the product by
demonstrating it.

### 3.2 Phase 2 — post-licence (months 6–18), RESEARCH SKU live

Now paid channels open, because we can complete Google's financial services verification and SEBI's
social advertiser verification.

- **Paid search** on high-intent, non-promissory terms: "ATR stop loss calculator", "risk reward
  calculator NSE", "volume profile India", "order flow terminal India". Never bid on
  return-promise language.
- **Compliant retargeting** to TERMINAL users for the RESEARCH upsell, with a KYC-aware funnel.
- **Displayed prominently everywhere:** entity name, INH registration number, Compliance Officer
  contact, the standard SEBI disclaimer, and the MITC link. These are obligations — but they are also
  trust signals in a market saturated with anonymous Telegram operators. Do not tuck them in the
  footer. Feature them.
- **Advertisement register** live from the first paid rupee: every creative, its approver, its live
  dates, and its retained copy.

### 3.3 Phase 3 — B2B2E platform (starts month 2, runs forever)

The highest-leverage channel and it needs no licence of our own, because the broker or the registered
RA carries the client-facing obligation.

**The pitch to a broker is a compliance pitch, not a features pitch.** Their compliance head, not
their product head, is the real buyer. Lead with:

1. **Reg 16A.** They legally cannot integrate an unregistered advice provider. We are structured to be
   integrable — that already eliminates most of the market they are being pitched by.
2. **The audit trail.** Every recommendation carries every input value, the model and prompt version
   hash, and the full reasoning chain. When a client complains — and the broker alone handles
   complaints under the February 2025 framework — they can reconstruct exactly what the client saw and
   why. Nobody else can offer that.
3. **AI governance readiness.** When SEBI's AI guidelines land, our documentation is already written.
   Their integration does not become a remediation project.
4. **The white-box discipline.** Ghost Lines and the risk validators are deterministic and
   reproducible. The reasoning layer stays out of the order path by design, so integrating us does not
   drag them into black-box registration obligations.

Target list, in order: mid-size full-service brokers who need differentiation against the discount
players; existing SEBI-registered RAs who have distribution but no technology; wealth platforms
serving the Investor profile.

---

## 4. Retention: instrument capital preservation, not wins

Most retail traders lose money and churn. If retention depends on winning, the business is built on
sand. Build it on *avoided loss* instead.

**Metrics to instrument and surface in-product** (internal analytics and a user-facing dashboard —
never as marketing copy):

- Trades the user proposed that VERIFY rejected, with the reason
- Aggregate risk the user did not take because of a rejected stop or a sub-floor R:R
- Count of forced HOLDs during conflicted technical/sentiment conditions
- Discipline score: how often the user followed their own stated plan

**Shipped, three of four** (`51c457a`, blocker P6). The in-product surface is now headed **"Discipline
Metrics"** and shows Setups Audited, Setups Rejected, Forced HOLDs and Plan Adherence. The four
performance figures that were there before — Total Return, Win Rate, Max Drawdown and Avg Conviction —
are gone, and the bull/bear colouring that made the panel read like a P&L went with them. Win rate and
expectancy survive **inside** `journal.py` as calibration input, which is where the draft AI framework
wants them.

Two things to know before this becomes copy:

- **Plan Adherence renders `—`, not a number.** Its counter has no data yet. That em-dash is the
  deliberate honest-empty pattern, not a bug — a fabricated zero on a discipline metric would be the
  same class of defect as a fabricated return.
- **The quarterly-summary sentence below is still unapproved.** It is the one piece of §4 that is
  outward-facing.

A quarterly in-product summary — "your plan was audited 46 times; 14 setups failed the volatility or
R:R floor" — is a factual statement about product usage. It contains no performance claim, and it is
the most powerful retention artefact available to us. **[COUNSEL — review the exact wording of any
user-facing summary before it ships.]**

Weight the roadmap and the messaging toward the **Swing** and **Investor** profiles. SEBI is
deliberately compressing intraday F&O participation and succeeding at it; building GTM primarily
around the scalper means growing into a shrinking pool.

---

## 5. Pricing

| SKU | Price | Registration | Note |
| --- | --- | --- | --- |
| TERMINAL Free | ₹0 | None | Delayed data, single watchlist. **No FIND, no DEBATE, no directional output** — free tiers must never carry recommendations |
| TERMINAL Pro | ₹1,499/mo · ₹14,999/yr | None | Full analytics, Ghost Lines, footprint, volume profile, VWEPR |
| RESEARCH | ₹4,999/mo · ₹49,999/yr | RA required | FIND, DEBATE, conviction score, journal, QA mode |
| PLATFORM | ₹25L–₹1.5cr/yr + rev-share | None (licensee's obligation) | White-label |

**The SKU names are now code, not just a price sheet.** `TERMINAL` and `RESEARCH` are the two values of
`Sku` in `frontend/src/lib/sku.ts`, and the mapping this table implies is enforced there and in
`agents/deep-quant-loop/entitlements.py`: FIND, DEBATE and QA are RESEARCH; **VERIFY is TERMINAL**,
because it checks arithmetic the user supplied rather than producing a view. Both gates fail closed —
a null, absent or malformed entitlement resolves to TERMINAL.

**Pre-licence, only the first two rows are sellable.** RESEARCH must not be advertised, priced on a
public page, or offered as a waitlist that implies advice is coming on a date. The entitlement source
it depends on does not exist yet either (`PLAN_OF_ACTION.md` §4.2, P1), so today `SKU_ENFORCE=1` denies
all RESEARCH traffic — which is the correct posture, not a bug to work around.

Annual prepay is permitted: since April 2025 SEBI allows RAs to collect advance fees covering up to
one year. At ₹49,999/year the RESEARCH tier sits at roughly **one third of the ₹1,51,000 per-family
annual fee ceiling**, leaving real headroom — but note the cap is **per family across all research
services**, so a household with two subscriptions must be tracked against one ceiling.

---

## 6. Launch sequence

**Month 0–1** · Freeze and audit every existing marketing asset against §1. Publish brand guidelines
with the banned list. Ship the TERMINAL/RESEARCH SKU split so the free and paid tiers carry no
recommendations. Screen and, where needed, terminate every affiliate and influencer relationship.
→ **Three of four done** (`876bbf0`, `51c457a`, `bf0c885`). Brand guidelines published at
`docs/compliance/BRAND_GUIDELINES.md`; in-product copy audited and scrubbed; SKU split shipped and
test-verified on both sides of the API. **Outstanding: the live website itself** — the audit covered
the repo, and the website is not in the repo. `docs/compliance/WEBSITE_COPY.md` is the replacement
copy, and affiliate/influencer screening has not started.

**Month 1–3** · Begin the engineering-credibility content engine — two deep pieces a month. Launch the
moderated no-calls community. Publish the AI model disclosure page. Open broker conversations led by
compliance, not features.
→ **The disclosure page is drafted but cannot be published in this window.** `docs/compliance/AI_DISCLOSURE.md`
§8 lists five blockers, of which the entity name, registration number and Compliance Officer contact
all depend on the INH. Do not slip this item quietly — publishing it with placeholders visible is
itself a disclosure defect. Reschedule to Month 6–9, alongside the INH wiring.

**Month 3–6** · TERMINAL Pro paid launch. Open-source one peripheral component. First broker pilot
signed. Stand up the advertisement register ahead of needing it.

**Month 6–9** · On INH grant: wire the registration number, Compliance Officer contact, disclaimer and
MITC into every surface. Complete Google financial services verification and SEBI SI Portal advertiser
verification. Launch RESEARCH with the KYC-gated funnel. Open paid search on non-promissory terms.

**Month 9–18** · Scale the platform track. Ship the capital-preservation retention dashboard. Publish
the first honest, disclosure-compliant accuracy report — voluntarily, before anyone requires it.

---

## 7. Sources

- SEBI (Intermediaries) (Amendment) Regulations, 2024 — Regulation 16A, effective 29 August 2024
- SEBI guidelines for Research Analysts, circular dated 8 January 2025
- [SEBI media release on social-media advertiser verification, March 2025](https://economictimes.com/markets/stocks/news/sebi-mandates-advertiser-verification-on-social-media-to-curb-investment-frauds/articleshow/119309602.cms)
- [Google Ads — financial services verification policy](https://support.google.com/adspolicy/answer/15332527)
- [Google Ads — financial products and services policy](https://support.google.com/adspolicy/answer/2464998)
- SEBI circular on relaxation of advance-fee restrictions for IAs and RAs, April 2025
- SEBI FY26 equity derivatives loss and participation data as reported to Parliament, August 2026
- SEBI circular, *Safer participation of retail investors in Algorithmic trading*, 4 February 2025

*Content from external sources was rephrased for compliance with licensing restrictions.*
