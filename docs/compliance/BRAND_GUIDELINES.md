# Brand Guidelines — Compliant Copy Standard

**Owner:** [COMPLIANCE OFFICER — unassigned]
**Version 1.0 · August 2026**

> Internal, binding. The banned list from `docs/business/GO_TO_MARKET.md` §2 turned into a review
> checklist, plus the findings of the repo-wide copy audit required by step **0.16** of
> `docs/business/PLAN_OF_ACTION.md`.
>
> **This is not legal advice.** The classifications in §1 reflect the regulatory reading in
> `GO_TO_MARKET.md` §1 and must be confirmed with securities counsel before an external campaign runs.
> **[COUNSEL — review §1 and §5.]**

---

## 0. The standard, in one sentence

**Describe what the product does. Never describe what the user will get.**

Every rule below is that sentence applied. A claim about product *behaviour* ("it rejects a stop
tighter than 1.5× ATR") is checkable, true, and carries no regulatory surface. A claim about *outcome*
("traders using it do better") is a return representation whether or not a number is attached.

This is not a defensive posture. `GO_TO_MARKET.md` §2 makes the restraint the positioning — the
category is *pre-trade risk adjudication* and the core message is that the most valuable output is the
word no. Compliant copy and good copy are the same copy here.

---

## 1. The banned list — enforce at review

Nothing on this list ships. No exceptions, no "just in a caption", no reposting a partner's version.

| # | Never publish | Why | Also catches |
| --- | --- | --- | --- |
| 1 | Assured, guaranteed or "consistent" returns — **express or implied** | SEBI (Intermediaries) (Amendment) Regulations 2024, Reg 16A, effective 29 Aug 2024 | "Beat the market", "consistent profits", "steady gains", "grow your capital", "profitable trades" |
| 2 | Win rate, hit rate or accuracy **as a headline or promotional figure** | Performance representation | "65% accurate", "2 out of 3 calls", a win-rate screenshot, an accuracy badge |
| 3 | P&L screenshots | Performance representation | Broker P&L, journal equity curves, a highlighted green number |
| 4 | Client testimonials referencing profits | Reg 16A, indirect return claim | "I made ₹X", "paid for itself", DMs quoted as social proof |
| 5 | "SEBI approved" / "SEBI certified" / "SEBI-approved algo" | No such approval exists in any form | Any construction implying SEBI endorses the product. Registration is not approval |
| 6 | Backtested or hypothetical performance without the advertisement code's required disclosures | SEBI RA advertisement code, 8 Jan 2025 | "Would have returned", simulated equity curves, paper-trading results presented as evidence |
| 7 | "Risk-free", "sure shot", "no-loss" | Reg 16A | "Zero risk", "can't lose", "downside protected" |
| 8 | Countdown-timer urgency on a securities subscription | Advertisement code | Expiring-offer banners, "3 seats left", artificial scarcity |
| 9 | Comparison implying superior returns to a named competitor or index | Reg 16A, indirect claim | "Outperforms X", "unlike Y, we actually make money" |
| 10 | Any association with unregistered advice or return claims — **direct or indirect** | Reg 16A | An affiliate, influencer, reseller or community moderator giving stock calls. Screen and terminate |

### 1.1 Two additions specific to this product

| # | Never publish | Why |
| --- | --- | --- |
| 11 | Any claim the AI **executes**, **trades**, or **manages money** | It cannot. `providers::BrokerProvider` has no order method. Beyond being false, it describes a different licence — see `SEBI_COMPLIANCE_BLUEPRINT.md` §1.3 |
| 12 | Any claim the product is **personalised**, **tailored**, or **suited to you** | The RA/IA boundary and the US publisher's exclusion both require strict impersonality. "Advice for your portfolio" is an IA claim. So, more subtly, is "tells you what to buy" |

Rule 12 is the one most likely to be broken by good-faith marketing copy, because personalisation is
the reflex of every SaaS growth playbook. It is prohibited here.

### 1.2 The conviction score

The conviction score is **not** a probability, an expected return, or a confidence interval, and must
never be presented as one. "Conviction 8/10" alongside a rupee figure reads as an expected value even
with no claim made — that is an *implied* return representation and is caught by rule 1. Present it
as what it is: a relative ranking of setup quality within our own framework.

---

## 2. Safe language — the approved vocabulary

From `GO_TO_MARKET.md` §2's message hierarchy. Every line below is a product fact.

| Instead of | Say |
| --- | --- |
| "Find profitable trades" | "Evaluate a setup before you take it" |
| "AI that beats the market" | "AI that tells you when *not* to trade" |
| "65% win rate" | "Three hard rules it cannot break: stop ≥ 1.5× ATR, a reward-to-risk floor by profile, correct level ordering" |
| "Trusted by N traders" | "Watch it think — every tool call, every number, every reason, streamed live" |
| "Never miss a move" | "When the tape and the news disagree, it holds" |
| "Institutional-grade intelligence" | "Sub-second ticks parsed in Rust. Five years of history. Native desktop, not a browser tab" |
| "Complete market picture" | "When data is missing, it says so. It never fills the gap with a guess" |

**"Institutional-grade" is not banned, but it is unsubstantiated puffery and it is weaker than the
engineering claim it replaces.** Prefer the specific fact. It is more credible to the audience that
converts.

---

## 3. Review checklist

Run before publishing anything external — landing page, docs page, social post, ad creative, app-store
listing, installer metadata, release notes, conference slide.

**Screen for a claim:**

- [ ] No outcome claim. Nothing about what the user will get, earn, avoid or achieve
- [ ] No number that is a performance figure (return, win rate, accuracy, expectancy, Sharpe, drawdown)
- [ ] No implied comparison to a competitor, an index, or "trading without it"
- [ ] No testimonial, screenshot or quote that references money made
- [ ] Nothing implying SEBI approval, endorsement or certification
- [ ] No urgency or scarcity device on a securities subscription

**Screen for a boundary breach:**

- [ ] Nothing implying the product executes, trades, or manages money (rule 11)
- [ ] Nothing implying the output is personalised or suitable for the reader (rule 12)
- [ ] Conviction, tiers and scores not presented as probabilities or expected values (§1.2)

**Screen for accuracy — a false claim about the AI is a disclosure defect, not a typo:**

- [ ] Every named model matches `AI_MODEL_GOVERNANCE.md` §2
- [ ] Every described feature exists in the shipped build, not the roadmap
- [ ] Any structural claim ("it cannot place an order") is still true of the current code

**Screen for the licence boundary — pre-licence only, and it expires on INH grant:**

Until the INH is granted, the sellable product is **TERMINAL alone** (`GO_TO_MARKET.md` §3.1, §5).
This screen exists because the previous three can all pass on a page that is nonetheless unlawful to
publish today.

- [ ] Nothing offers, prices, or takes payment for RESEARCH — FIND, DEBATE, conviction score, journal
      or QA mode. Naming them as *existing capability* is fine; **selling access to them is not**
- [ ] No waitlist, "coming soon" or early-access form that implies advice arrives on a date. A date
      we do not control is a promise we cannot keep, and the regulator reads it as pre-selling
      unlicensed research
- [ ] No paid financial-services advertising of any kind. Google Ads financial-services verification
      and SEBI SI Portal advertiser verification both gate on the registration
- [ ] No entity or registration language that could read as registered status — no INH placeholder
      rendered, no "SEBI-registered" in any tense, including "soon" and "applied for"
- [ ] The word "research" is not used as the *offer*. It may describe what the analysis is; it must not
      name a thing being sold

**Post-licence, additionally:**

- [ ] Entity name, INH registration number, Compliance Officer contact, standard SEBI disclaimer and
      MITC link displayed **prominently** — `GO_TO_MARKET.md` §3.2 is explicit that these go in the
      body, not the footer
- [ ] Logged in the advertisement register (blocker P10): creative, approver, live dates, retained copy

**Assume a machine reads everything.** SEBI has been running AI surveillance of financial social media
and has flagged roughly 20,000 fraudulent posts since November 2025. A Telegram message and a billboard
carry the same exposure.

---

## 4. Copy audit — findings

Repo-wide audit performed 2026-08-17/18 against §1.

**Method, in two passes — the second pass is the one that mattered.**

*Pass 1* scanned for the banned **claim** vocabulary of §1: returns, profit, guaranteed, win rate,
accuracy, testimonial, risk-free, "SEBI approved". *Pass 2* scanned for **boundary and accuracy**
language — rule 11 (execute / autonomous / on your behalf / places an order), rule 12 (personalised /
tailored / suited to you), §1.2 (probability language attached to a score), and named models that must
match `AI_MODEL_GOVERNANCE.md` §2. Both passes covered `*.tsx`, `*.ts`, `*.rs`, `*.py`, `*.json` and
`*.md`, followed by manual review of every rendered string surface: app and page metadata, mode
selectors, gate and paywall copy, system log messages, and the published documentation set.

**Pass 1 found nothing in the UI. Pass 2 found four rendered-string defects, one of them in paid-plan
promotional copy.** That gap is the single most useful result of this audit and it is recorded in §5:
a scan for the words a regulator's advertising rules name will not find a copy defect that breaches
the *licence boundary*, because those two failures do not share a vocabulary.

**Neither pass would have caught the worst finding (§4.0), because both passes trusted the wrong
source.** A word-scan cannot tell you that a vendor name is false; only checking the claim against the
code can. Verifying a *factual* claim is a third operation, distinct from scanning for banned words, and
it is now step 3 of the §3 checklist rather than something the scan is assumed to cover.

### 4.0 The most severe finding, and it was in this audit's own deliverables

Numbered 18 because it was found last. Ranked first because nothing else here comes close.

| # | Location | Was | Problem | Now |
| --- | --- | --- | --- | --- |
| 18 | `docs/compliance/AI_MODEL_GOVERNANCE.md` §2 and `docs/compliance/AI_DISCLOSURE.md` §4 — **the latter written for publication** | Provider column read "NVIDIA NIM" and "HuggingFace" | **Both named the wrong data processor.** NVIDIA appears in **no functional code path** in this repo — only two stale comments and test-teardown calls. HuggingFace appears only as a commented-out alternative. The actual default endpoint for every non-OpenRouter LLM call is `https://api.freemodel.dev/v1/chat/completions` (`agents/quant-rag/src/llm.rs:20`, `frontend/src-tauri/src/services/llm.rs:253`). Naming the wrong party as the recipient of user data on a page written to satisfy an AI-disclosure obligation is a worse defect than any marketing claim in §4.2 — and §2's inventory had also **omitted two entire LLM call sites**, which that document's own rule calls a policy breach | §2 rewritten: an "Endpoint / provider" column, 6 rows → 8, every row citing a source file, and a dated correction note. `AI_DISCLOSURE.md` §4 corrected to `freemodel.dev`, with the disclosure that both named providers are *routers* rather than the labs that run the models — material to anyone asking who receives their request |

**How it happened, stated plainly: I wrote both tables from `docs/ARCHITECTURE.md` instead of from the
code — while writing §5.1 lesson 2, which is the rule against exactly that.** That is the strongest
available evidence that this rule cannot be a thing people remember. §2 now carries it mechanically:
*no row may be sourced from another document.*

It also reframes finding 11. Fifteen internal sites carrying a dead model name is untidy; fifteen
internal sites that **fed a false vendor into a publication-bound compliance page** is a live defect
with a demonstrated propagation path — architecture doc → compliance doc → public page. Finding 3 took
the same path to shipped installer metadata. Two independent leaks from one stale source is the pattern,
not a coincidence.

### 4.1 What was genuinely clean

There are **no** testimonials, P&L screenshots, equity curves, countdown timers, scarcity devices,
competitor comparisons, or "SEBI approved / certified" references anywhere in the tree. No return,
profit or win-rate claim appears in any rendered string — every pass-1 hit inside
`frontend/src/**/*.tsx` was a code comment describing an engineering invariant ("Guarantees the line
can never render vertically"). Pass 2 found **zero** rule-12 (personalisation) hits in rendered
strings, which matters more than the rest of this section: it is the claim class that would most
directly contradict the RA boundary and the P8a guardrail, and the UI does not make it anywhere. The
`ResearchGate` upgrade CTA introduced by blocker P1c ("Research plan required" / "VIEW PLANS") is
non-promissory.

That is a good starting position on the claim axis and worth not degrading.

### 4.2 Fixed

Ordered by severity. Findings 1–4 and 19 are **rendered strings a user sees**; 1 and 2 are promotional
surfaces, which is the worst place for a defect. Findings 11 and 12 were originally logged under §4.4 as
"flagged, not changed" and were fixed once §4.0 showed what they had already caused.

| # | Location | Was | Problem | Now |
| --- | --- | --- | --- | --- |
| 1 | `frontend/src/components/quant/deep-quant/PremiumPaywall.tsx:56,58` — **paid-plan promotional copy** | "DeepSeek v4 Autonomous ReAct Agent Loop"; "Virtual Execution & Paper Broker Sync" | **Three faults in the copy that sells the subscription.** (a) *DeepSeek v4* does not run this loop and does not exist — see finding 3. (b) **"Autonomous"** is the single worst word to sell this product on: rule 11, and it is the exact impression the whole compliance position depends on not creating. (c) **"Paper Broker Sync"** implies orders reach a broker; paper trading never contacts one | "Multi-Step AI Research Loop with Live Tool Calls"; "Local Paper-Trade Simulator — No Broker Orders" |
| 2 | `frontend/src/components/quant/DeepQuantPanel.tsx:316,317` — mode selector | "Find High-Probability Trade" / "Autonomous breakouts & quant scanning" | **"High-Probability" states a probability about the outcome** — a mode *named* for a probability is precisely the misreading §1.2 exists to prevent, and it sits one word from rule 2. "Autonomous" again reads as acting without the user (rule 11). The main action button ("FIND QUANT TRADE") was already fine | "Find a Trade Setup" / "Scans breakouts & quant signals" |
| 3 | `frontend/src-tauri/tauri.conf.json:78` — **shipped installer metadata** | "powered by DeepSeek v4 Pro" | **No such model exists.** The recommendation model defaults to `openai/gpt-4o` (`graph.py:976`) and is configurable per deployment and per run; the real DeepSeek model — in a *different* service — is `deepseek-ai/DeepSeek-V3-0324`. A misstated model in shipped metadata is an AI-disclosure defect under the 8 Jan 2025 guidelines, which make the RA accountable for the accuracy of its AI disclosures. Also dropped "institutional-grade" | Factual pipeline description, ending "It analyses instruments and produces research: it cannot place, modify or cancel an order" |
| 4 | `frontend/src/app/layout.tsx:14,15` | "AI Trader - Trade Terminal" / "Institutional-grade AI-powered trading." | **"AI Trader" and "AI-powered trading" both claim the product trades** — rule 11, and false. "Institutional-grade" is unsubstantiated puffery (§2). The title was also stale: `productName` is "Strat Ai" | "Strat Ai — Market Analysis Terminal" / "Market analysis and charting terminal for NSE and NFO." |
| 5 | `frontend/src-tauri/tauri.conf.json:77` | "AI-Powered Institutional Trading Terminal" | "Trading Terminal" is defensible — it *is* a terminal — but "Institutional" is unsubstantiated | "AI-Assisted Market Analysis Terminal" |
| 6 | `frontend/src/components/quant/deep-quant/ActionableTradePlan.tsx:51` — system log | "Failed to execute trade" | Minor, and worth fixing because it is cheap: the paper-trade path is otherwise scrupulously labelled ("Approve & Execute (Virtual)", "Simulated Trade Executed", "[Paper Engine]"), and the `console.error` on the line above already said "paper". Only the user-visible log dropped the qualifier | "Failed to execute paper trade" |
| 7 | `docs/DEEP_QUANT_ANALYSIS.md:3`, `:942` | "deep profitable trades" | Implied-return language (rule 1) framing a technical document that is a candidate for the engineering-credibility content programme (`GO_TO_MARKET.md` §3.1) | "high-conviction setups"; "the 'deep' in deep quant" |
| 8 | `docs/product-detailed.md:169`, `docs/ARCHITECTURE.md:128` | "portfolio risk metrics (Sharpe Ratio, Max Drawdown, Beta, Alpha)" on the Investor surface | **Documentation drift, and a compliance one.** Blocker P6 removed those metrics from `useMacroIndicators.ts`. The docs described a performance surface that no longer exists — simultaneously misdescribing the product and preserving the exact claim P6 removed | Describes the discipline metrics `computeDisciplineMetrics` actually renders |
| 11 | **Repo-wide, 24 sites.** Code: `agents/quant-rag/Cargo.toml:5`, `src/engine.rs:3,181,437`, `src/main.rs:3,8,33`, `agents/sentiment/src/analyzer.js:16,25`, `src/claude.js:1,4,9,19`, `agents/sentiment/package.json:4,16`, `frontend/src-tauri/src/services/llm.rs:3,12`, `tauri.conf.json:28`. Docs: `ARCHITECTURE.md:19,21,133,134,137,149`, `COMPLETE_ANALYSIS.md:37,165,280`, `product-detailed.md:140`, `PRODUCTION_SETUP.md:39-41,176-178`, `system_architecture_and_data_flows.md:195,205,220,268,276`, `README.md:568`, `DEEP_QUANT_ANALYSIS.md:998` | "DeepSeek v4 Pro"; "NVIDIA NIM"; "HuggingFace Inference Router" | **Originally logged as a stale model name. It is worse than that: the vendor was wrong too, and it had operational and security consequences, not just cosmetic ones.** Four distinct defect classes: (a) a **non-existent model**, "DeepSeek v4 Pro" — the naming convention of an entire service and six documents; (b) a **false vendor** — NVIDIA is called by no code path, and the sentiment agent's own header declared a HuggingFace default its code has never used; (c) **`PRODUCTION_SETUP.md` instructed operators to set `NVIDIA_API_KEY=nvapi-…` and troubleshoot against `integrate.api.nvidia.com`** — a credential nothing reads and a host nothing contacts, so anyone following the runbook provisioned a real key at a real vendor for nothing; (d) the shipped **CSP allowlisted `integrate.api.nvidia.com`** in `connect-src` — dead config that widened the webview's egress surface for a host with no purpose. This set is also the proven origin of findings 3 and 18 | All 24 corrected. Docs now **cite** `AI_MODEL_GOVERNANCE.md` §2 instead of restating a model name, closing the propagation path at the source. `PRODUCTION_SETUP.md` documents the three real `LLM_*` variables; the dead CSP host is removed; `quant-rag` now **logs its resolved model and endpoint at startup**, so the deployed value is recoverable from logs rather than inferred from a doc |
| 19 | `agents/quant-rag/src/engine.rs:437` — rendered in the insight HUD | `analysis_text: format!("DeepSeek Error: {}", e)` | Found while fixing finding 11 and worth separating, because unlike the rest of that set **this string reaches the user**: the fallback `MarketInsight` is broadcast to the frontend on any LLM failure. The endpoint is configurable, so it named DeepSeek in deployments running something else — and the live `.env` runs `gpt-5.5`, so it was already wrong in production | `format!("LLM Error: {}", e)`, with a comment recording that this field is rendered |

### 4.3 Retained deliberately, with an annotation

**What the finding-11 sweep deliberately did not touch, so it is not read as more complete than it is:**
the word "DeepSeek" on its own survives in roughly a dozen internal comments and diagrams
(`docker-compose.yml:164`, `aggregator/src/quant/mod.rs:5`, `tools/load_tester/src/main.rs:22`,
`ARCHITECTURE.md:143-144`, `technical_indicators_analysis.md:246,388`,
`system_architecture_and_data_flows.md:29,265,344,355`, `audit_logger.rs:4`).
Those are **accurate** — `deepseek-ai/DeepSeek-V3-0324` is the code default for those services. Only the
false identifier ("v4 Pro") and the false vendors were wrong, and only those were changed. Genericising
every correct mention would have been churn with a real chance of introducing a new error, which is the
failure mode this whole section is about. They are, however, still model names in documents, so §2 remains
the citation and these are not sources.

**One item first triaged into this list was moved back out** — finding **21**, below. The lesson is that
"harmless because nothing reads it" and "harmless because it is accurate" are different judgements, and
the first one does not survive contact with a reviewer.

| # | Where | What | Severity |
| --- | --- | --- | --- |
| 21 | `frontend/src-tauri/tests/api_tests.rs` — `isolate_env()` and the inlined copy of it in TEST 6 | Test teardown cleared `NVIDIA_API_KEY` and `NVIDIA_NIM_API_URL` alongside `HF_*` and `DEEPSEEK_API_URL`. No code path reads any of them. Read literally, an env-isolation list is the most credible provider inventory in a repo — it looks like the set of credentials the system can consume — so leaving two vendors we have never called in it undercuts §2 exactly where a reviewer would go looking for corroboration | Low as behaviour, real as evidence |

Fixed rather than annotated, because the annotation would have had to say "ignore this list", and a list
that must be ignored is better rewritten. The removals are retained — a developer's shell may still
export these from an older setup — but they are now grouped under an explicit **legacy, read by nothing**
heading that points at §2 as the only inventory. The three names the code actually reads
(`LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL`, per `resolve_endpoint` / `resolve_model` /
`resolve_api_key` in `services/llm.rs`) are now listed separately from the dead ones.

**And it was hiding a live test defect (finding 22).** TEST 6 ended its inlined copy with
`set_var("DEEPSEEK_API_KEY", "TEST_KEY")` — a variable `resolve_api_key()` does not read, and which
`isolate_env()`'s own comment already warned must not be used. The test passed only because
`ALPHA_TEST_MODE` makes `resolve_api_key()` fall back to a synthetic `TEST_KEY`. Removing that fallback
would have broken an audit-logger test for a reason unrelated to audit logging. TEST 6 now calls
`isolate_env()` and re-enables `ALPHA_TEST_MODE` itself. `cargo test --test api_tests` — 6 passed,
0 failed.

The file header (`api_tests.rs:4`) and the env-lock comment (`:25`) were corrected in the same pass:
"DeepSeek LLM today" → "the LLM endpoint today", and the example variable `DEEPSEEK_API_KEY` → the real
`LLM_API_KEY`. The `test_deepseek_*` function names were left alone — they are test identifiers, not a
disclosure surface, and renaming them buys nothing a reviewer can see.

**One more, not a false claim but an incomplete one.** `docs/technical_indicators_analysis.md:279` read
"Configurable via 3 env vars — supports HuggingFace, OpenAI, Groq, local Ollama." Every word of that is
true: the client takes any OpenAI-compatible endpoint, so HuggingFace *is* supported. What it omitted is
which one is **deployed** — and a list of supported providers with no default stated reads as a list of
providers in use. That is close enough to how finding 18 happened to be worth closing: the line now names
the three real variables, states that the list is capability rather than deployment, and cites §2. Not
counted in finding 11's 24, because nothing there was wrong.

| # | Location | Content | Why it stays |
| --- | --- | --- | --- |
| 9 | `README.md:374`, `docs/product-detailed.md:206` | Journal win rate and expectancy | **Internal calibration, which is permitted and wanted** — `AI_MODEL_GOVERNANCE.md` §6 explains the split. Annotated in place as internal model calibration, never a user-facing or marketing figure, so it cannot be lifted into copy by someone reading the README for material |
| 10 | `agents/deep-quant-loop/prompt.md` | Win rate and expectancy throughout | The agent's own instruction to consult its track record and size conviction *down* on historically losing setups. Model monitoring, never published |

### 4.4 Flagged, not changed

| # | Location | Content | Assessment |
| --- | --- | --- | --- |
| 12 | `frontend/src/store/useQuantStore.ts:244` | `{ id: 'tllm/deepseek_v4', label: 'DeepSeek V4' }` in `MODEL_PROVIDERS_OMNIROUTE` | A **gateway routing ID**, not a claim about what powers the product — a menu of what the omniroute proxy exposes. Left alone because I cannot verify the proxy's catalogue from this repo. **Upgraded on review:** omniroute is the *default* gateway (`LLM_GATEWAY` falls back to it unless `NEXT_PUBLIC_LLM_GATEWAY=openrouter`, `:250-251`), so this list is what beta users actually see, and the label "DeepSeek V4" is therefore a **rendered** model name — for a model no lab publishes under that name. Selection is locked on this gateway (`MODEL_SELECTION_LOCKED`, `:253`), so the practical effect today is a picker showing a name it will not use. **Verify the ID resolves at the proxy and relabel to whatever it actually routes to**; a locked picker displaying an unverifiable model name is a §1.2 problem, not just an engineering one |
| 13 | `docs/hold-issue-session.md:989` | "At the ~65% win rate these oversold-at-support bounces carry, 1.3 R:R is clearly profitable (+0.5R/trade expectancy)" | A specific performance claim with a win-rate figure. It is an accurate record of an internal engineering decision, and rewriting engineering history to look compliant would be the wrong instinct. **But it is exactly the sentence that must never be lifted into anything published.** The file is a session transcript; it is not marketing material and must never be treated as a source for it |
| 14 | `docs/hold-issue-session.md`, `.kiro/specs/**` | Backtest win rates ("≈46% win rate, +0.38R on RELIANCE 1d") | Same reasoning. Internal engineering evidence. Note that rule 6 means these figures cannot be published **even with disclosures** unless the full advertisement-code disclosure set accompanies them |
| 15 | `README.md:1–3` | "Institutional AI-Powered Quantitative Trading Terminal", "institutional-grade" | Puffery, not a banned claim, and the README is developer-facing under a proprietary licence. Align with §2 when the README is next revised. Low priority — but it becomes high the moment any of it is copied onto a public page, which is how finding 3 happened |

### 4.5 Open, needs a decision

| # | Item | Blocked on |
| --- | --- | --- |
| 16 | Whether **any** accuracy figure appears on the public AI disclosure page. The draft AI/ML framework asks for accuracy disclosure; rule 2 and the advertisement code restrict performance figures | **[COUNSEL]** — tracked as `AI_MODEL_GOVERNANCE.md` §10 item 5 and `AI_DISCLOSURE.md` §8 item 4 |
| 17 | Public naming of the model vendors. `AI_DISCLOSURE.md` §4 names **OpenRouter** and **freemodel.dev** for transparency, and states that both are routers rather than the labs running the models; confirm no vendor agreement prohibits naming them, and confirm the router disclosure is sufficient where the ultimate processor is not named because it is not knowable to us | Vendor terms review |
| 20 | Whether the **omniroute** beta gateway must be named in `AI_DISCLOSURE.md` §4 at all. It is the default gateway (`AI_MODEL_GOVERNANCE.md` §2.1), so for beta users it — not OpenRouter — is the party receiving the request, and §4 currently does not mention it | Confirm the deployed `OPENROUTER_BASE_URL`, then a disclosure decision. **Do not publish §4 until this is settled** — it is the same class of error as finding 18 |
| 23 | **Approval of the website copy.** `docs/compliance/WEBSITE_COPY.md` was drafted 19 August 2026 and screened against §1–§3 by its author. Under §5.1 lesson 3 that is not sufficient to publish | A named approver who is not the author. The Compliance Officer role is unfilled (`PLAN_OF_ACTION.md` §4.3 item 0.12), so today there is nobody eligible to sign it |

### 4.6 The website — audited as a specification, not as a page

The §4 audit covered **the repository**. The live website is not in the repository, so it was never in
scope — and it is the single highest-exposure copy surface the company has. That gap is closed from the
supply side rather than the inspection side: rather than auditing the existing page string by string
from outside the repo, the replacement copy was **written inside it**, at
`docs/compliance/WEBSITE_COPY.md`, where the three §3 screens and the pre-licence screen can be applied
to it and where every claim can name the file or commit that substantiates it.

Three consequences worth stating, because each is a trap this document has already walked into once:

- **Substantiation is per claim, not per page.** Finding 18 happened because a table was written from
  another document. Every row in the copy file cites code or a commit; **no row may cite a document**,
  including this one.
- **A claim can be safe as a message and false as a fact.** `GO_TO_MARKET.md` §2's message hierarchy is
  approved *positioning*; it is not verified *description*. Both checks are required, and only the copy
  file does the second.
- **Drafting is not approval.** Item 23 above. The copy file is publishable-quality and unpublishable
  until someone other than its author signs it.

**When the live page is replaced, the old strings do not disappear from the obligation.** Blocker P10's
advertisement register must retain what was published, with dates — so capture the current page before
it is overwritten, rather than after.

---

## 5. Enforcement

| Frequency | Activity | Owner |
| --- | --- | --- |
| **Every publication** | §3 checklist, signed off before publishing | Author + [COMPLIANCE OFFICER] |
| **Weekly** | Marketing review against §1; Reg 16A screening of any new partner, affiliate or moderator | [COMPLIANCE OFFICER] |
| **On every release** | Re-run **both** §4 audit passes over `frontend/src`, app metadata and any changed docs. Installer metadata, paywall copy and mode labels are all user-facing copy — findings 1 and 3 shipped precisely because nobody treated them that way | Release owner |
| **Quarterly** | Re-read §1 against current SEBI advertising guidance; update on any change | [COMPLIANCE OFFICER] + [COUNSEL] |

**Community rule, absolute:** no stock calls, by anyone, including moderators and staff. This is a
Reg 16A requirement dressed as culture, and `GO_TO_MARKET.md` §3.1 is right that enforcing it publicly
is itself brand evidence.

### 5.1 Three lessons this audit actually produced

**1. Scan for the boundary, not just for the banned words.** Pass 1 — the scan for the vocabulary SEBI's
advertising rules name — returned a clean UI. Every real defect came from pass 2. "Autonomous",
"High-Probability", "Broker Sync" and a wrong model name are not on any banned-word list, and three of
them were in copy selling the product. §3 is split into three screens for this reason; running only the
first is worse than useless because it produces a clean report.

**2. A false claim about the AI is a compliance defect, not a typo.** Findings 1, 3 and 11 are one
defect that propagated: an incorrect model name entered the architecture docs, was copied into shipped
installer metadata and into paid-plan promotional copy, and was *already flagged as stale* at
`DEEP_QUANT_ANALYSIS.md:97` without anyone acting. Under the 8 Jan 2025 guidelines the accuracy of AI
disclosures is the RA's own non-delegable responsibility. Model identifiers have exactly one source of
truth — `AI_MODEL_GOVERNANCE.md` §2 — and copy cites it rather than the nearest doc.

**3. One authorship rule.** A person writing copy should not be the person approving it. Finding 3 is
the argument: a false model name survived into a shipped installer because the person who wrote it was
the only person who read it.
