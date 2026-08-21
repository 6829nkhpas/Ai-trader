# AI Model Governance Policy

**Owner:** [COMPLIANCE OFFICER — unassigned]
**Model Risk Owner:** [CTO — unassigned]
**Version 1.0 · August 2026**

> Internal policy governing every AI/ML model used to produce, support or publish research output.
> Written to close blocker **P12** in `docs/business/PLAN_OF_ACTION.md` §4.2 and step **0.14**, against
> the framework in `docs/business/SEBI_COMPLIANCE_BLUEPRINT.md` §4.2.
>
> **This is not legal advice.** The regulatory mapping in §1 must be confirmed with securities counsel
> before this policy is represented to SEBI or to a client. **[COUNSEL — review §1 and §9.]**

---

## 0. Why this document exists, and why it exists *now*

Two obligations are already binding, and one large one is not yet.

- **Binding — AI/ML system reporting.** SEBI's 2019 circulars require reporting of AI/ML systems
  offered or used. This is a **filing duty, not a licence**, and it applies whether or not the tool
  touches orders.
- **Binding — disclosure of the extent of AI usage.** The 8 January 2025 RA guidelines require
  Research Analysts and Investment Advisers to disclose the extent of AI usage in their offerings and
  hold them **responsible for data security and applicable compliance**. Accountability cannot be
  outsourced to a model vendor. If a model we did not train produces a bad recommendation, the RA owns
  it.
- **Draft — responsible AI/ML principles.** The consultation paper *Guiding Principles for Responsible
  usage of AI/ML in securities markets* (20 June 2025, comments closed 11 July 2025) proposes
  **Equality, Accountability, Transparency (explainability and auditability), and Safety &
  Reliability**, plus plain-language disclosure of a model's purpose, risks, accuracy and limitations.
  As of August 2026 it remains a draft.

Writing this policy before it is demanded is deliberate. Most of what the draft framework asks for is
already a property of this system rather than a document about it — §5 lists which control satisfies
which principle, with file references, so an inspection can be answered by pointing at code.

**One scope note that decides most of this policy:** the largest AI risk in a trading product is an
autonomous order. That risk is **absent by construction here**, not mitigated. The read-only broker
interface (`frontend/src-tauri/src/providers/mod.rs`, `trait BrokerProvider`) has no
order-placement method, so no prompt, tool definition or model upgrade can reach one. Paper trading
(`frontend/src-tauri/src/execution/paper.rs`) never contacts a broker. Everything below therefore
governs *research output*, which is the only thing a model here can emit.

---

## 1. Scope and regulatory mapping

| # | System | What it produces | Regime treatment |
| --- | --- | --- | --- |
| 1 | **Deep Quant agent** (`agents/deep-quant-loop/`) | Directional research recommendations (FIND), critiques (DEBATE), validations (VERIFY), impersonal Q&A (QA) | **In scope, highest tier.** Client-facing research output. RESEARCH SKU only |
| 2 | **Market-insights agent** (`agents/quant-rag/`) | Anomaly headlines and commentary broadcast on WS :8083 | **In scope.** Client-facing narrative text |
| 3 | **Sentiment agent** (`agents/sentiment/`) | A sentiment score consumed as one input among many | **In scope, lighter tier.** Never published alone; contributes to a recommendation |
| 4 | **Deterministic quant engines** (`frontend/src/charting/engines/`, `frontend/src-tauri/src/quant/`, `agents/deep-quant-loop/options.py`) | Indicators, regime classification, option analytics, validator verdicts | **Out of AI scope, in scope for change control.** Pure functions, no learned parameters, property-tested. Not "AI/ML" — do not describe them as such in disclosures |

The draft framework's lighter tier applies to purely internal models. **Nothing in rows 1–3 qualifies
for it** once the RESEARCH SKU is sold, because all three reach a client. Row 4 is not a model.

---

## 2. Model inventory

Maintained here. **A model reaching a client without a row in this table is a policy breach**, and the
inventory is reviewed quarterly under §8.

Both the **model** and the **endpoint** are recorded, because a model identifier alone does not say
whose infrastructure processed the request — which is the question a client, an auditor and a data
protection review each ask.

