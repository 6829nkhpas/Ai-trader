# Strat AI — Master Plan of Action

**From prototype to a licensed, multi-jurisdiction, investable business**
**Version 1.0 · August 2026 · 30-month horizon**

> **Not legal advice.** Every item marked **[COUNSEL]** requires sign-off from qualified counsel in
> the relevant jurisdiction before execution. Items marked **[VERIFY]** are figures or procedures that
> change and must be confirmed against the primary source at the time of filing. Read with
> `SEBI_COMPLIANCE_BLUEPRINT.md`, `INVESTOR_BRIEF.md` and `GO_TO_MARKET.md`.
> *Content from external sources was rephrased for compliance with licensing restrictions.*
>
> **Status · 19 August 2026.** The seven Phase-0 engineering blockers have shipped on `develop`.
> **§4.2 is the canonical status table** — it carries the commit for each blocker and, more
> importantly, the gap each one still has. Nothing in Part 4 should be read as complete without
> checking it there. Gate 0→1 currently stands at one criterion of five.

---

## Part 0 — The governing principle

**You do not have to weaken the product to become compliant. You have to re-package it.**

Every feature in the blueprint survives. Regulators in India do not prohibit sophisticated analysis,
AI reasoning, adversarial critique or conviction scoring. They prohibit **unlicensed
recommendations**, **untraceable decisions**, **performance claims** and **unrecorded client
interactions**. Those are four properties of *delivery*, not four features of *analysis*.

Three reframes carry the entire plan:

1. **Your 15-step FIND pipeline is not a compliance liability — it is the "rationale and risk factors"
   section that SEBI requires in every research report.** Most registered analysts write that section
   by hand and thinly. You generate it exhaustively and automatically.
2. **Your Bear Agent is not a legal problem — it is the mandated risk-factor disclosure.** You built,
   by accident, the thing the regulation asks for.
3. **Your glass-box streaming is not just a UX choice — it is the audit trail that satisfies both the
   research-report record rule and SEBI's draft AI transparency principle.**

The work ahead is: put a licence around the output, an immutable record under it, a KYC gate in front
of it, and disciplined language on top of it.

---

## Part 1 — Feature Preservation Matrix

The commercial question first: what happens to each thing a trader would pay for.

| # | Core feature | Regulatory issue | Verdict | What actually changes |
| --- | --- | --- | --- | --- |
| 1 | **FIND mode** — autonomous trade discovery with direction, entry, SL, TP | Research recommendation → RA licence | **KEEP 100%** | Wrapped as a compliant research report: publication timestamp, price at publication, holding period, risk factors, INH number, disclosures. Analyst-of-record sign-off. Immutable record. **Zero analytical content removed** |
| 2 | **The 15-step pipeline** — macro alignment, microstructure, regime, relative strength, order flow, volume profile, S/R, 19 patterns, VWEPR, forecaster, news, session, options chain, event gate, track-record calibration | None. Analysis is never the problem | **KEEP 100%, UNCHANGED** | Becomes the rationale section. It is an asset, not a risk |
| 3 | **VERIFY mode** — ATR stop check, R:R floor, level ordering, management-plan validation | Validation of the *user's own* numbers | **KEEP 100%** | Lives in the unregulated TERMINAL SKU. Output framed as *risk factors present in your plan*, not *do not trade* **[COUNSEL]** |
| 4 | **Bear Agent / devil's advocate** | Arguably a negative recommendation | **KEEP 100%** | Delivered inside the RESEARCH SKU as the mandated risk-factor disclosure. This is the feature regulation *wants* |
| 5 | **DEBATE mode** — Bull/Bear/Judge, consensus classification, conviction formula | Recommendation | **KEEP 100%** | Publish the conviction formula (70% winner strength + 30% separation − 25 contested penalty). Publishing converts an opaque score into an explainable model and pre-satisfies the AI transparency principle |
| 6 | **QA mode** — interactive follow-up questions | Risk of drifting into personalised advice (IA territory) | **KEEP, with one new guard** | Add a **personalisation refusal guardrail**: the agent must decline to tailor answers to the user's capital, income, goals or existing portfolio. Every turn logged as a client interaction, 5-year retention. Committed-decision immutability already exists — keep it |
| 7 | **Glass-box live streaming of reasoning** | None | **KEEP AND AMPLIFY** | Becomes headline marketing *and* your primary compliance evidence |
| 8 | **Ghost Lines** — OLS regression projection with R², locked to 10-minute | None. Deterministic, reproducible, self-limiting | **KEEP 100%, UNCHANGED** | Nothing. Document it as a white-box indicator so it can never be dragged into black-box obligations |
| 9 | **Conviction Score 1–100** | Marketing risk (implied performance) | **KEEP 100%** | Never presented as a probability of profit or expected return. Described as a composite agreement measure with published methodology |
| 10 | **Conflict → forced HOLD guardrail** | None | **KEEP AND PROMOTE** | Elevate to a named, unit-tested, documented, non-bypassable risk control in the compliance manual |
| 11 | **Order-flow footprint, volume profile, Level-2 depth** | None. Visualisation | **KEEP 100%** | Nothing |
| 12 | **Three trading profiles** with per-profile R:R floors (1:1.3 intraday, 1:2 swing/investor) | None | **KEEP 100%** | Document as the profile-based risk policy. Weight roadmap toward Swing and Investor (see §7.4) |
| 13 | **Trade journal — self-calibrating track record, win rate, expectancy per setup** | External display = performance claim | **KEEP 100% INTERNALLY** | Internally it is model monitoring, which the AI framework wants. Externally, swap the surface: show the user **their own discipline statistics** (setups rejected, risk avoided, plan adherence) instead of the model's win rate. Retention value preserved; performance claim eliminated |
| 14 | **Broker integration** — margin, positions, live P&L, order status | Read-only is execution support, not advice | **KEEP 100%** | Nothing today. If order *placement* is added: white-box only, broker as principal, static IP, vendor-client API key (see §7.5) |
| 15 | **Rust tick pipeline, Kafka/Redpanda, QuestDB, Tauri desktop, 5-yr history** | None directly | **KEEP 100%** | Brought inside CSCRF critical-systems scope. Secret hygiene remediated immediately |
| 16 | **Anomaly agent** — ≥2% move detection → LLM headline and commentary | Auto-published market commentary | **KEEP 100%** | Commentary must carry the same disclaimers as research. Never phrase as an implied call to act |

