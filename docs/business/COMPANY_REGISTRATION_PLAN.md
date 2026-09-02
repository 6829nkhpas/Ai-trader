# Strat AI — Company Registration & SEBI Compliance Execution Plan

**Version 1.0 · August 2026**
**Objective:** incorporate two entities, remediate three live exposures, close five product gaps, and file a SEBI Research Analyst application that is granted first time.
**Audit basis:** codebase at `ccf29b5` (origin/main) · `docs/compliance/*` · `stratai.live` · `tradingrw.com`

> **Not legal advice.** **[COUNSEL]** = requires a SEBI-practice securities lawyer before you act.
> **[CS]** = Company Secretary. **[VERIFY]** = confirm against the primary source at filing time.
> Marketing and user-acquisition work lives in `MARKETING_PLAN.md`.
> *Content from external sources was rephrased for compliance with licensing restrictions.*

---

## Part 1 — Where Strat AI actually stands

### 1.1 The verdict

**Architecturally complete. Operationally not registrable today.** The hard engineering is done and is
better than most pre-registration codebases. What is missing is the last inch: two switches, one
greenfield build (KYC), and user-facing copy. Three items are **live exposures right now**.

### 1.2 Blocker-by-blocker scorecard

| Blocker | Status | Evidence in your code |
| --- | --- | --- |
| **P2** Immutable recommendation record | ✅ **~85%, audit-grade** | `agents/deep-quant-loop/reco_store.py` — hash-chained, append-only triggers via `hashchain.enforce_append_only`, no update/delete/purge, `tool_inputs_json` + `model_id`/`prompt_hash`/`prompt_set_hash`, idempotent per `thread_id`. Wired `graph.py:3930`, called `graph.py:4056` |
| **P5** Client interaction log | ✅ **~85%** | `interaction_log.py` — same chain primitive. `request` row written **before** work, so refusals and crashes leave a trace. Content verbatim. `refused_entitlement` and `refusal_category` are first-class |
| **P8a** Personalisation guardrail | ✅ **~95%, launch-ready** | `personalisation.py` — pure, total, deterministic, pre-LLM. Eight ordered categories, NFKC normalisation, `_ADJ` slot so "my **entire** capital" cannot bypass `my capital`. `QA_PROMPT_RULE` as defence in depth (`graph.py:5086`), hard refusal `graph.py:5133` |
| **P6** Performance surfaces removed | ✅ **~90%** | `frontend/src/hooks/useMacroIndicators.ts:190-235` documents removing Total Return, Win Rate, Max Drawdown, Avg Conviction; replaced by `computeDisciplineMetrics()`. No HTTP endpoint exposes journal stats |
| **Read-only broker** | ✅ **Clean, test-enforced** | `aggregator/src/kite_api.rs` has no order paths. `frontend/src/components/fno/__tests__/scopeBoundary.test.ts:256-264` asserts a denylist (`place_order`, `execute_trade`, `cancel_order`, `modify_order`, `submit_order`, `close_position`, `square_off`) is absent |
| **P11/P12** AI governance docs | ✅ **Written** | `docs/compliance/AI_DISCLOSURE.md` (14KB), `AI_MODEL_GOVERNANCE.md` (30KB), `BRAND_GUIDELINES.md` (33KB), `SECRET_ROTATION_RUNBOOK.md` (15KB) — **but surfaced nowhere users can see** |
| **P1** Server-side SKU gate | ⚠️ **80% built, 0% active** | `entitlements.py` is a real fail-closed API-layer gate. `SKU_ENFORCE` defaults `"0"`; `.env:129` sets `0`; the endpoint it calls does not exist |
| **P7** Secret hygiene | ❌ **Incomplete** | `bedrock-api-key.txt` — 2 commits, still in history on a GitHub remote |
| **P4** KYC / onboarding | ❌ **Zero code** | No PAN, KRA, client agreement, MITC, or household fee tracking anywhere |
| **P3** Report renderer | ❌ **Absent** | No compliant research-report output path |
| **P8b** Analyst of record | ❌ **Absent** | `ANALYST_OF_RECORD` unset → `analyst_of_record` **always NULL** |
| **P9** Personal-trading surveillance | ❌ **Absent** | — |
| **P10** Advertisement register | ❌ **Absent** | — |
| **P13** Grievance module | ❌ **Absent** | No SCORES/ODR intake |

---

## Part 2 — RED items: fix before you file

Filing while these are open is a bad trade. You would be applying to SEBI while advertising a
registration you lack and selling unregistered recommendations. **Three weeks of delay is nothing
against a refused application.**

### 🔴 RED 1 — A false SEBI-registration claim is shipping in your product

