# Strat AI — SEBI Compliance & Corporate Structure Blueprint

**Version 1.0 · August 2026**

> **Not legal advice.** This is a research-grounded structuring plan built from SEBI's published
> regulations, circulars, consultation papers and FAQs (sources cited inline). Every item marked
> **[COUNSEL]** must be confirmed with a SEBI-practice securities lawyer and a Company Secretary
> before you file anything. Regulatory amounts and dates change; verify against the current RA
> Master Circular and the RAASB (BSE) portal at the time of filing.
> *Content from external sources was rephrased for compliance with licensing restrictions.*

---

## 0. The one-paragraph verdict

Strat AI's FIND mode produces a directional call with an entry, a stop loss, a take profit and a
conviction score. Under Indian law that is a **research recommendation**, and providing it for
consideration requires registration as a **Research Analyst (RA)** under the
[SEBI (Research Analysts) Regulations, 2014](https://www.sebi.gov.in/sebi_data/commondocs/RESEARCHANALYSTS-regulations_p.pdf).
The AI wrapper changes nothing — SEBI looks at what a tool *does*, not what it is called. On top of
that, the reasoning core is LLM-driven and non-reproducible, which places it in the **black-box**
category the moment it touches order flow, and the black-box lane *also* requires RA registration
plus a research report per algorithm. So there is no route to a compliant, monetised Strat AI in
India that skips RA registration. The good news: registration is cheap, the fee ceiling is far above
your likely price point, and it unlocks the only distribution channel that matters (brokers), because
Regulation 16A now forbids brokers from associating with unregistered advice providers.

---

## 1. Which regulatory box does Strat AI fall into?

SEBI's treatment splits by **what the software does to order flow and to the user's decision**, not
by the technology. Three distinct regimes apply to different parts of your product.

| Strat AI capability | What it actually is | Regime |
| --- | --- | --- |
| Ghost Lines, order-flow footprint, volume profile, indicators, regime classification, S/R levels, VWEPR | Analytics that surface facts; user decides | **Unregulated software.** No registration needed |
| VERIFY mode maths on user-supplied Entry/SL/TP (ATR check, R:R check, level ordering) | Validation of the *user's own* numbers | Unregulated **if** it returns no directional view **[COUNSEL]** |
| FIND mode output (direction + entry + SL + TP + conviction) | Research recommendation | **RA registration mandatory** |
| DEBATE / Judge consensus verdict, Bear Agent "don't take this trade" | Recommendation (including a negative one) | **RA registration mandatory** |
| Conviction Score 1–100 marketed as a quality signal | Recommendation + potential implied performance claim | **RA + advertisement code** |
| Journal win-rate / expectancy shown to users | Performance claim about recommendations | **RA + strict performance-disclosure rules** |
| Any future auto-execution or order routing | Black-box algo | **Retail algo framework + broker as principal + exchange empanelment** |

### 1.1 The RA trigger

A Research Analyst is any person or entity providing research reports or investment recommendations
for a fee ([SEBI investor portal](https://investor.sebi.gov.in/research_analyst.html)). Two adjacent
points that matter to you:

- **Advice vs research.** One-to-one, client-specific advice needs an **Investment Adviser (IA)**
  registration under the 2013 regulations. Public or non-personalised buy/sell calls need **RA**.
  Strat AI runs on a stock the user selects but does not consider their income, goals or risk
  profile — that is research, not advice, so **RA is the correct primary registration**. Do not let
  the product drift into portfolio-level or client-circumstance-aware suggestions without adding an
  IA registration; SEBI now permits the same entity to hold both, with segregation and an arm's
  length relationship between the two functions
  ([Mint, Jan 2025](https://www.livemint.com/market/sebi-new-ias-ras-guidelines-independent-advisers-research-analysts-investment-advice-kyc-11736349404566.html)).
- **"Education" is not a shelter.** SEBI closed that door in late 2024. If specific stocks and
  specific levels are named, calling it a course or a simulator does not help.

### 1.2 The black-box trigger

SEBI's circular *Safer participation of retail investors in Algorithmic trading*
(SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, dated **4 February 2025**) was deferred in September
2025 and applies in full from **1 April 2026**. Its structure:

- The **broker is the principal**; the algo provider is its **agent**.
- Every algo needs **exchange permission**, obtained by the broker, and every algo order carries an
  **exchange-issued unique ID**.
- Providers must be **empanelled with the exchanges**; brokers must run their own due diligence and
  may deal only with empanelled firms.
- **Open APIs are barred.** Access runs on a unique vendor–client API key from a **whitelisted static
  IP**, with OAuth and two-factor authentication.
- The **broker alone** handles client complaints. Exchanges retain a **kill switch** per algo ID.
- Self-coded algos need registration only above the exchange-set order-rate bar (currently
  **10 orders/second**); below it, orders are still tagged as algo orders.

**White-box vs black-box is the fork that decides your paperwork.** White-box means the logic is
disclosed to the user and reproducible — same inputs, same orders, every time. Black-box means it is
not. An LLM reasoning loop with a temperature above zero, non-deterministic tool ordering and a
self-calibrating journal is **black-box**. Black-box providers must register as RAs, file a research
report per algorithm, and **re-register from scratch when the logic materially changes**
([openalgo](https://openalgo.in/quant/retail-api-algo-framework),
[sahi.com](https://www.sahi.com/blogs/is-ai-trading-legal-in-india)).

> **Kill the myth internally now:** there is no such thing as a "SEBI-approved algo." SEBI does not
> vet algo providers. Exchanges empanel; brokers screen. Never use "SEBI approved" in any marketing
> asset.

### 1.3 Strategic consequence

Because re-registration is triggered by material logic change, **do not put the LLM reasoning core
into the order path**. Keep Strat AI's AI layer strictly on the *research/recommendation* side under
the RA licence, where you can iterate the model freely, and expose only a deterministic,
fully-documented white-box execution shim if you ever add automation. This is the single highest-value
architectural decision in this document: it preserves your ability to ship model improvements weekly
instead of filing paperwork for each one.

---

## 2. Recommended corporate structure

Use **two entities**. This is not tax engineering; it is a fundraising and iteration-speed decision.

```
                 ┌──────────────────────────────────────────┐
                 │   Strat AI Technologies Pvt Ltd          │
                 │   ("TechCo")  — UNREGULATED              │
                 │   • Owns all IP: Rust tick pipeline,     │
                 │     VWEPR, Ghost Lines, agents, UI       │
                 │   • Raises equity from investors         │
                 │   • Sells SaaS analytics (no calls)      │
                 │   • Licenses tech B2B to brokers/RAs     │
                 └───────────────┬──────────────────────────┘
                                 │  arm's-length IP licence
                                 │  + shared-services agreement
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │   Strat AI Research Pvt Ltd              │
                 │   ("ResearchCo") — SEBI RA (INH…)        │
                 │   • Holds the RA registration            │
                 │   • Employs NISM-certified analysts      │
                 │   • Publishes FIND / DEBATE output as    │
                 │     research reports                     │
                 │   • Owns client KYC, agreements, MITC    │
                 │   • Compliance Officer, deposit, audits  │
                 └──────────────────────────────────────────┘
```

**Why two entities**

1. **Change-of-control friction.** A SEBI-registered intermediary needs prior SEBI approval for a
   change in control. If your cap-table entity *is* the RA, every priced round, every ESOP pool
   expansion that shifts control, and any acquisition drags a SEBI approval onto the critical path.
   Keeping the registration in a subsidiary whose control does not change on a TechCo round removes
   that. **[COUNSEL]** — confirm that your specific shareholding chain does not itself constitute an
   indirect change in control of ResearchCo.
2. **Inspection surface.** SEBI inspection, cyber audit and record-production duties attach to the
   registered entity. Containing them limits how much of your engineering org lives inside a
   regulated perimeter.
3. **Valuation multiple.** A software licensor prices differently from a research subscription
   business. Investors will underwrite TechCo on ARR and IP; ResearchCo is the compliant delivery
   rail.
4. **Optionality.** If you later add IA, or a broking tie-up, or an offshore entity for non-Indian
   markets, TechCo stays the constant.

**Entity form:** Private Limited Company under the Companies Act, 2013 for both. A body corporate
(including LLP) can register as an RA; the amended Schedule III sets a **₹5,000 application fee for
body corporates and LLPs**. Registration and renewal fees are per the current Schedule III —
**confirm exact amounts on the SEBI e-services payment module before filing**. Renewal is on a
five-year cycle and SEBI has cancelled registrations for non-payment, so calendar it.

**Do not** register the founder personally as an individual RA as a shortcut. Individual RA
registration caps your ability to scale staff, complicates ESOPs, and puts personal liability where a
corporate veil should be.

---

## 3. Registration roadmap

### Phase 0 — Pre-filing (weeks 0–6)

| # | Action | Owner |
| --- | --- | --- |
| 0.1 | Incorporate TechCo and ResearchCo; ResearchCo MoA object clause must cover research services **[COUNSEL]** | CS |
| 0.2 | Identify the **principal officer / designated RA** — needs a graduate degree in finance, economics, business management, commerce, capital markets or an equivalent specified field, plus **NISM certification** | Founder |
| 0.3 | Book **NISM-Series-XV (Research Analyst)** for the principal officer and every person who will be "associated with research services." SEBI's July 2025 FAQ circular gives associated persons **one year from the circular date** to certify — new hires should be certified before they touch research output | HR |
| 0.4 | Appoint a **Compliance Officer** (non-individual RAs must have one; it cannot be a rubber stamp) | Founder |
| 0.5 | Execute the **TechCo ⇄ ResearchCo IP licence** at an arm's-length royalty; get a transfer-pricing opinion if either entity has non-resident shareholding **[COUNSEL]** | CA |
| 0.6 | Open the **lien-marked deposit**. Tiered on the maximum number of clients on any day in the previous FY: **≤150 → ₹1 lakh; 151–300 → ₹2 lakh; 301–1,000 → ₹5 lakh; 1,001+ → ₹10 lakh**, lien-marked to the RAASB. Since August 2025 SEBI permits **liquid and overnight mutual funds** in place of a scheduled-bank deposit — use this, it is not dead capital | CFO |
| 0.7 | Draft the compliance manual, code of conduct, personal-trading policy, conflict-of-interest policy, advertisement policy, grievance policy, record-retention policy | Compliance |

### Phase 1 — File (weeks 6–14)

| # | Action |
| --- | --- |
| 1.1 | Apply to SEBI via the **SEBI Intermediary Portal**, Form A + Schedule III fee |
| 1.2 | **Enlist with the RAASB — BSE Limited.** This is mandatory under the revised framework, not optional |
| 1.3 | Respond to SEBI/RAASB queries. Budget 3–6 months end-to-end; do not put a launch date in an investor deck that assumes faster |
| 1.4 | On grant of **INH registration number**, wire it into the product (see §5) |

### Phase 2 — Operating compliance (ongoing)

| Obligation | Detail |
| --- | --- |
| **Client agreement + MITC** | Every client needs a signed agreement and the mandated **Most Important Terms and Conditions** document, which states the fee ceiling and that SEBI registration is not a performance guarantee |
| **KYC** | Full KYC on every fee-paying client; upload to a KRA. This means Strat AI's signup flow cannot be a bare email + card form |
| **Record keeping** | Records of **all client interactions** — written and signed documents, call recordings, emails, SMS, or any other legally verifiable record — retained **minimum 5 years, or until dispute resolution, whichever is longer** |
| **Fee ceiling** | **₹1,51,000 per annum per family** for individual and HUF clients, across *all* research services of the RA. SEBI has confirmed this applies without exception. Revised every three years on the Cost Inflation Index. Non-individual clients are outside this cap |
| **Advance fee** | Since April 2025, advance collection may cover **up to one year** of fees (relaxed from one quarter). Annual plans are therefore viable |
| **Compliance audit** | Annual compliance audit; report to RAASB |
| **Grievances** | Register on **SCORES** and onboard the **ODR (Online Dispute Resolution)** portal **[COUNSEL — confirm current ODR onboarding steps for RAs]** |
| **Personal trading** | Analysts and the entity are restricted from trading contrary to published recommendations and within blackout windows around publication. Codify this in the code of conduct and, better, enforce it in software |
| **Disclosures per report** | Conflicts, holdings, compensation, RA identity + INH number, Compliance Officer contact, and the standard SEBI disclaimer |
| **Reg 16A** | ResearchCo must not associate — directly or indirectly — with any person giving unregistered securities advice or making return claims. This applies to your **affiliate marketers, influencer campaigns and referral partners**. Audit every partner |

---

## 4. Cybersecurity, AI governance and data protection

Three separate regimes stack on top of the RA licence. Two are already binding.

### 4.1 CSCRF — binding today

SEBI's **Cybersecurity and Cyber Resilience Framework** (20 August 2024) explicitly covers
Investment Advisers and Research Analysts, superseding earlier scattered circulars, with
clarifications issued 30 April 2025 and further technical clarifications thereafter
([Cyril Amarchand client alert](https://www.cyrilshroff.com/wp-content/uploads/2024/09/Client-Alert-Cybersecurity-and-Cyber-Resilience-Framework.pdf)).
Applicability is **threshold-based** by entity size, so a small RA sits in a lighter category —
**[COUNSEL] confirm your CSCRF category before scoping spend.** What you will need regardless:

- Classification of **critical systems** and an asset inventory. For Strat AI the critical set is the
  Rust tick ingester, QuestDB, the Kafka/Redpanda bus, the agent orchestrator, and anything holding
  broker API tokens or client KYC.
- **Cyber audit** covering **100% of critical systems and a 25% sample of non-critical systems**, with
  the sampling rationale stated in the report.
- SOC coverage or a documented equivalent, VAPT, incident response and reporting timelines, and a
  cyber-resilience/recovery plan.
- Cloud services adopted under SEBI's **Framework for Adoption of Cloud Services by REs**.

**Immediate engineering consequence — partly discharged, and the original claim was wrong.** This
paragraph previously said the repo had `.env`, `keys/` and `bedrock-api-key.txt` at the workspace root.
Verified against `git ls-files` on 17 August 2026:

- `.env` and `keys/` were **never tracked**. The claim was inherited from an architecture note, not
  measured. Both hold live material — the deploy key and the Tauri updater private key — so the risk
  was real, but it was never a *git* exposure.
- `bedrock-api-key.txt` **was** tracked (in history from `4aeceb2`), and so was
  `scripts/powershell/auth/keys/{private,public}.pem` — **which this paragraph missed entirely**.
  Neither is referenced by any code. Both are now untracked and `.gitignore` widened (`8293dda`).

What remains, and why it is not closed:

1. **Git history was deliberately not rewritten.** Every existing clone already holds the old values,
   so **rotation, not rewriting, is what closes the exposure**. The purge command is recorded in
   `docs/compliance/SECRET_ROTATION_RUNBOOK.md` §4 for when re-clones can be coordinated.
2. **Two credentials are still owed rotation** — see the runbook's §6 log, which is the artefact an
   auditor asks for and currently has empty cells.
3. **No managed secret store is in use** (runbook §3.3, §8 item 1). Stronghold covers user-supplied
   keys in the desktop app only, not service credentials.

A leaked broker credential inside a SEBI-regulated entity is a reportable cyber incident, not just a
bad day — and the reporting trigger itself is still **[COUNSEL]** (runbook §5).

### 4.2 AI/ML governance — one binding rule, one draft

- **Binding:** SEBI's 2019 circulars require reporting of AI/ML systems offered or used. For brokers
  and DPs that is SEBI/HO/MIRSD/DOS2/CIR/P/2019/10 dated 4 January 2019. It is a **filing duty, not a
  licence**, and it applies whether or not the tool touches orders. Separately, the **8 January 2025
  RA guidelines require RAs and IAs to disclose the extent of AI usage in their offerings and hold
  them responsible for data security and applicable compliance** — the RA cannot outsource
  accountability to a model vendor.
- **Draft:** the consultation paper *Guiding Principles for Responsible usage of AI/ML in securities
  markets* (**20 June 2025**, comments closed 11 July 2025) proposes principles of **Equality,
  Accountability, Transparency (explainability and auditability), and Safety & Reliability**, plus
  plain-language disclosure to investors of a model's **purpose, risks, accuracy and limitations**,
  with a lighter tier for purely internal use
  ([SEBI paper](https://www.sebi.gov.in/sebi_data/attachdocs/jun-2025/1750415065695.pdf),
  [Taxmann summary](https://www.taxmann.com/post/blog/sebi-proposes-ai-ml-governance-framework-for-securities-markets)).
  As of August 2026 it remains a draft; the SEBI Chairman has publicly stated detailed AI guidelines
  are coming.

**This is your single biggest unearned advantage and you should exploit it.** Strat AI's existing
architecture already satisfies most of the draft framework: glass-box streaming of every tool call is
*transparency and auditability*; the "honest failure over fabrication" rule is *safety and
reliability*; the bounded execution budget with forced HOLD is *reliability*; the SQLite journal
tracking realised expectancy per setup is *model monitoring*. Do three things now:

1. **Write the AI Model Governance Policy before you are asked for it** — model inventory, owner per
   model, version register, pre-deployment test suite, drift monitoring, human-override log, incident
   procedure, and a documented fallback when an LLM returns garbage.
   ✅ **Done** — `docs/compliance/AI_MODEL_GOVERNANCE.md` (`876bbf0`). Eight items in its §10 remain
   open; the two that bite are that **no human-override log exists** (there is nothing to override
   today, but that stops being true the moment P8b lands) and that **§2's inventory cannot be
   fully verified from source** because model names are baked at compile time via `option_env!`.
2. **Version and hash every model + prompt that produced a published recommendation**, and store the
   hash on the recommendation record. When SEBI asks "why did you recommend this," you replay it.
   ✅ **Done** — `prompt_version.py` emits `model_id`, `prompt_hash` and `prompt_set_hash`; P2's
   `reco_store` writes them onto every row at the `_finalize_decision` chokepoint.
3. **Publish a plain-language AI disclosure page** stating what the model does, its inputs, its known
   limitations, that its output is probabilistic, and its measured accuracy. Do this voluntarily. When
   the guidelines land, you are already compliant and every competitor is scrambling.
   ◐ **Drafted, not published, and one clause of this instruction is now deliberately not followed.**
   `docs/compliance/AI_DISCLOSURE.md` covers what the model does, its inputs, its limitations and that
   the output is probabilistic. It does **not** state a measured accuracy figure. That is a resolved
   choice, not an omission: publishing a hit rate is exactly the headline-performance claim the
   advertisement code restricts and `docs/compliance/BRAND_GUIDELINES.md` prohibits, so the two
   requirements pull against each other and the safer error is to under-claim. **[COUNSEL]** must
   settle it — tracked as `AI_MODEL_GOVERNANCE.md` §10 item 5 and `AI_DISCLOSURE.md` §8 item 4.
   Publication is separately blocked: the desktop build's default LLM gateway is an internal proxy,
   not the router the page names, so §4 of the page carries a hard stop.

### 4.3 DPDP Act 2023 + DPDP Rules 2025

The **DPDP Rules were notified 13 November 2025** with a phased runway; the core obligations —
itemised consent notices, purpose-based retention, security safeguards, **72-hour breach
notification**, data-principal rights, and Significant Data Fiduciary duties — become fully
enforceable around **mid-May 2027**, with penalties up to **₹250 crore**
([EY](https://www.ey.com/en_in/insights/cybersecurity/transforming-data-privacy-digital-personal-data-protection-rules-2025)).

Note the tension you must resolve explicitly: SEBI requires **5-year retention** of client
interactions; DPDP requires **purpose-limited retention and erasure on request**. The resolution is
that a statutory retention obligation overrides an erasure request for that specific data class — but
you must document that carve-out in your privacy notice rather than discover it during a complaint.
**[COUNSEL]**

---

## 5. Product changes required before you can charge a rupee

This is the engineering backlog. Ordered by blocking severity.

### 5.1 Blocking — cannot launch paid without these

> **Status column is a pointer, not the record.** `docs/business/PLAN_OF_ACTION.md` §4.2 is canonical
> and carries the commit and the residual gap for each item. Duplicating detail here is how the two
> drift apart.

| # | Change | Why | Status |
| --- | --- | --- | --- |
| P1 | **Split the product into two SKUs in code**: `TERMINAL` (analytics only — Ghost Lines, footprint, volume profile, indicators, regime, VWEPR, S/R, patterns) and `RESEARCH` (FIND, DEBATE, conviction score, journal). Gate `RESEARCH` behind a verified RA-client entitlement | Lets TechCo sell `TERMINAL` legally from day one while the INH is pending | ◐ Both gates ship and fail closed. The **entitlement source does not exist yet** in the remote auth deployment, so RESEARCH is currently deniable-only |
| P2 | **Recommendation record store**: every FIND/DEBATE output persisted immutably with timestamp, symbol, direction, entry, SL, TP, horizon, rationale, every tool input value, model + prompt version hash, and the analyst of record | Research-report retention, audit trail, AI accountability, and the black-box research report all draw on this one table | ◐ Shipped, hash-chained, DB-enforced append-only. **Analyst of record is null until P8b**; no external witness |
| P3 | **Research report renderer**: turn each FIND output into a compliant report containing date/time, price at publication, target, holding period, rationale, **risk factors**, RA name + INH number, Compliance Officer contact, conflict disclosures and the standard SEBI disclaimer | A recommendation delivered without these is a defective research report | ☐ Not started — Phase 1. Blocked on the INH number regardless |
| P4 | **KYC-gated onboarding**: PAN, KRA check, client agreement e-sign, MITC acknowledgement, family-level fee-cap tracking that hard-blocks a sale that would breach ₹1,51,000 p.a. per family | The fee cap is per *family*, not per login — you must model family linkage or you will breach it | ☐ Not started — Phase 1 |
| P5 | **Interaction logging**: every chat turn in QA mode, every notification, every support conversation retained 5+ years in a legally verifiable, tamper-evident form | Record-keeping rule covers *all* client interactions, and QA mode is a client interaction about a recommendation | ✅ Shipped for product interactions (`/run`, `/qa`, `/resume`, `/cancel`), logged before the work so refusals are recorded. **Notifications and support conversations are not yet covered** |
| P6 | **Strip or gate all performance surfaces**: the journal's win rate and expectancy cannot be shown as marketing. Internally it is a calibration input; externally it is a performance claim governed by the advertisement code | Fastest way to draw an enforcement action | ✅ Shipped. Discipline metrics replaced the four performance figures; `journal.py` keeps them internal. Labels still **[COUNSEL]** |
| P7 | **Secrets hygiene**: untrack every credential file, move to a managed secret store, rotate all credentials, and decide history-rewrite separately | CSCRF, and basic hygiene before you hold client data | ◐ Untracking done. **Rotation incomplete, no managed store, history deliberately intact** — see §4.1 above |

### 5.2 High priority — needed within one quarter of launch

| # | Change | Status |
| --- | --- | --- |
| P8 | **Analyst-of-record workflow.** A NISM-certified human must be accountable for published research. Design this as a *supervisory* layer — the analyst reviews and signs off on the AI's output, with the review logged — not as a bottleneck on every call **[COUNSEL on the acceptable degree of automation]** | ☐ Not started. P8a (the personalisation guardrail) shipped and is a different control; **P8b is what fills P2's null `analyst_of_record` column** |
| P9 | **Personal-trading surveillance**: block or flag trades by employees and the entity that run contrary to a live published recommendation or fall inside a blackout window | ☐ Not started |
| P10 | **Advertisement register**: every ad, landing page, social post and creative stored with approval trail. SEBI has been running AI-based surveillance of financial social media and has flagged roughly 20,000 fraudulent posts since November 2025 — assume your marketing is being read by a machine | ☐ Not started. **`docs/compliance/WEBSITE_COPY.md` is the first entry it will need to hold** — every landing-page string with its substantiation and approver |
| P11 | **Public AI disclosure page** (§4.2) | ◐ Drafted at `docs/compliance/AI_DISCLOSURE.md`; **not publishable** — five blockers in its §8, plus the gateway-naming stop in its §4 |
| P12 | **Model governance policy + model register** (§4.2) | ✅ Policy and inventory at `docs/compliance/AI_MODEL_GOVERNANCE.md`; per-output model id and prompt hash recorded by `prompt_version.py` |
| P13 | **Grievance module**: in-app complaint intake with SLA timers, wired to SCORES and ODR escalation | ☐ Not started |

### 5.3 Design choices that reduce regulatory load

- **Keep Ghost Lines white-box.** OLS linear regression over 14 closes with a published R² is fully
  reproducible and explainable. It already displays its own confidence and hides itself outside the
  10-minute timeframe it was calibrated on. Document it as a deterministic indicator; it never needs
  to enter the black-box lane.
  - ⚠️ **Verified 19 August 2026, and there are two implementations — do not describe them as one.**
    The `agents/predictive` service is exactly as described above: a rolling **14**-candle OLS on
    **10-minute** candles, `r_squared` converted to the displayed confidence, `MODEL_VERSION =
    "alpha-linreg-v1"`. The Tauri desktop path (`compute_ghost_curve` → `calculate_dual_projection`)
    is a **different model** — a dual-engine OLS + VWEPR projection with a 20-candle minimum and **no
    R²**. So "published R²" is true of the service and **not** of the desktop build. Any public
    sentence about Ghost Lines must either name the one the reader is looking at or describe only
    what both share (deterministic, reproducible, shows its own confidence, no forecast claim). This
    is the §5.1-lesson-2 failure mode in `docs/compliance/BRAND_GUIDELINES.md`: a wrong statement
    about how the model works is a compliance defect, not a wording nit.
- **Make the conflict-forced HOLD a named, documented control.** "When technical and sentiment
  signals conflict, the system outputs HOLD" is a *risk control*, and a regulator reads a documented,
  testable, always-on risk control very differently from a marketing claim. Give it a name, a test,
  and a line in the compliance manual.
- **Keep VERIFY mode's maths strictly on user-supplied numbers.** The ATR-multiple check, R:R floor
  and level-ordering check operate on inputs the user provides. Keep the Bear Agent's output framed
  as *risk factors present in your plan* rather than *do not take this trade*, and this feature stays
  much closer to a calculator than to a recommendation. **[COUNSEL]**
- **Never auto-execute from the LLM.** §1.3.

---

## 6. Three revenue tracks, ranked by time-to-cash

| Track | What you sell | Registration needed | Time to revenue | Notes |
| --- | --- | --- | --- | --- |
| **A. TERMINAL SaaS** | Charts, Ghost Lines, footprint, volume profile, indicators, regime, VWEPR, S/R — **no directional calls** | None | **Now** | Ship this while the INH is pending. Proves willingness to pay and builds the user base that the RESEARCH SKU upsells into |
| **B. B2B2C licensing** | White-label the terminal + research engine to brokers and existing SEBI-registered RAs; **they** carry the client-facing obligation | None for TechCo (licensee carries it) | 2–6 months | Highest margin, fastest scale, lowest compliance load. This is the Sensibull playbook — [Sensibull is itself a SEBI-registered Research Analyst](https://sensibull.com/index.html) and distributes through brokers. **Reg 16A is your salesman here:** brokers legally cannot integrate an unregistered advice provider, so an INH-holding partner has a structural advantage over every grey-market algo shop |
| **C. RESEARCH subscription** | FIND, DEBATE, conviction score as research reports, direct to retail | **RA (INH)** | 4–9 months | Highest ARPU per user, highest compliance load. Fee ceiling ₹1,51,000 p.a. per family is not a real constraint at a ₹2,000–₹8,000/month price point |

Run A and B while C is in registration. That sequencing means the SEBI application timeline does not
sit on your revenue critical path — which is exactly what an investor will probe.

---

## 7. What NOT to do

Each of these is a live enforcement risk, not a theoretical one.

- **No assured, guaranteed, or implied returns.** Not in copy, not in a testimonial, not in a
  screenshot, not in a comparison table, not as "our users averaged X%."
- **No "SEBI approved" or "SEBI certified."** Registration is not approval and algos are never
  SEBI-approved. Registration also does not guarantee performance, and you must say so.
- **No influencer or affiliate who gives stock calls or makes return claims.** Reg 16A, effective
  29 August 2024, prohibits direct *or indirect* association. Terminate and screen every partner.
- **No free trial that hands out live calls.** "For a fee" is broadly construed; consideration can be
  indirect. Route free tiers to the TERMINAL SKU only. **[COUNSEL]**
- **No backtested or hypothetical performance in marketing** without the disclosures the
  advertisement code requires — and if in doubt, leave it out.
- **No open API for order placement.** The February 2025 framework bars it.
- **No client-circumstance-aware suggestions** (income, goals, existing portfolio) without an IA
  registration.
- **No sharing of a single API key across users**, and no dynamic-IP order routing, if you ever touch
  execution.

---

## 8. Indicative cost of compliance (year 1)

Directional only — get quotes. Amounts in INR.

| Item | Estimate |
| --- | --- |
| Incorporation × 2 + secretarial setup | 40,000 – 80,000 |
| SEBI RA application fee (body corporate, per amended Schedule III) | 5,000 |
| SEBI registration fee (per Schedule III — **verify current amount**) | verify |
| RAASB (BSE) enlistment | verify with BSE |
| Lien-marked deposit (≤150 clients tier, held in liquid MF — recoverable, not an expense) | 1,00,000 |
| NISM Series XV certification × 3 people | 15,000 – 25,000 |
| Securities-law counsel: structuring, agreements, MITC, policies | 3,00,000 – 8,00,000 |
| Compliance Officer (part-time / retained, year 1) | 4,00,000 – 12,00,000 |
| CSCRF gap assessment + first cyber audit + VAPT | 3,00,000 – 10,00,000 |
| DPDP readiness (notices, consent plumbing, retention policy) | 1,00,000 – 3,00,000 |
| Annual compliance audit | 75,000 – 2,00,000 |
| **Working total (ex-deposit)** | **≈ ₹13 lakh – ₹36 lakh** |

Wide range because CSCRF scope and counsel depth dominate. Budget ₹25 lakh for a credible year-one
compliance line in an investor model, and note that it is a one-time-heavy, recurring-light curve.

---

## 9. 90-day execution sequence

> **Engineering ran ahead of the sequence.** The Days 1–60 *code* — P7, P1, P2, P5, plus P6, P8a and
> P14 that this section did not schedule — landed on `develop` on 19 August 2026. The Days 1–60
> **non-engineering** items are all still open, and they are now the critical path: no amount of
> shipped mechanism substitutes for incorporation, counsel, NISM or a rotation log with entries in it.
> Status per blocker: `docs/business/PLAN_OF_ACTION.md` §4.2.

**Days 1–30**
Incorporate both entities. Appoint the principal officer and Compliance Officer; register them for
NISM Series XV. Engage securities counsel. Purge and rotate all secrets (P7). Ship P1 — the SKU split
— and start selling the TERMINAL SaaS. Freeze all marketing copy containing performance, return or
"SEBI" claims until reviewed.
→ P1 shipped. P7 **half done** — files untracked, credentials not rotated. Marketing frozen and the
in-product copy scrubbed; the **website** is the outstanding surface, and
`docs/compliance/WEBSITE_COPY.md` is the reviewed copy to replace it with. Nothing else here started.

**Days 31–60**
Build P2 (recommendation record store) and P5 (interaction logging) — both are pure engineering and
both are prerequisites for everything downstream. Open the lien-marked deposit in a liquid fund.
Draft the compliance manual, code of conduct, personal-trading policy, advertisement policy and AI
model governance policy. Commission the CSCRF gap assessment. Begin B2B2C conversations with brokers
— lead with Reg 16A and your glass-box audit trail, which is what their compliance team actually
cares about.
→ P2 and P5 shipped. Of the five policies, **only the AI model governance policy is drafted**. Deposit,
CSCRF assessment and broker conversations not started.

**Days 61–90**
File the SEBI RA application and the RAASB enlistment. Build P3 (report renderer) and P4 (KYC +
family fee-cap gating). Publish the AI disclosure page. Stand up the advertisement register. Get the
first B2B2C pilot signed.
→ Unchanged, with one correction: **the AI disclosure page cannot be published on this schedule as
drafted.** Two of its blockers are outside engineering's control — the INH number (item 1) and three
**[COUNSEL]** questions — and one is inside it: the default LLM gateway must match the provider the
page names. Treat "publish the disclosure page" as gated on the INH, not on the calendar.

---

## 10. Sources

All primary sources are SEBI publications; secondary sources are cited inline above.

- [SEBI (Research Analysts) Regulations, 2014](https://www.sebi.gov.in/sebi_data/commondocs/RESEARCHANALYSTS-regulations_p.pdf) — Third Amendment notified 16 December 2024
- [SEBI FAQs on regulatory provisions for Research Analysts, July 2025](https://www.sebi.gov.in/sebi_data/faqfiles/jul-2025/1753269723942.pdf)
- SEBI circular, *Safer participation of retail investors in Algorithmic trading*, 4 February 2025 — SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013; in force from 1 April 2026
- [SEBI Consultation Paper, *Guiding Principles for Responsible usage of AI/ML in securities markets*, 20 June 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jun-2025/1750415065695.pdf)
- SEBI Cybersecurity and Cyber Resilience Framework (CSCRF), 20 August 2024, with clarifications 30 April 2025
- SEBI (Intermediaries) (Amendment) Regulations, 2024 — Regulation 16A, effective 29 August 2024
- SEBI circular on relaxation of advance-fee restrictions for IAs and RAs, April 2025
- Digital Personal Data Protection Act, 2023 and DPDP Rules, notified 13 November 2025
- [Mint, *Sebi's new guidelines for independent advisers, research analysts*, January 2025](https://www.livemint.com/market/sebi-new-ias-ras-guidelines-independent-advisers-research-analysts-investment-advice-kyc-11736349404566.html)
- [Taxmann, *SEBI guidance on RA fee cap*, September 2025](https://www.taxmann.com/post/blog/sebi-guidance-on-ra-fee-cap-for-individual-and-huf-clients)
- [openalgo, *The Retail API Algo Framework in India*](https://openalgo.in/quant/retail-api-algo-framework)
- [sahi.com, *Is AI Trading Legal in India? The SEBI Rules*, updated August 2026](https://www.sahi.com/blogs/is-ai-trading-legal-in-india)
- [Cyril Amarchand Mangaldas, CSCRF client alert](https://www.cyrilshroff.com/wp-content/uploads/2024/09/Client-Alert-Cybersecurity-and-Cyber-Resilience-Framework.pdf)
- [EY, *DPDP Rules 2025 notified by MeitY*](https://www.ey.com/en_in/insights/cybersecurity/transforming-data-privacy-digital-personal-data-protection-rules-2025)

*Content from external sources was rephrased for compliance with licensing restrictions.*