| System | Default model | Where configured | Override path | Endpoint / provider | Owner |
| --- | --- | --- | --- | --- | --- |
| Deep Quant — main reasoning loop | `openai/gpt-4o` | `agents/deep-quant-loop/graph.py:976` (`OPENROUTER_DEFAULT_MODEL`), resolved at `:980` | `LLM_MODEL` per deployment; `state["model"]` per run, chosen in the UI composer | `https://openrouter.ai/api/v1` (`graph.py:975`) — **OpenRouter** | [CTO — unassigned] |
| Deep Quant — DEBATE bull | `LLM_MODEL`, else `gemini-2.5-flash` | `agents/deep-quant-loop/debate.py:86`, `:95` | `DEBATE_BULL_MODEL` | OpenRouter, as above | [CTO — unassigned] |
| Deep Quant — DEBATE bear | as above | `debate.py:87` | `DEBATE_BEAR_MODEL` | OpenRouter, as above | [CTO — unassigned] |
| Deep Quant — DEBATE judge | as above | `debate.py:88` | `DEBATE_JUDGE_MODEL` | OpenRouter, as above | [CTO — unassigned] |
| Market insights (`quant-rag`) | `deepseek-ai/DeepSeek-V3-0324` | `agents/quant-rag/src/llm.rs:23` | `LLM_MODEL` | `https://api.freemodel.dev/v1/chat/completions` (`llm.rs:20`) — **freemodel.dev**, an OpenAI-compatible aggregator | [CTO — unassigned] |
| In-app LLM client (Tauri) | `deepseek-ai/DeepSeek-V3-0324` | `frontend/src-tauri/src/services/llm.rs:254` | `LLM_MODEL` (runtime env → compile-time baked → hardcoded) | `llm.rs:253` — **freemodel.dev** | [CTO — unassigned] |
| Sentiment agent (Node) | `deepseek-ai/DeepSeek-V3-0324` | `agents/sentiment/src/analyzer.js:31-32`, `claude.js:23-24` | `LLM_MODEL`, `LLM_API_URL` | `analyzer.js:31` — **freemodel.dev** | [CTO — unassigned] |
| Sentiment command (Tauri) | resolved at runtime | `frontend/src-tauri/src/commands/sentiment.rs:360`, endpoint `:356` | `LLM_MODEL`, `LLM_API_URL` | `sentiment.rs:360` — **freemodel.dev** | [CTO — unassigned] |

> **Corrected 2026-08-18 — and the correction is the point of this section.** Version 1.0 of this table
> named **NVIDIA NIM** and **HuggingFace Inference Router** as the providers for the last rows. Both were
> wrong. `NVIDIA` appeared in **no functional code path** in this repository — only two stale comments
> (`quant-rag/src/engine.rs:181`, `main.rs:33`, both since corrected), a dead `connect-src` entry in the
> shipped CSP (removed), and two `remove_var` calls in a test. HuggingFace appeared only as a *documented
> alternative* in module comments — and, wrongly, as the stated default in the sentiment agent's own
> headers, which its code has never used. Every non-OpenRouter call site defaults to `api.freemodel.dev`.
>
> I introduced those two errors by taking the provider names from `docs/ARCHITECTURE.md` instead of from
> the code — the exact failure `BRAND_GUIDELINES.md` §5.1 lesson 2 describes, committed while writing the
> rule against it. **Rows in this table cite source files and are verified against them. No row may be
> sourced from another document.** The reverse dependency is now written into the source documents
> themselves: `ARCHITECTURE.md`, `COMPLETE_ANALYSIS.md`, `system_architecture_and_data_flows.md` and
> `DEEP_QUANT_ANALYSIS.md` each point here instead of naming a model, so the propagation path that caused
> this is closed at both ends.

### 2.1 What is actually deployed is not what this table says

Every row above is a **code default**. Three separate mechanisms override them, and in the live
deployment all three are in play — so the table is the floor, not a description of production.