```
frontend/src/lib/sku.ts:176
  'available to subscribers of our SEBI-registered research service.'
agents/deep-quant-loop/entitlements.py:255
  "SEBI-registered research service."
frontend/src/components/quant/deep-quant/agentErrorClassifier.ts:71
  match: /RESEARCH plan|SEBI-registered research service/i
+ two test files asserting the same string
```

Every user who hits the locked-research state is told your service is SEBI-registered. It is not.
Misrepresenting registration status is among the most serious representations in the Indian securities
market, and it is precisely the pattern SEBI's AI social-media surveillance targets — the regulator has
flagged roughly 20,000 fraudulent posts since November 2025 covering guaranteed-return claims,
fraudulent certifications and impersonation of regulated entities. It also gives a reviewer a reason to
refuse your own application.

**Fix (today, ~30 minutes of work):**

| File | Change |
| --- | --- |
| `frontend/src/lib/sku.ts:175-177` | `RESEARCH_LOCKED_MESSAGE` → `'Trade analysis and recommendations are part of the RESEARCH plan.'` |
| `entitlements.py:253-256` | Drop the second sentence. Keep `"This analysis requires a RESEARCH subscription."` |
| `agentErrorClassifier.ts:71` | Update the regex to the new string |
| Both test files | Update fixtures |
| **Do not touch** `personalisation.py:467` | It directs users to *"a SEBI-registered investment adviser"* — a third party. Factually correct and useful |

Then grep the whole estate for `SEBI` — code, both websites, decks, emails, app-store listings, social
bios, pitch materials. Every hit must be either a factual disclosure of **non**-registration or deleted.

**Add the positive disclosure** while unregistered, on both sites and in-app:

> Strat AI is not currently registered with SEBI as a Research Analyst. An application is in progress.
> Nothing on this platform is a personal recommendation or investment advice.

**[COUNSEL on exact wording — a badly worded disclosure is its own problem.]**

### 🔴 RED 2 — Both gates are off while billing is live

| Layer | State | Source |
| --- | --- | --- |
| Server gate | **OFF** | `entitlements.enforcement_enabled()` defaults `"0"`; `.env:129` `SKU_ENFORCE=0` |
| Its dependency | **Missing** | `GET /api/v1/internal/entitlement/{user_id}` not implemented |
| Frontend gate | **Defeatable in prod** | `NEXT_PUBLIC_RESEARCH_BETA_OPEN=true` disables it in a production build; passed via `docker-compose.prod.yml:454`, `frontend/Dockerfile:88` |
| Web proxy tier | **Blind to caller identity** | `CLAUDE.md:312-317` — proxy routes enforce only the deployment-wide switch, so "a subscriber-level bypass of the per-user gate remains possible on the web path" |
| Billing | **Live** | `Payment`, `PaymentType = 'subscription' \| 'topup'`, `gatewayPaymentId`, `creditMultiplier` — `frontend/src/lib/api/types.ts:58-86` |

**Composite: money can be taken for a RESEARCH plan while nothing authoritative gates the
recommendation surface.** FIND emits direction, entry, stop and target. Delivered for consideration by
an unregistered entity, that is the exact activity requiring an INH.

Your `sku.ts` comment names three conditions that make the beta flag defensible — invite-only, risk copy
visible, removed before public launch. **Condition 2 fails** (zero disclaimer strings in
`frontend/src`), and `stratai.live` runs an open waitlist and a public pricing page, which strains
"invite-only."

**Interim decision required this week — pick one:**
- **(a)** Stop charging for RESEARCH until the gate is real. TERMINAL keeps selling.
- **(b)** Keep RESEARCH strictly **closed, invite-only and non-paying**, with the risk copy and
  non-registration disclosure visible on every research surface.

Free + closed + disclosed is defensible. Paid + open is not.

### 🔴 RED 3 — A credential remains in git history on a GitHub remote

```
git log --all --oneline -- bedrock-api-key.txt   →  2 commits
origin  https://github.com/thestratai/Ai-trader.git
```

Removing a file from HEAD does not remove it from history. Anyone with read access can recover the AWS
Bedrock key. A broader add-history scan also surfaced `auth/keys/private.pem`, `auth/keys/public.pem`
and copies under `scripts/powershell/`; an exact-path query returned nothing for those, so paths may
differ or sit in a submodule. **I could not conclusively resolve that and will not assert either way** —
the remediation is identical.

**Confirmed clean:** `keys/tauri-updater.key` and `keys/stratai_deploy` → **0 commits**, never
committed. That matters enormously — the Tauri updater key signs desktop auto-updates, so a holder could
push a signed malicious update to every desktop user. Keep it out of git permanently.