**Net product change: one new guardrail (#6), one surface swap (#13), and packaging around #1–#5.
Nothing is deleted.**

---

## Part 2 — The regulatory chassis

Build this structure once. Everything else plugs into it.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  STRAT AI TECHNOLOGIES PVT LTD  ("TechCo")            UNREGULATED         │
│  Domestic India · Companies Act 2013                                      │
│  • Owns 100% of IP: Rust pipeline, VWEPR, Ghost Lines, agents, UI         │
│  • Raises all equity. Holds the cap table and ESOP pool                   │
│  • Sells TERMINAL SaaS (analytics, no calls) — India + worldwide          │
│  • Licenses tech to brokers, RAs and its own subsidiaries at arm's length │
└──┬────────────────┬────────────────────┬───────────────────┬──────────────┘
   │                │                    │                   │
   ▼                ▼                    ▼                   ▼
┌───────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ RESEARCH  │  │ IFSC UNIT    │  │ CRYPTO           │  │ OFFSHORE         │
│ PVT LTD   │  │ GIFT City    │  │ PVT LTD          │  │ (later)          │
│           │  │              │  │                  │  │                  │
│ SEBI RA   │  │ IFSCA        │  │ No SEBI licence  │  │ UAE / SG / EU    │
│ (INH…)    │  │ Research     │  │ (crypto ≠        │  │ as revenue       │
│           │  │ Entity       │  │  security)       │  │ justifies        │
│ Indian    │  │              │  │                  │  │                  │
│ residents │  │ Non-residents│  │ India crypto     │  │ Tier 4–5         │
│ Phase 1   │  │ + NRIs       │  │ research         │  │                  │
│           │  │ Phase 3      │  │ Phase 2          │  │                  │
└───────────┘  └──────────────┘  └──────────────────┘  └──────────────────┘
```

**Why this shape**

- **Change of control.** A SEBI-registered intermediary requires prior SEBI approval for a change in
  control. Keeping the licence in a subsidiary means a priced round into TechCo does not put a
  regulator on your closing checklist. **[COUNSEL — confirm your specific shareholding chain does not
  itself constitute indirect change of control.]**
- **Blast radius.** Inspection, cyber audit and record-production duties attach to the registered
  entity. Contain them.
- **Crypto is a different regulator entirely** (or none). Do not contaminate the SEBI-registered
  entity with VDA activity, and do not let VDA activity inherit SEBI obligations it does not owe.
- **GIFT City is a separate legal territory.** An IFSC unit needs its own vehicle and its own Letter
  of Approval.
- **One IP owner.** All licences flow from TechCo. Clean for diligence, clean for transfer pricing,
  clean for an eventual acquisition of any single arm.

---

## Part 3 — Phase plan with hard gates

Six phases. **Do not start a phase before its gate clears.** Dates are relative to Day 0.

| Phase | Window | Objective | Gate to exit |
| --- | --- | --- | --- |
| **0 · Foundation** | Day 0–45 | Legally clean, revenue-capable, no unlicensed activity | TERMINAL SKU live and selling; zero recommendation surfaces exposed to unlicensed users; all secrets rotated |
| **1 · India licence** | Day 30–210 | SEBI RA registration granted | INH number issued + RAASB enlistment complete + RESEARCH SKU live |
| **2 · Crypto** | Day 90–270 | Crypto product live in India | Counsel opinion in hand that the product is not a notified VDA activity; crypto SKU selling |
| **3 · Global via GIFT** | Day 180–450 | IFSC unit serving non-residents | IFSCA Research Entity registration + first non-resident client |
| **4 · Anglo-market entry** | Day 360–630 | US and UK via publication exclusions | Counsel opinions secured; product guardrails shipped; first paying US/UK users |
| **5 · Licensed offshore** | Day 540–900 | UAE, then Singapore / EU as revenue justifies | Licence granted in at least one Gulf or APAC jurisdiction |

---

## Part 4 — Phase 0: Foundation (Day 0–45)

Nothing here needs a regulator's permission. All of it is blocking.

### 4.1 Corporate (Day 0–20) · Owner: Founder + Company Secretary

| # | Step | Detail |
| --- | --- | --- |
| 0.1 | **Incorporate TechCo** | Private Limited, Companies Act 2013. Name reservation via SPICe+ Part A, then Part B with MoA/AoA, DIN for directors, PAN, TAN. Objects: software development and licensing. **Do not** put "investment advice" or "research services" in TechCo's objects |
| 0.2 | **Incorporate ResearchCo** | Private Limited, wholly or majority owned by TechCo. Objects **must** cover research services / research analyst activity **[COUNSEL]** |
| 0.3 | **Assign IP to TechCo** | Founder-to-company IP assignment deeds for all pre-incorporation code, models and designs. Diligence killer if missed |
| 0.4 | **IP licence: TechCo → ResearchCo** | Arm's-length royalty. Transfer-pricing opinion if any non-resident shareholding **[COUNSEL]** |
| 0.5 | **Shared-services agreement** | Engineering, infra and support provided by TechCo to ResearchCo, priced and documented |
| 0.6 | **Founder agreements, ESOP pool, vesting** | Do before the round, not during |
| 0.7 | **Open bank accounts, GST registration, Startup India / DPIIT recognition** | DPIIT recognition unlocks tax and procurement benefits and costs nothing |
| 0.8 | **Trademark "Strat AI"** | Class 9 (software) and Class 36 (financial services). File in India now; Madrid Protocol later for target jurisdictions |

### 4.2 Engineering — the seven blockers (Day 0–45) · Owner: CTO

Full detail in `SEBI_COMPLIANCE_BLUEPRINT.md` §5.1. Summary and current state:

> **All seven mechanisms shipped on `develop` on 19 August 2026.** The Status column below is the
> canonical record of what is closed and what is not; every other document in `docs/` cites this
> table rather than restating it. **Shipped ≠ closed.** Four of the seven carry a residual gap that
> is a Gate 0→1 item in its own right, and they are named here rather than in a footnote, because a
> table that reads all-green is how a control gets treated as done when it is not.

| # | Task | Why blocking | Status |
| --- | --- | --- | --- |
| **P7** | **Secret hygiene first.** Purge `.env`, `keys/`, `bedrock-api-key.txt` from the repo and full git history. Rotate every credential. Move to a managed secret store | A leaked broker credential inside a regulated entity is a reportable cyber incident. Do this before anything else | ⚠️ **Partial** (`8293dda`). `.env` and `keys/` were **never tracked** — the original claim was wrong. `bedrock-api-key.txt` and `scripts/powershell/auth/keys/*.pem` **were** tracked and are now untracked, with `.gitignore` widened. Runbook at `docs/compliance/SECRET_ROTATION_RUNBOOK.md`. **Three things remain: git history deliberately not rewritten (§4 of the runbook records why), two credentials still owed rotation, and no managed secret store exists** |
| **P1** | **SKU split in code.** `TERMINAL` (analytics, Ghost Lines, footprint, volume profile, indicators, regime, VWEPR, S/R, patterns, VERIFY maths) vs `RESEARCH` (FIND, DEBATE, conviction, journal). Entitlement-gated at the API layer, not the UI | Lets you sell legally on Day 45 while the licence is pending. UI-only gating is not a control | ⚠️ **Shipped, not yet operable** (`bf0c885` server, `51c457a` client). Authoritative gate is `agents/deep-quant-loop/entitlements.py`; `frontend/src/lib/sku.ts` is explicitly defence-in-depth and a UX affordance only. Both fail closed. **Blocked on the remote auth deployment: `GET /api/v1/internal/entitlement/{user_id}` does not exist, so `SKU_ENFORCE=1` currently denies *all* RESEARCH traffic.** Correct posture for Phase 0 — TERMINAL-only is what is being sold — but it must be built before RESEARCH can be sold to a real user |
| **P2** | **Immutable recommendation record store.** Per output: timestamp, symbol, direction, entry, SL, TP, horizon, rationale, every tool input value, model + prompt version hash, analyst of record | One table serves the research-report record rule, the audit trail, AI accountability and any future black-box research report | ⚠️ **Shipped, one field null** (`bf0c885`). `hashchain.py` + `reco_store.py`, append-only SQLite with `row_hash = sha256(prev_hash ‖ canonical_json(payload))`, `verify_chain()`, UPDATE/DELETE refused by database trigger. Hooked at the single `_finalize_decision` chokepoint. Model id and `prompt_hash` recorded per row via `prompt_version.py`. **`analyst_of_record` is null until P8b** (recorded honestly, not faked), and **the chain has no external witness** — a holder of the file could rebuild it end to end |
| **P5** | **Interaction log.** Every QA turn, notification and support message, tamper-evident, 5+ year retention | Record-keeping covers *all* client interactions | ✅ **Shipped** (`bf0c885`). `interaction_log.py` on the same hash-chain primitive. Logged at `/run`, `/qa`, `/resume`, `/cancel`, **before** the work, so refused requests are on the record too. Content stored verbatim; oversize content truncated visibly. **No `purge()` exists** and a test asserts it never will — the 5-year floor is enforced by the absence of an API. Notifications and support messages are not yet in scope: the log covers product interactions only |
| **P6** | **Strip external performance surfaces.** Journal win rate and expectancy become internal-only. Ship the discipline-statistics surface instead | Fastest route to enforcement if left exposed | ✅ **Shipped** (`51c457a`). `computePortfolioMetrics` no longer computes Total Return, Win Rate, Max Drawdown or Avg Conviction; the surface is now Setups Audited / Rejected / Forced HOLDs / Plan Adherence, headed "Discipline Metrics", with the P&L colouring removed. `journal.py` keeps win rate and expectancy as **internal** calibration. Plan Adherence renders `—` until its counter has data — honest empty, never a fabricated zero. User-facing wording still **[COUNSEL]** |
| **P8a** | **Personalisation refusal guardrail in QA mode** | Prevents drift from RA (research) into IA (advice) territory | ✅ **Shipped** (`bf0c885`). `personalisation.py` `detect_personalisation()` is deterministic and runs **before** the model is called, so the refusal costs no tokens and does not depend on the model choosing to comply. Covers capital, income, net worth, goals, holdings, position sizing and suitability. The matched category is stamped on the refusal and carried into the P5 log as evidence. Prompt rule 6 added as defence in depth |
| **P14** | **Broker + market-data adapter abstraction** | Removes the single-point-of-failure on one broker. Investors will ask | ⚠️ **Seam shipped, still one feed** (`e1caf32`). `providers::MarketDataProvider` and `providers::BrokerProvider`, selected by `MARKET_DATA_PROVIDER` (default `kite`). `BrokerProvider` exposes only `positions` and `margins` — **it has no order method**, which is what makes the "cannot place an order" claim in `docs/compliance/AI_DISCLOSURE.md` §2 structural rather than a policy. **Kite is the only implementation**, so the single point of failure is reduced to weeks of work, not removed |

**What the seven do not cover.** P3, P4, P8b, P9, P10 and P13 remain open and are Phase-1 items
(Part 14). P11 and P12 have shipped as *documents* — `docs/compliance/AI_DISCLOSURE.md` and
`docs/compliance/AI_MODEL_GOVERNANCE.md` (`876bbf0`) — but the disclosure page is **not publishable**:
§8 of it lists five blockers, and §4 carries a hard stop because the desktop build's default LLM
gateway is an internal proxy rather than the router the page names.

### 4.3 Compliance groundwork (Day 15–45) · Owner: Founder + Counsel

| # | Step |
| --- | --- |
| 0.9 | **Engage SEBI-practice securities counsel.** Non-negotiable. Scope: entity structure, RA application, product classification opinion, MITC and client agreement, marketing review |
| 0.10 | **Identify the principal officer.** Graduate degree in a specified field (finance, economics, business management, commerce, capital markets or equivalent) plus NISM certification |
| 0.11 | **Register for NISM Series XV (Research Analyst)** for the principal officer and every person who will touch research output |
| 0.12 | **Appoint a Compliance Officer** — mandatory for a non-individual RA |
| 0.13 | **Open the lien-marked deposit.** ₹1 lakh at the ≤150-client tier. Hold it in a **liquid or overnight mutual fund** — SEBI permitted this from August 2025, so it need not be dead capital |
| 0.14 | **Draft the policy set:** compliance manual, code of conduct, personal-trading and blackout policy, conflict-of-interest policy, advertisement policy, grievance policy, record-retention policy, **AI model governance policy** — ✅ the AI model governance policy is drafted at `docs/compliance/AI_MODEL_GOVERNANCE.md` (`876bbf0`), with eight open items listed in its §10. The other eight policies are not started |
| 0.15 | **Commission the CSCRF gap assessment.** Confirm your applicability category first — thresholds are size-based and a small RA sits in a lighter tier **[VERIFY]** |
| 0.16 | **Freeze and audit all existing marketing.** Remove every return claim, performance figure, "SEBI" reference and P&L screenshot. Publish internal brand guidelines with the banned list from `GO_TO_MARKET.md` §2 — ✅ guidelines published at `docs/compliance/BRAND_GUIDELINES.md` and the in-product copy audited and scrubbed (`876bbf0`). **The audit covered the repo, not the live website** — that is the next pass, and `docs/compliance/WEBSITE_COPY.md` is the artefact it produces |
| 0.17 | **Screen every affiliate, influencer and referral partner** against Regulation 16A. Terminate anyone giving stock calls or making return claims |

### 4.4 Revenue (Day 30–45) · Owner: Founder + Growth

- **Launch TERMINAL Pro.** ₹1,499/month or ₹14,999/year. No directional output anywhere in the SKU,
  including the free tier.
- **Start the content engine.** Two deep technical pieces a month (see `GO_TO_MARKET.md` §3.1).
- **Launch the moderated no-calls community.** Enforce the rule publicly — it is Regulation 16A
  hygiene *and* brand evidence.
- **Open broker conversations for the platform track.** Lead with Regulation 16A and the audit trail;
  the buyer is their compliance head.

**Gate 0 → 1:** TERMINAL live and taking payment · no recommendation surface reachable by an
unlicensed user (verified by a written test, not an eyeball) · all secrets rotated · counsel engaged ·
NISM booked.

**Gate status as of 19 August 2026 — one of five criteria met.**

| Criterion | State |
| --- | --- |
| TERMINAL live and taking payment | ⬜ Not met. The SKU split exists in code; the website still has to sell it. `docs/compliance/WEBSITE_COPY.md` is the copy source, `GO_TO_MARKET.md` §5 the price |
| No recommendation surface reachable by an unlicensed user, **verified by a written test** | ✅ Met as a mechanism. The tests exist and are the evidence this criterion asked for: `frontend/src/lib/__tests__/sku*` (client), `agents/deep-quant-loop/tests/test_entitlements*.py` (server), and `test_interaction_log.py::test_a_refused_request_is_logged_with_both_rows` — which proves the refusal is *written down*, so "no unentitled user asked" can be distinguished from "we never recorded it". Both gates fail closed on a null, malformed or absent flag |
| All secrets rotated | ⬜ **Not met.** Two credentials are still owed rotation and the rotation log in `SECRET_ROTATION_RUNBOOK.md` §6 has empty cells. This is the criterion most likely to be mistaken for done because P7 "shipped" |
| Counsel engaged | ⬜ Not met. Every **[COUNSEL]** marker in `docs/` is downstream of this one |
| NISM booked | ⬜ Not met |

---

## Part 5 — Phase 1: India licence (Day 30–210)

### 5.1 File (Day 45–75)

| # | Step |
| --- | --- |
| 1.1 | Assemble the application pack: Form A, incorporation documents, MoA/AoA, principal officer and Compliance Officer qualification and NISM evidence, net-worth/deposit evidence, infrastructure details, CIBIL and declarations as currently required. Note SEBI's August 2025 consultation proposed relaxing some of these — **[VERIFY the current checklist against the RA Master Circular dated February 2026 before filing]** |
| 1.2 | Pay the Schedule III application fee — **₹5,000 for body corporates and LLPs**. Registration fee per Schedule III **[VERIFY current amount on the SEBI e-services payment module]** |
| 1.3 | Submit via the **SEBI Intermediary Portal** |
| 1.4 | **Enlist with the RAASB — BSE Limited.** Mandatory under the revised framework |
| 1.5 | Respond to queries. Budget 3–6 months. Never put a licence date on a revenue-critical path |

### 5.2 Build while waiting (Day 45–180) · Owner: CTO

| # | Task |
| --- | --- |
| P3 | **Research report renderer.** Date and time, price at publication, target, holding period, rationale, **risk factors** (Bear Agent output), RA name and INH number, Compliance Officer contact, conflict disclosures, standard SEBI disclaimer |
| P4 | **KYC-gated onboarding.** PAN, KRA upload, client agreement e-sign, MITC acknowledgement, and **family-level fee-cap tracking that hard-blocks** any sale breaching ₹1,51,000 per annum per family. The cap is per *family*, not per login — model household linkage or you will breach it |
| P8b | **Analyst-of-record workflow.** A NISM-certified human accountable for published research, designed as a logged supervisory review rather than a per-call bottleneck **[COUNSEL on the acceptable degree of automation]** |
| P9 | **Personal-trading surveillance.** Block or flag employee and entity trades contrary to a live recommendation or inside a blackout window |
| P10 | **Advertisement register.** Every creative, approver, live dates, retained copy |
| P11 | **Public AI disclosure page.** Plain language: what the model does, its inputs, its limitations, that output is probabilistic, its measured accuracy. Ship this voluntarily and early |
| P12 | **Model register.** Every model and prompt version, owner, test suite, drift monitoring, human-override log |
| P13 | **Grievance module.** In-app intake, SLA timers, SCORES and ODR escalation paths **[VERIFY current ODR onboarding steps for RAs]** |

### 5.3 On grant (Day ~180–210)

Wire the INH number, Compliance Officer contact, disclaimer and MITC link into every surface —
prominently, not in the footer. Launch RESEARCH at ₹4,999/month or ₹49,999/year. Complete **Google
financial services verification** and **SEBI SI Portal advertiser verification**, then open paid
search on non-promissory keywords only.

**Gate 1 → 2:** INH granted · RAASB enlisted · RESEARCH live with KYC gating and fee-cap enforcement ·
CSCRF gap assessment closed · first compliance audit scheduled.

---

## Part 6 — Phase 2: Crypto (Day 90–270)

### 6.1 The counterintuitive finding

**Crypto in India is a *lighter* regime than equities, not a heavier one.**

- A virtual digital asset is **not a security** under Indian law. SEBI has no jurisdiction. **No RA
  registration applies to crypto research**, which also means **no ₹1,51,000 fee cap, no RA
  advertisement code, no RAASB, no lien-marked deposit.**
- The binding Indian regime is **AML, via the PMLA**. A **VDA Service Provider** must register with
  **FIU-IND** as a reporting entity — but only if engaged in the **notified activities**: exchange
  between VDAs and fiat, exchange between VDAs, **transfer** of VDAs, **safekeeping or
  administration** of VDAs or instruments giving control over them, and participation in financial
  services related to an issuer's offer and sale of a VDA
  ([Ministry of Finance response](https://sansad.in/getFile/annex/270/AU1986_Ku3XXP.pdf?source=pqars),
  [ET](http://economictimes.indiatimes.com/news/economy/finance/fiu-ind-issues-notices-to-25-offshore-crypto-exchanges/articleshow/124265777.cms)).
- **Pure analytics, research and signals are not on that list.** On the current framework, a crypto
  version of Strat AI that never custodies, never exchanges, never transfers and never routes orders
  is likely **outside** the VDA SP definition.

**This is the single most important legal opinion to obtain before writing crypto code.
[COUNSEL — get it in writing.]** Two conditions must hold, and both are product decisions:

1. **No custody, ever.** No wallet, no key management, no pooled balances.
2. **No order routing, ever** — or if execution is added, it happens entirely on the user's own
   exchange account through the user's own credentials, and you obtain a fresh opinion first.

### 6.2 If the analysis flips, or if you later add execution

FIU-IND's 2026 AML/CFT guidelines (updated 8 January 2026) consolidate the obligations. You would
need: **FINgate portal registration before commencing operations**, a **Designated Director** and a
separate **Principal Officer**, an AML/CFT/CPF programme with internal policies and controls, training
and internal audit, and a **cyber security audit certificate from a CERT-In empanelled auditor**
([CryptoSlate summary](https://cryptoslate.com/crypto-laws/india-fiu-ind-aml-cft-guidelines-vda-service-providers/),
[Moneycontrol](https://www.moneycontrol.com/technology/fiu-ind-releases-guidelines-for-crypto-virtual-digital-asset-entities-article-13765629.html)).
Note the regime reaches **offshore entities serving Indian users** — FIU-IND issued notices to 25
offshore exchanges in October 2025 and directed app and URL takedowns. Do not assume a foreign
holding company escapes it.

### 6.3 Steps

| # | Step | When |
| --- | --- | --- |
| 2.1 | **Counsel opinion**: is analytics-only crypto research a notified VDA activity? Get it in writing before code | Day 90 |
| 2.2 | **Incorporate CryptoCo** as a separate TechCo subsidiary. Keep it structurally isolated from ResearchCo | Day 120 |
| 2.3 | **Port the engine to crypto markets.** The 15-step pipeline maps across with substitutions: 24×7 sessions replace NSE session phases; funding rates, open interest and perpetuals basis replace the options chain; on-chain flow supplements order flow; BTC dominance replaces NIFTY as the relative-strength benchmark; the event gate reads unlocks, halvings and listings instead of earnings | Day 120–210 |
| 2.4 | **Ship a VDA tax-aware P&L view** — 30% flat tax on transfer income and 1% TDS under section 194S of the 1961 Act, carried into the tabular provisions of the Income-tax Act, 2025. Users badly need this and no competitor does it well. A compliant, high-value differentiator | Day 180–240 |
| 2.5 | **Launch India crypto** at a price *above* the equity RESEARCH SKU. No fee cap applies. **[COUNSEL to confirm no cap or code applies]** | Day 240 |
| 2.6 | **Keep a written monitoring brief** on India's crypto policy track — a discussion paper and international reporting-framework alignment have both been signalled. Assign an owner and review quarterly | Ongoing |

**Strategic caution:** crypto's light Indian regulation is *unstable*, not permanent. Treat it as a
high-margin accelerant, never as the foundation. The equity RA licence remains the durable asset.

**Gate 2 → 3:** written counsel opinion secured · crypto SKU selling · no custody and no order routing
anywhere in the codebase (verified by test, not by policy).

---

## Part 7 — Phase 3: Global via GIFT City (Day 180–450)

### 7.1 Why GIFT City is the answer, not Singapore or Dubai

The **IFSC at GIFT City** is a Special Economic Zone unit treated as **foreign territory** for many
purposes, interacting principally with non-residents, and with much of Indian exchange-control law
disapplied ([BW Legal World](https://www.bwlegalworld.com/article/international-financial-service-centre-ifsc-propelling-india%E2%80%99s-aspiration-in-amrit-kalm-465271)).
IFSCA's **(Capital Market Intermediaries) Regulations, 2025** create registration categories for
**Investment Adviser** and **Research Entity**, with **Master Circulars** for each issued in August
2025, and a **Unified Registration ("Master Key")** route for multiple capital market activities
introduced in February 2026
([IFSCA Research Entity Master Circular](https://ifscacms.devitsandbox.com/Common/PreviewPdf?fileName=Master_Circular_for_Research_Entities_in_the_IFSC_20250805_0227_20250806_0224.pdf&id=80e660c7990aa90605134583584791e6),
[Unified Registration circular](https://ifsca.gov.in/CommonDirect/DownloadFile?fileName=Unified_Registration_for_multiple_Capital_Market_Activities_under_the_IFSCA__Capital_Market_Intermediaries__Regulations__2025__Master_Key__20260213_0615.pdf&id=36ff47aaeb9222f627d166fe86c6ec20)).

So you can serve **NRIs and global non-resident clients** from an Indian office, under a single Indian
regulator, with your engineering team in the same time zone — instead of standing up a Singapore or
Dubai entity with local directors, local capital and local auditors. For a seed-stage company this is
the difference between one global rail and four.

### 7.2 Steps

| # | Step | Note |
| --- | --- | --- |
| 3.1 | **Obtain the Letter of Approval (LoA)** under the SEZ Act, 2005 for a GIFT IFSC unit. IFSCA confirmed in August 2026 that a valid LoA is a **prerequisite** to any IFSC registration; an LoA is valid one year if business has not commenced and five years once it has, with renewal at least two months before expiry ([Business Standard](https://www.business-standard.com/economy/news/ifsca-asks-ifsc-entities-to-maintain-valid-approvals-or-face-action-126081001120_1.html)) |
| 3.2 | **Incorporate the IFSC unit** and lease qualifying space in GIFT City |
| 3.3 | **Meet the CMI Regulations conditions:** minimum net worth for the Research Entity category **[VERIFY the current USD figure in the Master Circular]**, plus a **Principal Officer and a Compliance Officer** with the specified qualifications — a professional qualification or post-graduate degree in finance, law, accountancy, business management, commerce, economics, capital markets, banking, insurance, actuarial science, fintech, or a STEM field, or CFA or FRM; a graduate degree suffices with five years of financial-services experience |
| 3.4 | **Apply for Research Entity registration**, and evaluate the **Master Key** unified route if you also want Investment Adviser permissions for a future advisory product |
| 3.5 | **Onboard clients through an IFSC KYC Registration Agency** — the IFSCA (KYC Registration Agency) Regulations, 2025 require IFSCA-regulated entities to upload client KYC to a KRA |
| 3.6 | **Price in USD.** Segregate systems and records between the IFSC unit and ResearchCo — different regulators, different client sets, no commingling |
| 3.7 | **Confirm resident-Indian eligibility.** IFSC units are oriented to non-residents; resident participation is limited and route-dependent. **[COUNSEL — establish exactly which client categories you may serve before you market]** |

**Gate 3 → 4:** IFSCA registration granted · first non-resident client onboarded through an IFSC KRA ·
systems and records segregated.

---

## Part 8 — Phase 4: US and UK via publication exclusions (Day 360–630)

These two markets are large and can be entered at **low licensing cost** — but only if the product is
strictly impersonal. The exclusions are narrow and the product must be engineered to fit them.

### 8.1 United States — the publisher's exclusion

In **Lowe v. SEC, 472 U.S. 181 (1985)** the Supreme Court held that publications of
**non-personalised** investment advice and commentary fell within the Advisers Act's statutory
exclusion for bona fide publications, so the publishers were not "investment advisers"
([Justia](http://supreme.justia.com/us/472/181/), [Cornell](https://www.law.cornell.edu/supremecourt/text/472/181)).
Later SEC guidance and no-action letters build on this framework for automated tools.

**What the product must satisfy — treat these as engineering requirements, not legal notes:**

1. **Bona fide publication** — a genuine, regularly published product, not a front for touting
   specific positions the firm holds.
2. **General and regular circulation** — the same content available to all subscribers in a tier, not
   generated per client.
3. **Strictly impersonal** — no consideration of any user's finances, capital, goals, holdings or risk
   profile. **The QA-mode personalisation guardrail (#6) is the load-bearing control here.**
4. **Not promotional** — no interest in the securities discussed, disclosed conflicts.

**[COUNSEL — a written US securities-law opinion is mandatory before accepting a single US
subscriber.]** Also confirm state-level requirements and whether any state asserts a narrower
exclusion. Note the exclusion covers the **Advisers Act**; anti-fraud liability under the Exchange Act
applies regardless, so accuracy and disclosure discipline still bind.

### 8.2 United Kingdom — the Article 54 exclusion

The FCA's Perimeter Guidance explains that the main exclusion from "advising on investments" under
Article 53(1) is **Article 54 (advice given in newspapers etc.)**, covering **periodical
publications, regularly updated news and information services and broadcasts**, provided the
**principal purpose** is not to give such advice or to lead or enable people to buy or sell
([FCA PERG 8.31](https://handbook.fca.org.uk/handbook/perg8/perg8s32)). Separately, **COBS 12**
governs investment research where a firm is authorised.

The "principal purpose" test is the hard part for a product whose whole purpose is trade evaluation.
**[COUNSEL — obtain a UK opinion, and consider seeking an FCA certificate that the publication
qualifies for the Article 54 exclusion.]** If the exclusion is unavailable, the fallback is a UK
appointed-representative arrangement or full authorisation, both materially more expensive — in which
case defer the UK behind the UAE.

### 8.3 Steps

| # | Step |
| --- | --- |
| 4.1 | Ship a **hard impersonality mode**: a build-level flag that disables any per-user tailoring, enforced in the API layer and covered by tests |
| 4.2 | Establish a **regular publication cadence** with an archive — evidence of a bona fide, generally circulated publication |
| 4.3 | Obtain the **US opinion** and the **UK opinion**. Do not launch on either without them |
| 4.4 | Geo-fence by entitlement, not by IP alone. Capture jurisdiction at signup and bind SKU features to it |
| 4.5 | Jurisdiction-specific disclaimers, terms and privacy notices. Confirm the export-of-data position for each |
| 4.6 | Register trademarks in the US and UK via the Madrid Protocol |

**Gate 4 → 5:** written opinions in hand for each market entered · impersonality mode enforced and
tested · first paying users in at least one of the two.

---

## Part 9 — Phase 5: Licensed offshore (Day 540–900)

Sequence by cost-to-serve, not by prestige.

| Priority | Jurisdiction | What is required | Why this order |
| --- | --- | --- | --- |
| **1** | **UAE** | For crypto advisory in or from Dubai, a **VARA** licence covering the relevant virtual-asset activity; VARA covers the whole Emirate **except DIFC**, which is DFSA territory. ADGM/FSRA is the Abu Dhabi alternative. For securities research, the DIFC/DFSA or ADGM/FSRA route. Costs vary widely by activity and carry fixed-overhead requirements **[VERIFY]** ([VARA licensed activities](https://www.vara.ae/en/licenses-and-register/licensed-activities/)) | Large Indian-origin wealth base, familiar business culture, credible dual-track for crypto and securities, and a realistic first offshore licence for a seed-stage firm |
| **2** | **Singapore** | A **Financial Advisers Licence** is required to advise on investment products **and to issue or promulgate research analyses or research reports** concerning investment products ([Waystone](https://compliance.waystone.com/services/apac-solutions/licensing/obtaining-licences-in-singapore-how-can-waystone-help/)). MAS also operates a **FinTech Regulatory Sandbox** granting time-limited, case-by-case relief with boundary conditions | No publication-style exclusion to lean on, so the research product needs a licence. High quality but high cost. Approval rates in adjacent regimes have been low. Enter when APAC revenue justifies it, and evaluate the sandbox as a lower-cost first step |
| **3** | **European Union** | Under **MiCA**, **providing advice on crypto-assets** is a regulated crypto-asset service requiring CASP authorisation ([ESMA, MiCA Article 81](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mica/article-81-providing-advice-crypto-assets)). Securities research falls under MiFID II | Most expensive per unit of revenue. Defer until the crypto product is proven and funded. One CASP authorisation does passport across the EU, which is the upside when you do go |

**Steps for whichever you pick first:** market and revenue case → local counsel → entity and substance
requirements (local director, office, capital, auditor) → application → local AML/CFT programme →
data-transfer and privacy alignment → local marketing rules review.

---

## Part 10 — Operating cadence

Once licensed, compliance is a calendar, not a project. Assign a named owner to each line.

| Frequency | Obligation |
| --- | --- |
| **Continuous** | Interaction logging · immutable recommendation records · advertisement register · personal-trading surveillance · family-level fee-cap enforcement · grievance SLA timers |
| **Weekly** | Marketing review against the banned list · new-partner Regulation 16A screening · model drift dashboard |
| **Monthly** | Grievance report · deposit-tier check against client count · model register update · LLM cost per FIND run |
| **Quarterly** | Internal compliance review · AI governance review · crypto and AI policy monitoring brief · client-count vs deposit tier reconciliation |
| **Annually** | Compliance audit and RAASB report · **CSCRF cyber audit — 100% of critical systems and a 25% sample of non-critical, with the sampling rationale stated** · VAPT · policy refresh · NISM continuity check · IFSC LoA and registration validity check |
| **Every 3 years** | Fee-cap revision on the Cost Inflation Index |
| **Every 5 years** | **SEBI registration renewal.** SEBI has cancelled registrations for non-payment of renewal fees. Calendar it with two independent reminders |
| **By ~May 2027** | **DPDP full compliance** — itemised consent notices, purpose-based retention, security safeguards, 72-hour breach notification, data-principal rights. Penalties reach ₹250 crore. Document the carve-out where SEBI's 5-year retention overrides an erasure request **[COUNSEL]** |

---

## Part 11 — Marketing build-out, phase by phase

Detail in `GO_TO_MARKET.md`. Alignment to this plan:

| Phase | Message | Channels | Hard limits |
| --- | --- | --- | --- |
| **0** | *"It tells you when not to trade."* Engineering credibility | Technical long-form, founder video, moderated no-calls community, one open-sourced peripheral component | No paid financial-services ads. No performance, return or "SEBI" claims anywhere |
| **1** | Same, plus *"Watch it think"* and the three unbreakable risk rules | Add paid search on non-promissory intent terms once Google financial services verification and SEBI advertiser verification are complete | Advertisement register live from the first paid rupee. INH number, Compliance Officer contact, disclaimer and MITC displayed prominently |
| **2** | *"The same discipline, applied to a 24×7 market"* — plus tax clarity | Crypto communities, tax-education content | No RA advertisement code applies, but **hold yourself to it anyway**. One reckless crypto ad becomes evidence against the regulated arm |
| **3** | *"Institutional-grade Indian market research, from GIFT City, in USD"* | NRI corridors: Gulf, Singapore, US, UK. Diaspora communities, NRI wealth advisers | IFSCA marketing rules, plus the marketing rules of every country you solicit into |
| **4** | *"A published research service"* — deliberately publication-framed | Newsletter-native distribution, podcasts, syndication | Impersonality is a legal requirement, not a style choice. Any per-user tailoring in a US or UK asset can destroy the exclusion |
| **5** | Localised per market | Local partners | Local rules govern. Never reuse Indian creative unreviewed |

**Retention across every phase:** the capital-preservation dashboard. Setups rejected, risk avoided,
plan adherence — the user's own statistics, never the model's win rate. **[COUNSEL to review the exact
wording before it ships.]**

---

## Part 12 — Fundraise alignment

| Round | When | Size | What the money buys | Milestone that unlocks the next |
| --- | --- | --- | --- | --- |
| **Seed** | Day 0–60 | ₹10 cr | Phases 0–2. Compliance-blocking engineering, RA registration, TERMINAL and RESEARCH launch, crypto build, two broker deals | INH granted · ₹4–5 cr ARR · crypto live · 12 months of labelled recommendation-outcome data |
| **Pre-A / bridge** | Day 300–420 | ₹20–30 cr | Phase 3. GIFT City unit, USD product, NRI go-to-market, second data feed | IFSCA registration · non-resident revenue · ₹20 cr+ ARR |
| **Series A** | Day 540–720 | ₹80–150 cr | Phases 4–5. US and UK entry, first offshore licence, scale | Multi-jurisdiction revenue · ₹60 cr+ ARR |

**The Series A story is not the AI.** It is: a licensed multi-jurisdiction research infrastructure
company holding a proprietary, compounding dataset of AI-generated recommendations tagged by setup
with realised outcomes — an asset no competitor can buy. Every phase above exists to make that
sentence true and defensible.

---

## Part 13 — Risk register with triggers

| Risk | Trigger to watch | Pre-agreed response |
| --- | --- | --- |
| SEBI RA application rejected or stalled beyond 9 months | No substantive query response by Day 180 | Escalate via counsel; seek informal guidance under the SEBI (Informal Guidance) Scheme, 2003; run harder on TERMINAL and platform tracks; consider the IFSC route first instead of second |
| **Crypto counsel opinion comes back adverse** (product *is* a notified VDA activity) | Opinion at Day 90 | Either register with FIU-IND (Designated Director, Principal Officer, AML programme, CERT-In audit) or restrict the crypto product to pure analytics with no signal output until the position is clear |
| India tightens crypto regulation | Discussion paper published, or international reporting-framework alignment announced | Quarterly monitoring brief already assigns an owner. Crypto is an accelerant, never the foundation — the equity licence carries the company |
| SEBI AI/ML guidelines land stricter than the draft (e.g. mandatory reproducibility for client-facing models) | Final circular issued | LLM already out of the order path. Model and prompt hashes on every record make outputs replayable. Governance policy already written |
| US or UK exclusion opinion unavailable | Counsel declines to opine favourably | Do not launch. Redirect to UAE, or serve those markets only through a locally licensed partner |
| Single-broker dependency fails | Any Kite outage exceeding one trading session | Adapter abstraction (P14) shipped in Phase 0 (`e1caf32`) specifically so a second feed can be added in weeks. **The mitigation is not yet in place** — the seam exists, but Kite remains the only implementation, so today an outage is still an outage |
| Regulation 16A breach via a marketing partner | Any partner names a stock or claims a return | Immediate termination clause in every partner contract. Weekly screening already in the cadence |
| CSCRF audit failure or data breach | Audit finding, or any incident | Gap assessment in Phase 0. 72-hour DPDP breach-notification runbook written before it is needed |
| Fee-cap breach at the family level | Any household approaching ₹1,51,000 across subscriptions | P4 hard-blocks the sale. Monthly reconciliation catches drift |
| SEBI registration lapses on renewal | 5-year anniversary approaching | Two independent calendar reminders, 6 months and 3 months prior |

---

## Part 14 — Master checklist

**Phase 0 · Day 0–45** — ☑ done · ◐ shipped with a named gap (see §4.2) · ☐ not started
☐ TechCo incorporated ☐ ResearchCo incorporated ☐ IP assigned to TechCo ☐ IP licence executed
☐ Shared-services agreement ☐ ESOP pool ☐ GST, DPIIT, bank accounts ☐ Trademark filed (IN)
◐ **P7 secrets purged and rotated** — untracked, not rotated; no managed store
◐ **P1 SKU split** — both gates ship and fail closed; remote entitlement endpoint absent
◐ **P2 recommendation store** — chained and append-only; no external witness, analyst null
☑ **P5 interaction log** ☑ **P6 performance surfaces stripped** ☑ **P8a personalisation guardrail**
◐ **P14 broker/data adapters** — seam shipped, Kite still the only implementation
☐ Counsel engaged ☐ Principal officer identified ☐ NISM booked
☐ Compliance Officer appointed ☐ Deposit opened in liquid fund ☐ Policy set drafted (1 of 9)
☑ AI governance policy drafted ☐ CSCRF gap assessment commissioned
◐ Marketing frozen and audited — product copy scrubbed; website copy drafted, not yet published
☐ Partners screened for Reg 16A ☐ **TERMINAL Pro live and selling** ☐ Content engine running
☐ Community live with no-calls rule ☐ Broker conversations opened

**Phase 1 · Day 30–210**
☐ Application pack assembled ☐ Schedule III fee paid ☐ Filed on SEBI Intermediary Portal
☐ RAASB (BSE) enlistment ☐ **P3 report renderer** ☐ **P4 KYC + family fee-cap gating**
☐ **P8b analyst-of-record workflow** ☐ **P9 personal-trading surveillance**
☐ **P10 advertisement register** ◐ **P11 AI disclosure page** — drafted at
`docs/compliance/AI_DISCLOSURE.md`, **not publishable** (its §8 lists five blockers, §4 a hard stop)
◐ **P12 model register** — `prompt_version.py` records model id + prompt hash per output;
inventory and version register at `docs/compliance/AI_MODEL_GOVERNANCE.md` §2–§3
☐ **P13 grievance module** ☐ INH number wired into product ☐ RESEARCH SKU live
☐ Google financial services verification ☐ SEBI SI Portal advertiser verification
☐ Paid search live on approved terms

**Phase 2 · Day 90–270**
☐ **Written crypto counsel opinion** ☐ CryptoCo incorporated ☐ Engine ported to crypto markets
☐ No custody and no order routing (test-enforced) ☐ VDA tax-aware P&L view ☐ Crypto SKU live
☐ Policy monitoring brief with named owner

**Phase 3 · Day 180–450**
☐ SEZ Letter of Approval ☐ IFSC unit incorporated ☐ GIFT City space leased
☐ Net worth met **[VERIFY figure]** ☐ Principal Officer + Compliance Officer appointed
☐ IFSCA Research Entity application ☐ Master Key unified registration evaluated
☐ IFSC KRA onboarding live ☐ USD pricing ☐ Records segregated
☐ Client-category eligibility confirmed **[COUNSEL]**

**Phase 4 · Day 360–630**
☐ Impersonality mode shipped and tested ☐ Publication cadence and archive established
☐ **US opinion** ☐ **UK opinion** ☐ Jurisdiction-bound entitlements ☐ Per-market legal documents
☐ Trademarks (US, UK)

**Phase 5 · Day 540–900**
☐ Offshore market and revenue case ☐ Local counsel ☐ Substance requirements met
☐ Application filed ☐ Local AML/CFT programme ☐ Data-transfer alignment ☐ Local marketing review

---

## Part 15 — Sources

**India — securities**
- [SEBI (Research Analysts) Regulations, 2014](https://www.sebi.gov.in/sebi_data/commondocs/RESEARCHANALYSTS-regulations_p.pdf) · Third Amendment notified 16 December 2024
- SEBI guidelines for Research Analysts, circular dated 8 January 2025
- [SEBI FAQs for Research Analysts, July 2025](https://www.sebi.gov.in/sebi_data/faqfiles/jul-2025/1753269723942.pdf)
- SEBI circular, *Safer participation of retail investors in Algorithmic trading*, 4 February 2025 · deferred September 2025 · in force 1 April 2026
- [SEBI Consultation Paper, *Guiding Principles for Responsible usage of AI/ML in securities markets*, 20 June 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jun-2025/1750415065695.pdf)
- SEBI CSCRF, 20 August 2024, with clarifications 30 April 2025
- SEBI (Intermediaries) (Amendment) Regulations, 2024 · Regulation 16A, effective 29 August 2024
- [Mint, summary of the January 2025 IA/RA guidelines](https://www.livemint.com/market/sebi-new-ias-ras-guidelines-independent-advisers-research-analysts-investment-advice-kyc-11736349404566.html)
- [Taxmann, SEBI guidance on the RA fee cap](https://www.taxmann.com/post/blog/sebi-guidance-on-ra-fee-cap-for-individual-and-huf-clients)

**India — crypto**
- [Ministry of Finance, Parliament response on VDA SP registration obligations](https://sansad.in/getFile/annex/270/AU1986_Ku3XXP.pdf?source=pqars)
- [FIU-IND downloads, including AML/CFT guidelines for VDA reporting entities updated 8 January 2026](https://fiuindia.gov.in/files/Downloads/Downloads.html)
- [CryptoSlate, India FIU-IND AML/CFT guidelines for VDA service providers](https://cryptoslate.com/crypto-laws/india-fiu-ind-aml-cft-guidelines-vda-service-providers/)
- [Moneycontrol, CERT-In audit requirement for VDA entities](https://www.moneycontrol.com/technology/fiu-ind-releases-guidelines-for-crypto-virtual-digital-asset-entities-article-13765629.html)
- [Economic Times, FIU-IND notices to 25 offshore exchanges](http://economictimes.indiatimes.com/news/economy/finance/fiu-ind-issues-notices-to-25-offshore-crypto-exchanges/articleshow/124265777.cms)
- [Income Tax India, section 194S](https://www.incometaxindia.gov.in/w/section-194s-4) · [CryptoSlate on the Income-tax Act, 2025 treatment](https://cryptoslate.com/crypto-laws/india-income-tax-act-vda-tax-tds-regime/)

**GIFT City / IFSCA**
- [Master Circular for Research Entities in the IFSC, August 2025](https://ifscacms.devitsandbox.com/Common/PreviewPdf?fileName=Master_Circular_for_Research_Entities_in_the_IFSC_20250805_0227_20250806_0224.pdf&id=80e660c7990aa90605134583584791e6)
- [Master Circular for Investment Advisers in the IFSC, August 2025](https://ifsca.gov.in/CommonDirect/DownloadFile?fileName=Master_Circular_for_Investment_Advisers_in_the_IFSC_20250805_0226.pdf&id=21626bde60601ef44a0ed02201682306)
- [Unified Registration ("Master Key") circular, February 2026](https://ifsca.gov.in/CommonDirect/DownloadFile?fileName=Unified_Registration_for_multiple_Capital_Market_Activities_under_the_IFSCA__Capital_Market_Intermediaries__Regulations__2025__Master_Key__20260213_0615.pdf&id=36ff47aaeb9222f627d166fe86c6ec20)
- [IFSCA public comments on proposed CMI amendments — qualification wording](https://ifsca.gov.in/CommonDirect/DownloadFile?fileName=Public_comments_on_proposed_amendments_to_the_IFSCA__Capital_Market_Intermediaries__Regulations__2025_20251223_0729.pdf&id=38fea9cc5969551d78bf00e670b8dff4)
- [Business Standard, IFSCA on Letter of Approval validity, August 2026](https://www.business-standard.com/economy/news/ifsca-asks-ifsc-entities-to-maintain-valid-approvals-or-face-action-126081001120_1.html)
- [ICSI Info Capsule, IFSCA (KYC Registration Agency) Regulations, 2025](https://www.icsi.edu/media/webmodules/infocapsule/Info_Capsule_21042025.pdf)

**United States · United Kingdom · Singapore · EU · UAE**
- [Lowe v. SEC, 472 U.S. 181 (1985) — Justia](http://supreme.justia.com/us/472/181/) · [Cornell](https://www.law.cornell.edu/supremecourt/text/472/181)
- [FCA PERG 8.31 — exclusions for advising on investments (Article 54)](https://handbook.fca.org.uk/handbook/perg8/perg8s32) · [FCA COBS 12 — investment research](https://handbook.fca.org.uk/handbook/cobs12)
- [Waystone, MAS licensing — Financial Advisers Licence scope](https://compliance.waystone.com/services/apac-solutions/licensing/obtaining-licences-in-singapore-how-can-waystone-help/)
- [ESMA, MiCA Article 81 — providing advice on crypto-assets](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mica/article-81-providing-advice-crypto-assets)
- [VARA, licensed activities](https://www.vara.ae/en/licenses-and-register/licensed-activities/)

**Data protection**
- Digital Personal Data Protection Act, 2023 and DPDP Rules notified 13 November 2025
- [EY, DPDP Rules 2025 compliance guide](https://www.ey.com/en_in/insights/cybersecurity/transforming-data-privacy-digital-personal-data-protection-rules-2025)

*Content from external sources was rephrased for compliance with licensing restrictions.*