| Override | Evidence | Effect |
| --- | --- | --- |
| Deployment `.env` | `LLM_MODEL=gpt-5.5`, `LLM_EFFORT=high` (recorded at `docs/DEEP_QUANT_ANALYSIS.md:996`) | The Tauri-side client runs **neither** DeepSeek nor gpt-4o |
| Gateway selection | `NEXT_PUBLIC_LLM_GATEWAY` defaults to **`omniroute`**, not OpenRouter (`useQuantStore.ts:250-251`); `useQuantStore.ts:169-170` requires the server's `OPENROUTER_BASE_URL` to match | The recommendation loop's gateway in a beta build is the omniroute proxy. `graph.py:979` resolves `OPENROUTER_BASE_URL` → `LLM_API_URL` → OpenRouter, so the OpenRouter row above holds only where neither is set |
| Compile-time baking | `option_env!` in `services/llm.rs:280,288,296` and `commands/sentiment.rs:356` | Two installers of the same version can carry different models |
| Per-run user selection | `state["model"]`, chosen in the UI composer — though selection is **locked** on the omniroute gateway (`MODEL_SELECTION_LOCKED`, `useQuantStore.ts:253`) | On the production gateway the end user can change the model for a single recommendation |

**Consequence for disclosure:** a published statement of the form "we use model X" is unsupportable for
this architecture no matter how carefully X is chosen. `AI_DISCLOSURE.md` §4 therefore names defaults and
states that the model is configurable, and the per-recommendation `model_id` in §3 is what actually
answers "which model produced this output" — for any *specific* output, which is the only level at which
that question has a true answer here.

**Consequence for this table:** it cannot be verified from source alone. Whoever signs the §8 quarterly
review must check it against the **deployed** environment, not the repo, and a release must record what
was baked into it. Both are §10 open items.

### 2.2 Two structural facts that must survive into the public disclosure

**Two vendors are in the default path, and both are aggregators.** OpenRouter for the recommendation
loop, `api.freemodel.dev` for insights and sentiment. Both are OpenAI-compatible *routers*, which means
the party that actually executes inference sits one hop beyond our contract and can change without any
change on our side. A vendor outage or a silent vendor-side model revision is a live risk. Under the
8 January 2025 guidelines the RA carries that risk regardless of the contract, which is why §7 requires
the vendor-change entry in the register even when we changed nothing — and why **§9 must treat "the
aggregator silently re-pointed a model identifier" as a named incident class**, not a hypothetical.
`AI_DISCLOSURE.md` §4 states the router fact plainly rather than implying we hold a direct relationship
with a model lab.

**Nothing about the user is in the request** — see `AI_DISCLOSURE.md` §3. This is what makes the vendor
chain tolerable rather than a data-protection problem, and it is a *guarded* property, not a convention:
the P8a personalisation refusal (`agents/deep-quant-loop/personalisation.py`) blocks the question before
it reaches a model. It must not be weakened to improve output quality without a decision recorded in §7.

> **Audit finding — the reason these two subsections exist.** Two separate defects, one pattern.
>
> **(a) A model identifier that exists nowhere in this codebase, "DeepSeek v4 Pro", had reached two
> user-facing surfaces:** shipped installer metadata (`tauri.conf.json:78`) and the **paid-plan paywall**
> (`PremiumPaywall.tsx:56`, "DeepSeek v4 Autonomous ReAct Agent Loop"). The recommendation model defaults
> to `openai/gpt-4o`; the real DeepSeek model — in a *different* service — is
> `deepseek-ai/DeepSeek-V3-0324`. Both surfaces corrected: `BRAND_GUIDELINES.md` §4.2 findings 1 and 3.
> It had *already been flagged as stale* at `docs/DEEP_QUANT_ANALYSIS.md:97` and nothing was done, which
> is how it reached shipped copy.
>
> **(b) Version 1.0 of the §2 table named the wrong providers** — NVIDIA NIM and HuggingFace, neither of
> which any code path uses. See the correction note under §2.
>
> Both defects have the same cause: **copy sourced from another document instead of from the code.** In
> (b) I did it while writing the rule against it, which is the strongest available argument that the rule
> needs to be mechanical rather than remembered. §2 rows cite source files; nothing in this document or
> any published page cites `ARCHITECTURE.md` for a model or vendor fact.