**Sequence (order matters):**
1. **Determine whether the repo is public or private.** If public, treat as a disclosed credential exposure, not a hygiene task.
2. **Rotate the AWS Bedrock key now.** Rotation takes minutes; history rewriting takes hours.
3. Run `gitleaks` or `trufflehog` over full history to settle the `.pem` question. Do not rely on pathspec results.
4. Rewrite with `git filter-repo` (or BFG), force-push, all collaborators re-clone. **This is destructive across a shared remote — schedule it deliberately with the team.**
5. Execute `docs/compliance/SECRET_ROTATION_RUNBOOK.md` and record the incident with a date. Under CSCRF a credential exposure inside a regulated entity is reportable. A dated internal record showing you found and fixed it **before** registration is an asset; an inspector finding it after is not.

---

## Part 3 — AMBER items to close before filing

| # | Issue | Where | Fix |
| --- | --- | --- | --- |
| **A1** | **Audit-write failures swallowed to a WARN.** A recommendation can reach a client with no regulatory record — the exact scenario `reco_store` exists to prevent. Your own comment calls it "a defect to fix, not to ignore" | `graph.py:4056-4057` | **Fail the request** if the recommendation record cannot be written. A user seeing an error is recoverable; an unrecorded published recommendation is not. For `interaction_log` (`main.py:168-180`), keep the WARN but add a monitored alert |
| **A2** | **`backtest.py` output may reach the SSE stream.** It is LLM-callable and tool results stream as visible reasoning — displaying backtested performance is what P6 removed from the dashboard | `backtest.py`, SSE path | Add an explicit test asserting backtest output never reaches the user-visible stream |
| **A3** | **`kycVerified` is read and logged but not part of the grant decision** — a control that looks like a control | `entitlements.py` docstring | Wire it into `_extract_entitlement` so RESEARCH requires `kycVerified === true` |
| **A4** | **Retention is a docstring, not a property.** Both audit stores are local SQLite; the five-year floor depends on a backup runbook | `reco_store.py`, `interaction_log.py` | Off-machine, append-only backup with a **documented restore test**. An inspector asking for a 2026 record in 2031 will not accept "it was on a laptop" |
| **A5** | **Zerodha is a single point of failure** | `aggregator/` | Adapter abstraction for market data and broker. Also an investor-diligence question |
| **A6** | **"Insights / market research & strategies" published while unregistered** | Both websites | **[COUNSEL]** on free-research-alongside-paid-product. Interim rule: publish *method*, never a live directional call on a named security |
| **A7** | **No disclaimers in product or on either site.** `AI_DISCLOSURE.md` exists as a document only. The sole compliance copy users ever see is the personalisation refusal — which appears only when a question is **refused**, never on the happy path where a recommendation is delivered | `frontend/src` | See P3 spec in §6.3 |

---

## Part 4 — Corporate structure

### 4.1 Two entities