---

## 3. Version register — every published output is replayable

The register is not a spreadsheet someone maintains. It is computed and stored on the record itself,
because a manually maintained register drifts from production the day someone forgets.

**Implementation:** `agents/deep-quant-loop/prompt_version.py`.

| Field | Meaning | Function |
| --- | --- | --- |
| `model_id` | The model that produced this output — the run's override, else the deployment default | `model_id(override)` |
| `prompt_hash` | SHA-256 of the *composed* system prompt actually sent | `prompt_hash(text)` |
| `prompt_set_hash` | Fingerprint of the whole prompt library at run time | `prompt_set_hash()` |

The library under version control is the ordered set `DEEP_QUANT_SYSTEM_PROMPT`,
`DEEP_QUANT_FNO_PROMPT`, `RISK_MANAGER_PROMPT`, `INDEX_OPTIONS_ADDENDUM`, plus
`personalisation.QA_PROMPT_RULE` — the personalisation refusal rule is part of the published behaviour
of the Q&A surface, so a change to it is a change to the analyst.

Four properties of this design matter to an auditor, and each exists to defeat a specific failure:

1. **Per-constant digests, then a digest of digests.** Hashing the concatenation would let an edit
   that moves text from the end of one prompt to the start of the next leave the fingerprint
   unchanged. This construction does not.
2. **Line-ending normalisation** (`_normalise`). A prompt edited on Windows and one edited on Linux are
   the same prompt; a register that disagreed would be useless at exactly the moment it mattered —
   comparing what production ran against what the repository says.
3. **`<unavailable>` is a distinct value, not a blank.** A missing prompt is recorded as unavailable
   rather than hashed as an empty string, so a record can never claim a prompt produced it when the
   prompt was never captured. `prompt_version_report()` lists `missing` explicitly.
4. **Never raises.** These functions run on the compliance write path of a live decision. A hash
   failure degrades the record; it must not abort the run.

Storage: `agents/deep-quant-loop/reco_store.py`, columns `model_id`, `prompt_hash`, `prompt_set_hash`,
alongside every tool input value and the rationale. **This is what "we can replay it" means
operationally** — checkout the prompt library at that hash, request that model, feed those tool
inputs.

### 3.1 Human accountability

`analyst_of_record` (`reco_store.py:166`) is **NULL until a NISM-certified person signs off.** It is
recorded as null rather than populated with a placeholder, because a fabricated analyst name on a
research record is materially worse than an honest gap. The sign-off workflow is blocker **P8b**
(Phase 1, day 45–180) and does not exist yet. **No output may be published externally as research
while this field is null.** [COUNSEL — confirm the acceptable degree of automation in the sign-off.]

---

## 4. Pre-deployment test suite

**Gate:** no prompt, model default, or guardrail change ships without these passing. This is the
"pre-deployment test suite" the blueprint requires, and it is executable rather than descriptive.

| Suite | Command | What it proves |
| --- | --- | --- |
| Compliance suites (329 tests) | `cd agents/deep-quant-loop && .venv/Scripts/python -m pytest tests/test_interaction_log.py tests/test_reco_store_chain.py tests/test_personalisation_guardrail.py tests/test_entitlements_endpoints.py tests/test_entitlements_unit.py` | Record immutability and chain verification; personalisation refusal; entitlement fail-closed; interaction logging on every endpoint |
| Rust engine + provider (194 tests) | `cd frontend/src-tauri && cargo test --lib` | Deterministic quant layer; the provider seam; QuestDB parsing totality |
| Frontend SKU gate | `cd frontend && npx vitest run src/lib/__tests__` | RESEARCH modes refused under a TERMINAL SKU, with no IPC issued |
| Charting | `cd frontend && npx vitest run src/charting` | Datafeed and engine invariants |

Three of these are worth naming individually as **model-risk** controls rather than ordinary tests:

- **The personalisation guardrail is deterministic and pre-LLM.** `personalisation.py`
  `detect_personalisation()` runs *before* the model call in `qa_node`. A refusal therefore costs no
  tokens and does not depend on temperature, sampling, or the model behaving. Property-tested. This is
  the load-bearing control for both the RA/IA boundary and the US publisher's exclusion.
- **The record store cannot be edited.** SQLite `BEFORE UPDATE`/`BEFORE DELETE` triggers ABORT, and a
  `prev_hash`/`row_hash` chain makes any interior edit detectable. `verify_chain()` reports the first
  broken row.
- **Tail truncation is *not* detectable by the chain alone.** Documented here because pretending
  otherwise would be the more dangerous error: deleting the most recent N rows leaves a valid chain.
  Detecting that requires an external witness (a retained row count or tip hash). **[Open — the
  witness is not yet implemented. It must be before an external audit relies on the store.]**

### 4.1 Harness conditions — so a passing gate means the same thing everywhere

A gate that only passes on one laptop is not a gate. Three harness properties were fixed deliberately,
and changing any of them silently weakens this section:

- **The suite collects without an LLM credential** (`71534be`). `graph.py` builds its client at module
  scope, so on a machine with no key every test module that imports it used to fail at *collection*
  with `Missing credentials` — clean CI, or any new contributor. `tests/conftest.py` exports a
  deliberately invalid `LLM_API_KEY` at module scope, which also pins the credential **mode** to
  shared-key so CI exercises the same branch a developer with a `.env` does. If a test ever does reach
  the network it 401s loudly and names itself, rather than quietly spending someone's quota.
- **The compliance store is redirected per test.** An autouse fixture points `COMPLIANCE_DB_PATH` at a
  throwaway file. This is not tidiness: the property tests drive `_finalize_decision` with hundreds of
  synthetic decisions, and those rows must never land in an append-only artefact whose entire purpose
  is that rows cannot be removed from it.
- **One property test runs without a Hypothesis deadline** (`5333934`) — the finalize/journal parity
  test does real SQLite work per example, so a per-example time limit made it fail on timing rather
  than on behaviour.

**Verified 19 August 2026:** the five compliance suites report **329 passed**. There is a separate,
pre-existing family of failures elsewhere in this service's test tree (Hypothesis feeding NUL bytes
into unrelated `st.text()` strategies); it does not touch these five suites, and it must not be allowed
to become the reason someone stops running them.

---

## 5. How existing architecture maps to the draft principles

The blueprint calls this "your single biggest unearned advantage". Concretely:

| Draft principle | Control | Where |
| --- | --- | --- |
| **Transparency — explainability** | Every tool call, argument and returned number streamed to the user live during the run. The reasoning is observable while it happens, not reconstructed afterwards | `deep-quant-stream` IPC events; `agents/deep-quant-loop/main.py` SSE |
| **Transparency — auditability** | Immutable hash-chained recommendation record with all tool inputs, model id and prompt hashes | `reco_store.py` |
| **Accountability** | Single commit chokepoint for every decision; per-client interaction log; analyst-of-record field | `graph.py:3835` `_finalize_decision`; `interaction_log.py`; §3.1 |
| **Safety & Reliability — honest failure** | Missing data is reported as unavailable. No synthetic values, no defaulting a missing metric to neutral | Product-wide rule; `frontend/src/components/fno/viewModel.ts` is the reference implementation (`unavailable` vs `service-error` are distinct states) |
| **Safety & Reliability — bounded execution** | Watch_Cap and Session_Budget bound the hunt; on exhaustion the system commits a terminal stand-aside on the model's behalf | `graph.py` `force_terminal` (`:4755`), routed at `:4656` |
| **Safety & Reliability — hard risk rules** | The Trade_Validator rejects any directional output with stop < 1.5× ATR(14) or below the profile's R:R floor, regardless of model confidence | Validator invoked from `declare_trade` |
| **Equality** | Impersonal by design: identical analysis for the same symbol and timeframe for every subscriber in a tier. No per-client tailoring exists to be unequal | `personalisation.py`; the RA/IA boundary requires this anyway |
| **Model monitoring** | Realised expectancy and win rate per setup type, used to calibrate conviction downward on historically losing setups | `journal.py` — **internal only**, see §6 |