```
┌──────────────────────────────────────────────────────────────┐
│  STRAT AI TECHNOLOGIES PVT LTD  ("TechCo")     UNREGULATED   │
│  • Owns 100% IP: Rust tick pipeline, VWEPR, trajectory       │
│    projections, agent graph, Tauri desktop, QuestDB schema   │
│  • Raises all equity. Holds cap table + ESOP pool            │
│  • Sells TERMINAL SaaS (analytics, no directional calls)     │
│  • Licenses tech to brokers, RAs and subsidiaries            │
│  MoA objects: software development and licensing             │
│  ✗ NOT "investment advice" or "research services"            │
└───────────────────────────┬──────────────────────────────────┘
                            │ arm's-length IP licence
                            │ + shared-services agreement
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  STRAT AI RESEARCH PVT LTD  ("ResearchCo")     SEBI RA       │
│  • Holds the INH registration                                │
│  • Employs NISM-certified analysts                            │
│  • Publishes FIND / DEBATE output as research reports         │
│  • Owns client KYC, agreements, MITC, grievances              │
│  • Compliance Officer, lien-marked deposit, audits            │
│  MoA objects: MUST cover research services  [COUNSEL]         │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Why two, specifically for you

1. **Change of control.** A SEBI-registered intermediary needs prior SEBI approval for a change in control. Your `INVESTOR_BRIEF.md` plans a ₹10 cr seed, a pre-A, and a Series A. If the cap-table entity **is** the RA, every priced round puts a regulator on your closing checklist. **[COUNSEL — confirm your shareholding chain does not itself constitute indirect change of control of ResearchCo.]**
2. **Inspection surface.** Inspection, CSCRF cyber audit and record-production attach to the registered entity. Containing them keeps most of engineering outside the regulated perimeter.
3. **Crypto isolation.** Your `PLAN_OF_ACTION.md` Phase 2 adds crypto, which is a VDA — different regulator, or none. Do not contaminate the SEBI entity with VDA activity, and do not let VDA activity inherit SEBI obligations it does not owe.
4. **GIFT City later.** Phase 3 needs a separate IFSC vehicle with its own SEZ Letter of Approval. TechCo stays the constant parent.
5. **Valuation.** A software licensor prices differently from a research subscription. Investors underwrite TechCo on ARR and IP.

**Do not** register a founder personally as an individual RA as a shortcut. It caps staffing,
complicates ESOPs, and puts personal liability where a corporate veil belongs.

---

## Part 5 — Registration execution, week by week

### Week 1 — Stop the bleeding

| Owner | Task |
| --- | --- |
| CTO | RED 1: purge SEBI-registration claims from `sku.ts:176`, `entitlements.py:255`, `agentErrorClassifier.ts:71`, both test files. Deploy same day |
| CTO | RED 3 step 1–3: determine repo visibility · rotate the AWS Bedrock key · run `gitleaks` over full history |
| Founder | RED 2: decide (a) stop charging for RESEARCH or (b) close the beta to invite-only, non-paying |
| Founder | **Engage SEBI-practice securities counsel.** Hand them this file, `SEBI_COMPLIANCE_BLUEPRINT.md` and `MARKETING_PLAN.md` |
| Founder | Identify the **principal officer**: graduate degree in finance, economics, business management, commerce, capital markets or an equivalent specified field |
| HR | **Book NISM Series XV (Research Analyst) × 3** — principal officer + two who will touch research output. **This gates P8b and therefore the whole application. Book it first.** |
| Growth | Freeze all marketing. Take down `tradingrw.com` Success Metrics and every strike percentage (see `MARKETING_PLAN.md` §2) |
| Counsel | Draft the non-registration disclosure wording |

### Week 2–3 — Incorporate and clean up

| Owner | Task |
| --- | --- |
| CS | **Incorporate TechCo.** SPICe+ Part A name reservation → Part B with MoA/AoA, DIN for directors, PAN, TAN, EPFO/ESIC, professional tax. AGILE-PRO for GSTIN and bank account |
| CS | **Incorporate ResearchCo.** Same route. MoA objects must cover research services **[COUNSEL]** |
| CS + Counsel | **Founder-to-company IP assignment deeds** for all pre-incorporation code, models, VWEPR methodology and designs. This is a diligence killer if missed |
| Counsel + CA | **TechCo → ResearchCo IP licence** at an arm's-length royalty. Transfer-pricing opinion if any non-resident shareholding |
| Counsel | **Shared-services agreement** — engineering, infra, support priced and documented |
| CS | Founder agreements, ESOP pool, vesting schedules. Do this **before** the round |
| CA | GST registration, bank accounts, **DPIIT/Startup India recognition** (free, unlocks tax and procurement benefits) |
| CS | **Trademark "Strat AI"** — Class 9 (software) and Class 36 (financial services). File in India now; Madrid Protocol later for the Phase 4 jurisdictions |
| CTO + team | RED 3 step 4–5: **execute the git history rewrite**, force-push, all collaborators re-clone, record the incident per the runbook |
| Growth | Rewrite `tradingrw.com` against `docs/compliance/BRAND_GUIDELINES.md` |
| Growth | Publish `AI_DISCLOSURE.md` as a linked public page on both sites |

### Week 4–6 — Make the gate real

| Owner | Task |
| --- | --- |
| Backend | **Build `GET /api/v1/internal/entitlement/{user_id}`** in api-web to the contract already specified in `entitlements.py`'s docstring (§6.1 below). Implement exactly that shape |
| Backend | Wire `kycVerified` into the **grant decision** (A3), not just the log |
| DevOps | Set `SKU_ENFORCE=1`. **CI test proving a TERMINAL user is refused FIND, DEBATE and QA.** Gate 0→1 requires proof by test, not eyeball |
| DevOps | **Delete `NEXT_PUBLIC_RESEARCH_BETA_OPEN`** from `docker-compose.prod.yml:454` and `frontend/Dockerfile:88`. Keep for local dev only, or remove entirely |
| Backend | Close the Next.js proxy identity hole — forward the authenticated user to the agent service so the per-user gate applies on the web path |
| Backend | A1: fail the request on recommendation-record write failure |
| Backend | A2: test asserting `backtest.py` output never reaches the SSE stream |
| CFO | **Open the lien-marked deposit** — ₹1 lakh at the ≤150-client tier, lien-marked to the RAASB. Hold it in a **liquid or overnight mutual fund** (SEBI permitted this from August 2025), so it is not dead capital |
| Founder | **Appoint the Compliance Officer** — mandatory for a non-individual RA, and not a rubber stamp |
| Compliance | Draft the policy set (§7) |

### Week 6–9 — Client onboarding from zero

The largest remaining build. Specs in §6.

| Owner | Task |
| --- | --- |
| Full-stack | **P4 KYC onboarding** — PAN, KRA upload and status, client agreement e-sign, MITC acknowledgement, **family-level fee-cap enforcement** |
| Full-stack | **P3 research report renderer** |
| Full-stack + Compliance | **P8b analyst-of-record workflow**; populate `ANALYST_OF_RECORD` |
| Backend | **P9 personal-trading surveillance** |
| Compliance | **P10 advertisement register** |
| Full-stack | **P13 grievance module** — in-app intake, SLA timers, SCORES and ODR escalation **[VERIFY current ODR onboarding steps for RAs]** |
| DevOps | A4: off-machine append-only backups + documented restore test |
| Backend | A5: broker and market-data adapter abstraction |

### Week 9–11 — File

| Owner | Task |
| --- | --- |
| CS + Counsel | Assemble the application pack (§8) |
| Founder | Pay the Schedule III fee — **₹5,000 application fee for body corporates and LLPs**. Registration fee per Schedule III **[VERIFY on the SEBI e-services payment module]** |
| Counsel | **File Form A via the SEBI Intermediary Portal** |
| Counsel | **Enlist with the RAASB — BSE Limited** (mandatory under the revised framework) |
| CTO | Commission the **CSCRF gap assessment** — confirm your applicability category first, thresholds are size-based **[VERIFY]** |

### Week 11–30 — Pendency

Respond to SEBI and RAASB queries; budget 3–6 months. Sell TERMINAL hard — this window is where TERMINAL
revenue proves the licence is not on your revenue critical path, which is the first thing a sophisticated
investor probes. If no substantive query arrives by day 180, escalate via counsel and consider seeking
informal guidance under the SEBI (Informal Guidance) Scheme, 2003.

### On grant

- Wire the **real INH number**, Compliance Officer contact, disclaimer and MITC link into every surface — **prominently, not in the footer**. In a market full of anonymous Telegram operators these are trust signals, not just obligations
- Restore the (now true) "SEBI-registered" language, always with the caveat that **registration does not guarantee performance**
- Complete **Google financial services verification** and **SEBI SI Portal advertiser verification**
- Launch RESEARCH behind KYC and fee-cap gating
- Hand `MARKETING_PLAN.md` §5 to Growth to unlock paid channels

---

## Part 6 — Build specifications

### 6.1 The entitlement endpoint (Week 4)

Implement exactly the contract `entitlements.py` already expects.

```
GET {INTERNAL_API_BASE_URL}/api/v1/internal/entitlement/{user_id}