---

## 6. What stays internal, and why that is a compliance decision

`journal.py` computes realised win rate and expectancy per setup type, and the agent consults it to
size conviction down when a comparable setup has historically lost money.

**This is model monitoring, and the AI framework actively wants it. It is also a performance
representation, which advertising rules restrict.** Both are true, so the resolution is by audience:

- **Retained internally** as calibration and drift evidence. Never removed — removing it would delete
  the drift signal §7 depends on.
- **Never surfaced to a user or in marketing.** Blocker **P6** removed Total Return, Win Rate, Max
  Drawdown and Avg Conviction from the only user-facing surface that carried them
  (`frontend/src/hooks/useMacroIndicators.ts`), replacing them with discipline statistics.

Anyone proposing to render a journal statistic in the UI is proposing a regulatory change, not a
feature. It goes through the Compliance Officer.

---

## 7. Drift monitoring and change control

### 7.1 Weekly — drift review

Owner: [MODEL RISK OWNER]. Reviewed from `journal.py` statistics per setup type and profile:

| Signal | Watch for | Action on trip |
| --- | --- | --- |
| Realised expectancy per setup type | A previously positive setup turning negative over a non-trivial sample | Investigate before the model down-sizes conviction on its own; a regime change and a model regression look identical in this number and must be distinguished |
| Validator rejection rate | A rise, indicating the model is proposing structurally worse brackets | Compare `prompt_set_hash` and `model_id` against the last known-good week |
| Forced-HOLD / `force_terminal` rate | A rise, indicating the model is failing to converge within budget | As above |
| Refusal-category distribution (`interaction_log`) | A shift in personalisation refusals | Check whether phrasing is evading the detector — a *detector* gap, not a model gap |
| `prompt_version_report().missing` | Any non-empty value in production | Records are being written that cannot claim a prompt. Treat as a P1 defect |

**`low_sample` is not a drift signal.** Reading a handful of trades as drift is the failure mode this
row exists to prevent.

### 7.2 Change register

Every entry appended. **Never edited or deleted** — the register's value is that it is complete.

| Date | Change | Old → new `prompt_set_hash` / `model_id` | Reason | Tests run | Approved by |
| --- | --- | --- | --- | --- | --- |
| 2026-08-17 | Added `personalisation.QA_PROMPT_RULE` to the prompt library; deterministic pre-LLM refusal in `qa_node` (P8a) | — → [record at first deploy] | RA/IA boundary; US publisher's exclusion | Compliance suites | [COMPLIANCE OFFICER] |

A change requires an entry when **any** of these changes: a prompt constant; the default or per-role
model; the personalisation detector; the Trade_Validator's rules; the Watch_Cap or Session_Budget.

**A vendor-side model revision requires an entry even though we changed nothing.** `openai/gpt-4o`
today and `openai/gpt-4o` in six months are not necessarily the same weights. The register records
what we observed, not only what we edited.

### 7.3 Human-override log

**Status: not yet implemented.** There is no mechanism today by which a human alters a model's output
before it reaches a client — the RESEARCH SKU is not sold, and `analyst_of_record` is null, so there
is nothing to override. The blueprint requires the log, so its shape is fixed now:

| Field | Meaning |
| --- | --- |
| `recommendation_id` | The record being overridden — the chain row, so the original stays intact |
| `overridden_by` | NISM-certified analyst identity |
| `field`, `original`, `replacement` | What changed |
| `reason` | Free text, mandatory |
| `at` | Timestamp |

**Requirement: an override never mutates the original record.** It appends a linked row. The immutable
store is what makes this possible and the ABORT triggers are what make it enforced — an override
implemented as an UPDATE would fail loudly rather than silently rewriting history. Build this
alongside P8b.

---

## 8. Review cadence

| Frequency | Activity | Owner |
| --- | --- | --- |
| **Weekly** | Drift review (§7.1) | [MODEL RISK OWNER] |
| **On every change** | Register entry + pre-deployment suite (§4, §7.2) | Change author + [COMPLIANCE OFFICER] |
| **Quarterly** | Full AI governance review: inventory accuracy, vendor changes, register completeness, disclosure page accuracy, open items in this document | [COMPLIANCE OFFICER] |
| **On regulatory change** | Re-read against the final AI/ML circular when issued; update §1 and §5 | [COMPLIANCE OFFICER] + [COUNSEL] |

---

## 9. Incident procedure

An **AI incident** is any of: a model producing output that reaches a client and is materially wrong
in a way the validator did not catch; a personalisation refusal that failed to fire; a recommendation
written with an unavailable prompt hash; a vendor model change discovered after the fact; or a
`verify_chain()` failure.

1. **Preserve.** Do not repair the record. `verify_chain()` output, the affected `thread_id`s and the
   `interaction_log` rows are the evidence. A "fix" destroys it, and the ABORT triggers will refuse
   the attempt anyway.
2. **Contain.** Roll back the prompt library or pin the previous model, whichever the register
   identifies as the change. Pinning is faster than debugging.
3. **Assess client impact** from `interaction_log` — precisely who was told what, and when. This is
   the question the store exists to answer.
4. **Record** in §7.2 with the incident reference.
5. **Escalate.** Compliance Officer within 24 hours of detection.
6. **Report externally** if the incident is also a cyber incident (credential or data exposure) — see
   `docs/compliance/SECRET_ROTATION_RUNBOOK.md` §5 for CSCRF timelines. **[COUNSEL — confirm whether
   a pure model-quality incident carries any external reporting duty. It is assumed here that it does
   not, and that assumption is untested.]**

---

## 10. Open items

Tracked here rather than omitted, so the gaps are on the record.

| # | Item | Blocks |
| --- | --- | --- |
| 1 | **External witness for the record store.** Tail truncation is undetectable by the chain alone (§4) | An external audit relying on the store |
| 2 | **`analyst_of_record` is null** — no certified sign-off workflow exists (P8b) | Publishing anything as research |
| 3 | **Human-override log not built** (§7.3) | Nothing today; required before overrides are possible |
| 4 | **`/internal/entitlement/{user_id}` does not exist** in the remote auth deployment. The client is written fail-closed, so `SKU_ENFORCE=1` currently denies all RESEARCH traffic | Selling the RESEARCH SKU |
| 5 | **Measured accuracy is not published.** The AI disclosure page (§0 of `AI_DISCLOSURE.md`) states limitations but no accuracy figure, because publishing one is a performance representation. **[COUNSEL — the draft framework asks for accuracy disclosure while the advertisement code restricts performance claims. This tension needs a written opinion.]** | Full alignment with the draft framework |
| 6 | **AI/ML system filing** under the 2019 circulars not made | Confirm applicability pre-registration [COUNSEL] |
| 7 | **§2 cannot be verified from source alone.** Deployment `.env`, gateway selection and compile-time `option_env!` baking all override the code defaults (§2.1) — the live `.env` already runs a model no row names. The §8 quarterly review must be signed against the **deployed** environment | An accurate answer to "which model is in production" |
| 8 | **Releases do not record what was baked into them.** Two installers of the same version can carry different models and endpoints (§2.1). Until a release records its resolved values, "which model produced this output" is unanswerable for a given build | Per-build auditability; §3 replayability for desktop-side outputs |
| 9 | **The public disclosure page names a provider the desktop build does not use.** `NEXT_PUBLIC_LLM_GATEWAY` defaults to an internal proxy, so for some builds the party receiving the request is not the one §4 of `AI_DISCLOSURE.md` names. Either the default changes or the wording does — it cannot be left to whichever is noticed first | Publishing `AI_DISCLOSURE.md` §4; also `BRAND_GUIDELINES.md` §4.5 item 20 |
| 10 | **Website copy is drafted but unapproved.** `docs/compliance/WEBSITE_COPY.md` carries per-claim substantiation, but under `BRAND_GUIDELINES.md` §5.1 lesson 3 the writer cannot be the approver, and no approver is appointed (the Compliance Officer role is itself unfilled) | Publishing the landing page; the P10 advertisement register has nothing to record an approval into |