200 → { "success": true,
        "data": { "sku": "RESEARCH",            // or "TERMINAL"
                  "canAccessResearch": true,     // authoritative boolean
                  "kycVerified": true,           // RA client onboarding done
                  "planName": "RESEARCH" } }

404 → user unknown, or caller IP not in INTERNAL_ALLOWED_IPS
```

Requirements:
- Restrict to `INTERNAL_ALLOWED_IPS`. The agent service host must be whitelisted or every request 404s and fails closed.
- Return **real booleans**, never `"true"` strings — `_extract_entitlement` checks identity against `True` deliberately.
- **After A3:** grant RESEARCH only when `canAccessResearch === true` **and** `kycVerified === true`.
- Honour the 300s cache TTL; call `entitlements.clear_cache()` on plan change so an upgrade takes effect immediately.

**Acceptance test (must be in CI):** a TERMINAL user receives `entitlement_required` on FIND, DEBATE
and QA, and succeeds on VERIFY. An unknown mode string is refused.

### 6.2 P4 — KYC and client onboarding (Week 6–9)

Nothing exists. Build order:

| Step | Requirement | Notes |
| --- | --- | --- |
| 1 | **PAN collection + format validation** | Mandatory for a fee-paying RA client |
| 2 | **KRA upload and status check** | Integrate a KYC Registration Agency. Store the KRA reference and status on the user |
| 3 | **Client agreement e-sign** | Versioned. Store the exact version the client accepted, plus timestamp and IP |
| 4 | **MITC acknowledgement — separately recorded** | The Most Important Terms and Conditions document must be acknowledged distinctly from the agreement. It states the fee ceiling and that SEBI registration is not a performance guarantee |
| 5 | **Family-level fee-cap enforcement** | **₹1,51,000 per annum per family** for individual and HUF clients, across **all** research services. SEBI has confirmed it applies without exception, revised every three years on the Cost Inflation Index. **The cap is per family, not per login** — model household linkage (self, spouse, dependent children, dependent parents) or you will breach it |
| 6 | **Hard block, not a warning** | A sale that would breach the cap must be refused at the API layer, with the refusal written to `interaction_log` |
| 7 | **Advance-fee ceiling** | Since April 2025 you may collect up to **one year** in advance. Enforce that ceiling in the billing logic — annual plans are permitted, multi-year is not |

**Design note:** your `entitlements.py` already anticipates `kycVerified`. Make the KYC flow write that
flag, and A3 makes it binding. That is the whole loop.

### 6.3 P3 — Compliant research report renderer (Week 6–9)

Every FIND and DEBATE output becomes a research report. Your pipeline already produces every input.

| Required field | Where it comes from in your code |
| --- | --- |
| Date and time of publication | `reco_store.created_at` |
| **Price at publication** | Tool inputs — capture and persist explicitly |
| Direction, entry, stop loss, take profit | `reco_store` `action`/`entry`/`stop_loss`/`take_profit` |
| Holding period / horizon | `reco_store.horizon` |
| Rationale | `rationale_json` — the 15-step pipeline output |
| **Risk factors** | **The Bear Agent output.** This is the mandated disclosure and you already generate it exhaustively |
| Reward-to-risk and volatility basis | `risk_reward`, ATR basis in `rationale_json` |
| Analyst of record | `analyst_of_record` — NULL until P8b |
| Entity name + INH number | Config, post-grant |
| Compliance Officer contact | Config |
| Conflict-of-interest disclosures | Static block + any holdings disclosure |
| **Standard SEBI disclaimer** | Static block, including that registration does not guarantee performance |
| AI-generated-content disclosure | Link `docs/compliance/AI_DISCLOSURE.md` |

**The insight worth internalising:** your Bear Agent is not a legal risk, it *is* the risk-factor
section SEBI requires. Most registered analysts write that section by hand and thinly. You generate it
adversarially and exhaustively. Frame it that way in the application cover note.

### 6.4 P8b — Analyst of record (Week 6–9)

`ANALYST_OF_RECORD` is unset, so every regulatory record names nobody. Your `reco_store.py` docstring is
right that NULL is more honest than a placeholder — but SEBI wants a named responsible analyst.

Design as a **logged supervisory layer, not a per-call bottleneck**: the NISM-certified analyst reviews
and signs off on published research, with the review recorded (reviewer identity, timestamp, decision).
**[COUNSEL on the acceptable degree of automation — this is the single most important product question
to put to your lawyer, because the answer determines whether Strat AI scales or becomes a human
bottleneck.]**

### 6.5 P9 — Personal-trading surveillance (Week 6–9)

Block or flag any trade by an employee or the entity that runs contrary to a live published
recommendation, or falls inside a blackout window around publication. Codify in the code of conduct and
**enforce in software** — you already have the recommendation store to check against.

### 6.6 P13 — Grievance module (Week 6–9)

In-app complaint intake, SLA timers, escalation paths to **SCORES** and **ODR**. Your
`interaction_log.for_user()` is already the subject-access query a complaint investigation needs — wire
the grievance view to it.

---

## Part 7 — Policy set (Week 4–6)

Draft all of these before filing. Several already exist in `docs/compliance/`.

| Policy | Status |
| --- | --- |
| Compliance manual | To draft |
| Code of conduct | To draft |
| Personal-trading and blackout policy | To draft — must match the P9 implementation |
| Conflict-of-interest policy | To draft |
| **Advertisement policy** | Partially covered by `BRAND_GUIDELINES.md` — formalise |
| **AI model governance policy** | ✅ `docs/compliance/AI_MODEL_GOVERNANCE.md` |
| **AI disclosure** | ✅ `docs/compliance/AI_DISCLOSURE.md` — needs publishing |
| Grievance redressal policy | To draft |
| Record-retention policy | To draft — must state the SEBI 5-year floor and the DPDP erasure carve-out |
| **Secret rotation runbook** | ✅ `docs/compliance/SECRET_ROTATION_RUNBOOK.md` |
| Business continuity / cyber resilience plan | To draft under CSCRF |

**The DPDP tension, resolved explicitly:** SEBI requires 5-year retention of client interactions; DPDP
requires purpose-limited retention and erasure on request. A statutory retention obligation overrides an
erasure request for that data class — but **document the carve-out in your privacy notice** rather than
discovering it during a complaint. Your `interaction_log.py` docstring already reasons this correctly;
lift that reasoning into the policy. **[COUNSEL]**

---

## Part 8 — Application pack checklist

**[VERIFY the current checklist against the RA Master Circular dated February 2026 before filing — SEBI's
August 2025 consultation proposed relaxing CIBIL, net-worth statement and infrastructure requirements.]**

☐ Form A, completed
☐ Certificate of Incorporation, MoA and AoA (ResearchCo) — objects covering research services
☐ PAN, GST registration
☐ Principal officer: degree certificate in a specified field + **NISM Series XV certificate**
☐ Compliance Officer: appointment letter, qualifications, NISM where applicable
☐ Certificates for all persons associated with research services
☐ **Lien-marked deposit evidence** — ₹1 lakh, RAASB lien, liquid/overnight fund holding
☐ Infrastructure details — office, systems, the QuestDB/Kafka/agent stack description
☐ Organisation chart and shareholding pattern (showing TechCo as parent)
☐ Declarations: fit and proper, no disciplinary history, no pending litigation
☐ CIBIL report **[VERIFY still required]**
☐ Net worth / asset-liability statement **[VERIFY still required]**
☐ Compliance manual and policy set
☐ Client agreement template + **MITC** template
☐ **AI disclosure and model governance documents** — not required, but include them. They pre-answer the questions SEBI's draft AI framework raises and signal a serious applicant
☐ Schedule III fee payment receipt
☐ RAASB (BSE) enlistment application

### Cover-note strategy

Write a short cover note. Most applicants do not, and yours has an unusually strong story:

1. **A hash-chained, append-only recommendation record** with model and prompt version hashes — every published recommendation is replayable years later and provably unaltered.
2. **A durable interaction log** recording what was communicated, to whom, when, including refusals.
3. **A deterministic pre-LLM guardrail** that refuses personalised advice, keeping the service inside the RA perimeter by construction rather than by policy.
4. **A read-only broker integration** enforced by an automated denylist test — no order-placement capability exists in the codebase.
5. **Honest-failure architecture** — missing data is reported as unavailable rather than fabricated. Point to commit `82e0cb0`.
6. **AI governance and disclosure documented in advance** of SEBI's guidelines.

That is a stronger control narrative than most registered shops can produce. Say it plainly, without
adjectives.

---

## Part 9 — Operating cadence after grant

| Frequency | Obligation | Owner |
| --- | --- | --- |
| Continuous | Interaction logging · recommendation records · advertisement register · personal-trading surveillance · family fee-cap enforcement · grievance SLA timers | Systems |
| Weekly | Marketing review against the banned list · new-partner **Regulation 16A** screening · model drift dashboard | Compliance + Growth |
| Monthly | Grievance report · **deposit-tier check against client count** · model register update · LLM cost per FIND run | Compliance |
| Quarterly | Internal compliance review · AI governance review · hash-chain verification (`reco_store.verify_chain()`, `interaction_log.verify_chain()`) · backup restore test | Compliance + CTO |
| Annually | Compliance audit + RAASB report · **CSCRF cyber audit: 100% of critical systems and a 25% sample of non-critical, with sampling rationale stated** · VAPT · policy refresh · NISM continuity | Compliance + CTO |
| Every 3 years | Fee-cap revision on the Cost Inflation Index | CFO |
| **Every 5 years** | **SEBI registration renewal.** SEBI has cancelled registrations for non-payment. Two independent reminders at 6 and 3 months | CS |
| By ~May 2027 | **DPDP full compliance** — itemised consent notices, purpose-based retention, security safeguards, 72-hour breach notification, data-principal rights. Penalties reach ₹250 crore | Counsel + CTO |

**Deposit tiers to watch:** ≤150 clients ₹1 lakh · 151–300 ₹2 lakh · 301–1,000 ₹5 lakh · 1,001+ ₹10
lakh, based on the maximum clients on any day in the previous financial year. Crossing 150 is a real
event — reconcile monthly.

---

## Part 10 — Indicative year-one cost

| Item | Estimate (INR) |
| --- | --- |
| Incorporation × 2 + secretarial | 40,000 – 80,000 |
| SEBI application fee (body corporate) | 5,000 |
| SEBI registration fee | **[VERIFY]** |
| RAASB (BSE) enlistment | **[VERIFY with BSE]** |
| Lien-marked deposit (recoverable, held in liquid MF) | 1,00,000 |
| NISM Series XV × 3 | 15,000 – 25,000 |
| Securities counsel — structuring, application, opinions, MITC, policies | 3,00,000 – 8,00,000 |
| Compliance Officer (retained, year 1) | 4,00,000 – 12,00,000 |
| CSCRF gap assessment + first cyber audit + VAPT | 3,00,000 – 10,00,000 |
| DPDP readiness | 1,00,000 – 3,00,000 |
| Trademark (2 classes) | 20,000 – 50,000 |
| Annual compliance audit | 75,000 – 2,00,000 |
| **Working total (ex-deposit)** | **≈ ₹13 lakh – ₹36 lakh** |

Budget **₹25 lakh** for a credible year-one compliance line. The curve is one-time-heavy,
recurring-light.

---

## Part 11 — Consolidated checklist

**Today**
☐ Purge "SEBI-registered" from `sku.ts:176`, `entitlements.py:255`, `agentErrorClassifier.ts:71`, 2 test files ☐ Determine repo visibility ☐ Rotate the Bedrock key ☐ Decide RED 2 interim posture

**Week 1**
☐ Engage counsel ☐ Book NISM × 3 ☐ Run `gitleaks` on full history ☐ Freeze marketing ☐ Draft non-registration disclosure

**Week 2–3**
☐ Incorporate TechCo ☐ Incorporate ResearchCo ☐ IP assignment deeds ☐ IP licence ☐ Shared services ☐ ESOP pool ☐ GST · DPIIT · banking ☐ Trademark (Class 9 + 36) ☐ Git history rewrite ☐ Publish AI_DISCLOSURE.md

**Week 4–6**
☐ Entitlement endpoint ☐ `kycVerified` in grant decision ☐ `SKU_ENFORCE=1` + CI proof ☐ Delete `RESEARCH_BETA_OPEN` from prod ☐ Close proxy identity hole ☐ Fail-hard on record write failure ☐ Backtest-SSE isolation test ☐ Deposit in liquid fund ☐ Appoint Compliance Officer ☐ Policy set

**Week 6–9**
☐ PAN + KRA ☐ Client agreement e-sign ☐ MITC acknowledgement ☐ Family fee-cap hard block ☐ Advance-fee ceiling ☐ P3 report renderer ☐ P8b analyst of record ☐ P9 surveillance ☐ P10 ad register ☐ P13 grievances ☐ Off-machine backups + restore test ☐ Broker adapter abstraction

**Week 9–11**
☐ Application pack ☐ Schedule III fee ☐ File Form A ☐ RAASB enlistment ☐ CSCRF gap assessment ☐ Cover note

---

## Part 12 — Sources

**Audited artefacts (commit `ccf29b5`):** `entitlements.py` · `reco_store.py` · `interaction_log.py` · `personalisation.py` · `graph.py` · `main.py` · `journal.py` · `prompt_version.py` · `hashchain.py` · `backtest.py` · `frontend/src/lib/sku.ts` · `frontend/src/lib/api/types.ts` · `frontend/src/hooks/useMacroIndicators.ts` · `frontend/src/components/quant/deep-quant/agentErrorClassifier.ts` · `frontend/src/components/fno/__tests__/scopeBoundary.test.ts` · `aggregator/src/kite_api.rs` · `docker-compose.prod.yml` · `frontend/Dockerfile` · `.env` · `CLAUDE.md` · `docs/compliance/*`

**Regulatory**
- [SEBI (Research Analysts) Regulations, 2014](https://www.sebi.gov.in/sebi_data/commondocs/RESEARCHANALYSTS-regulations_p.pdf) · Third Amendment 16 December 2024
- SEBI guidelines for Research Analysts, 8 January 2025 · [FAQs, July 2025](https://www.sebi.gov.in/sebi_data/faqfiles/jul-2025/1753269723942.pdf)
- SEBI (Intermediaries) (Amendment) Regulations, 2024 — Regulation 16A, effective 29 August 2024
- SEBI circular, *Safer participation of retail investors in Algorithmic trading*, 4 February 2025 · in force 1 April 2026
- [SEBI Consultation Paper, *Guiding Principles for Responsible usage of AI/ML*, 20 June 2025](https://www.sebi.gov.in/sebi_data/attachdocs/jun-2025/1750415065695.pdf)
- SEBI Cybersecurity and Cyber Resilience Framework, 20 August 2024, with clarifications 30 April 2025
- SEBI circular on relaxation of advance-fee restrictions for IAs and RAs, April 2025
- [Mint — January 2025 IA/RA guidelines, deposit tiers and fee structure](https://www.livemint.com/market/sebi-new-ias-ras-guidelines-independent-advisers-research-analysts-investment-advice-kyc-11736349404566.html)
- [Taxmann — SEBI guidance on the RA fee cap](https://www.taxmann.com/post/blog/sebi-guidance-on-ra-fee-cap-for-individual-and-huf-clients)
- [Cyril Amarchand Mangaldas — CSCRF client alert](https://www.cyrilshroff.com/wp-content/uploads/2024/09/Client-Alert-Cybersecurity-and-Cyber-Resilience-Framework.pdf)
- Digital Personal Data Protection Act, 2023 and DPDP Rules notified 13 November 2025
- [EY — DPDP Rules 2025 compliance guide](https://www.ey.com/en_in/insights/cybersecurity/transforming-data-privacy-digital-personal-data-protection-rules-2025)

*Content from external sources was rephrased for compliance with licensing restrictions.*
