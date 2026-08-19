# How Strat AI Uses AI

**Public disclosure · Version 1.0 · August 2026**

> This is the plain-language source copy for the public AI disclosure page required by blocker **P11**
> (`docs/business/PLAN_OF_ACTION.md` §4.2, `docs/business/SEBI_COMPLIANCE_BLUEPRINT.md` §4.2). It is
> published **voluntarily**, ahead of SEBI's draft AI/ML guidelines being finalised.
>
> **Editing rules for this file.** Every sentence here is a statement to clients and, in effect, to the
> regulator. Three constraints bind: no claim about returns or accuracy (see
> `docs/compliance/BRAND_GUIDELINES.md`); every factual claim must be true of the shipped build, not of
> the roadmap; and the model inventory must match `docs/compliance/AI_MODEL_GOVERNANCE.md` §2.
> **[COUNSEL — review before first publication.]**
>
> **Not publishable yet.** §8 lists what must be filled in first. Publishing with placeholders
> unresolved would itself be a disclosure defect.

---

## What this page is

We use large language models to help analyse trade setups. Since that is the part of the product most
easily oversold, here is exactly what the AI does, what it is given, and where it fails. We would
rather you read this than infer it.

If anything below turns out not to match the product, that is a defect — please report it to
[COMPLIANCE OFFICER CONTACT].

---

## 1. What the AI does

**It evaluates one instrument at a time and argues about whether a trade is worth taking.**

Concretely, when you run an analysis the model:

1. Calls quantitative tools to fetch real market data — candles, indicators, volume profile, option
   chain, order-flow proxies, recent news.
2. Reads what came back, including where data is missing.
3. Reasons in the open. Every tool call, every argument it passed, and every number returned is
   streamed to your screen while it happens.
4. Either proposes a bracket — direction, entry, stop loss, take profit, holding period — or declines.
5. Argues against itself. A separate bear pass exists specifically to attack the proposal, and its
   objections are part of the output you receive rather than something discarded on the way.

**The output you receive is research: an opinion about an instrument, with reasoning attached.**

---

## 2. What the AI is *not* allowed to do

These are structural limits, not policies we promise to follow.

**It cannot place, modify or cancel an order.** No capability to reach a broker's order system exists
anywhere in the product. This is enforced in the type system: the internal broker interface has no
order method, so it cannot be called by any prompt or any future model. Paper trading is entirely
local and never contacts a broker.

**It cannot give you personal advice.** It will not tailor an answer to your capital, income, net
worth, goals, existing holdings, or position size. If you ask "how much of my ₹5 lakh should I put in
this", it declines and redirects to impersonal analysis of the instrument. This refusal is a
deterministic rule that runs **before** the model is called — it does not depend on the model choosing
to comply.

Two consequences worth being explicit about, because they are the point rather than a side effect:

- **The analysis is the same for everyone.** Two subscribers on the same plan asking about the same
  instrument at the same time get the same analysis. It is not personalised, and it is not suitable
  advice for your situation. **Whether a trade is appropriate for you is a judgement we do not make and
  cannot make** — we do not know anything about your circumstances, by design.
- **It will not tell you your position size.** That is a function of your capital and risk tolerance,
  which is exactly the information the product refuses to consider.

**It cannot override the hard risk rules.** A proposed trade whose stop is closer than 1.5× the
instrument's 14-period ATR, or whose reward-to-risk ratio falls below the floor for that profile, is
rejected by a deterministic validator no matter how confident the model is. The model does not get a
vote on this.

---

## 3. What it is given as input

| Input | Source |
| --- | --- |
| Price and volume history | Zerodha Kite Connect (NSE / NFO), stored locally |
| Live ticks | Zerodha Kite Connect websocket |
| Computed indicators | Our own deterministic code — RSI, MACD, EMA, ATR, ADX, volume profile, VWAP and similar. Not AI-generated |
| Option chain data | Zerodha Kite Connect quotes; open interest, implied volatility, PCR, max pain, OI walls |
| Recent news headlines | Third-party news providers |
| Its own track record | An internal record of how comparable past setups actually resolved, used to reduce confidence on setups that have historically lost money |

**What it is *not* given:** anything about you. Not your capital, your holdings, your other positions,
your income, your age, your goals, or your trading history. That information is not passed to the
model, so it cannot influence the output even inadvertently.

---

## 4. Which models we use

The model is **configurable**, so we name the default rather than claiming a single fixed one. The
model that produced any given output is recorded on that output's record, so it can be identified
after the fact.

| Purpose | Default model | Reached through |
| --- | --- | --- |
| Main analysis | `openai/gpt-4o` | OpenRouter |
| Bull / bear / judge debate roles | Same as main analysis unless separately configured; otherwise `gemini-2.5-flash` | OpenRouter |
| Market-anomaly commentary | `deepseek-ai/DeepSeek-V3-0324` | freemodel.dev |
| News sentiment scoring | `deepseek-ai/DeepSeek-V3-0324` | freemodel.dev |

**Both providers named above are *routers*, not the labs that built the models.** They accept our
request and forward it to whoever actually runs the model. We name them because they are the parties our
requests are sent to — but the inference itself happens one step further away, at a party the router
selects. If that matters to you, the practical answer is §3: nothing about you is in the request.

> **⛔ Do not publish this section yet.** One gateway question is open: the desktop build's default
> LLM gateway is an internal proxy ("omniroute"), not OpenRouter, so for some builds the party receiving
> the request is not the one named above. Settle it before this page goes live — `BRAND_GUIDELINES.md`
> §4.5 item 20 and `AI_MODEL_GOVERNANCE.md` §2.1. Publishing a provider we cannot stand behind is the
> exact defect this page was already corrected for once.

**We did not train these models.** They are general-purpose models from third parties. What is ours is
the tooling, the prompts, the deterministic quant engines, the risk validation and the guardrails —
which is where the product's behaviour actually comes from.

We are responsible for the output regardless of whose model produced it. We do not treat a vendor as a
place to send responsibility. That includes this table being right: an earlier draft of this page named
two providers we do not use, because it was written from an internal architecture document instead of
from the running code. It was corrected on 18 August 2026, before publication, and the rule that
produced the error is now closed — every row here is verified against source, and against the internal
inventory at `docs/compliance/AI_MODEL_GOVERNANCE.md` §2, which is the only place a model or provider
name may come from.

---

## 5. Limitations — read this part

**The output is probabilistic. It is not a prediction, and it is not a guarantee of anything.** The
same instrument analysed twice may produce different reasoning, because these models are not
deterministic. The deterministic parts — indicators, validation, option analytics — are identical every
time; the language reasoning over them is not.

Specific known limitations:

| Limitation | What it means for you |
| --- | --- |
| **Language models can be confidently wrong** | Fluent, well-structured reasoning is not evidence of correctness. The confidence of the writing carries no information about the accuracy of the conclusion |
| **Only what it was given** | It sees market data and headlines. It does not see your portfolio, an unpublished corporate action, a policy announcement made mid-run, or anything else outside its tool results |
| **Historical patterns need not repeat** | Every input is a description of the past. Nothing here forecasts the future |
| **News interpretation is shallow** | Headlines are read as text. A sarcastic, hedged or later-corrected headline can be misread |
| **Missing data is common** | Option chains, order-flow proxies and news are often unavailable. We mark these unavailable rather than substituting a neutral value — so an analysis may be built on less than the full picture, and it will say so |
| **It is bounded, and may stop without concluding** | Analysis runs under a fixed budget. On exhaustion the system stands aside rather than forcing a conclusion. A "no decision" outcome is a real outcome, not an error |
| **It does not know your risk tolerance** | By design (§2). A setup it rates highly may be entirely unsuitable for you |

---

## 6. When it says nothing

The product is built to decline. Where it lacks the data to support a view, or the data conflicts, or
the risk rules cannot be satisfied, the output is a stand-aside rather than a lower-confidence trade
idea.

**We consider this the most valuable thing it does**, and we mention it here because a system that
never refuses is not being cautious on your behalf.

We also do not fill gaps. If open interest is unavailable, it is reported unavailable — not defaulted
to zero, and not described as neutral. A missing number and a neutral number mean very different
things, and a system that conflates them is lying to you quietly.

---

## 7. Transparency and records

**You can watch it work.** The reasoning is streamed live — tool calls, arguments, returned values,
and the argument between the bull and bear passes. Nothing is summarised after the fact from a hidden
process.

**We keep records.** Every recommendation is stored with its timestamp, every input value it used, the
model identifier, and a cryptographic hash of the exact prompt version that produced it. Records are
append-only and tamper-evident: they cannot be edited or deleted, and any alteration to an existing
record is detectable. Your interactions — questions asked and answers given — are retained on the same
basis.

We keep these records because we are required to, and because it means the question "why did it say
that, on that date, on that data?" has an answer we can produce rather than reconstruct.

**Retention:** [RETENTION PERIOD — 5 years from the relevant date under SEBI record-keeping rules;
confirm the exact computation]. Note that this retention obligation can override a request to delete
your data. **[COUNSEL — the DPDP Act carve-out wording must be settled before publication.]**

---

## 8. Before this page can be published

| # | Placeholder | Needed |
| --- | --- | --- |
| 1 | Entity name, registration number, Compliance Officer contact | The INH registration |
| 2 | The standard SEBI disclaimer and MITC link | [COUNSEL] |
| 3 | Retention wording in §7 | [COUNSEL] — DPDP vs SEBI retention |
| 4 | Whether any accuracy figure is disclosed at all | [COUNSEL] — the draft AI framework asks for accuracy disclosure; the advertisement code restricts performance claims. Unresolved, tracked as open item 5 in `AI_MODEL_GOVERNANCE.md` §10 |
| 5 | Confirmation that §2's structural claims survive the final build | Re-verify against `providers::BrokerProvider` and `personalisation.py` at release |

**Until item 4 is resolved this page states limitations without an accuracy figure.** That is a
deliberate choice, not an omission: publishing a hit rate is precisely the headline-performance claim
that `docs/compliance/BRAND_GUIDELINES.md` prohibits, and the safer error is to under-claim.

---

*Strat AI is a research and analysis product. It does not execute trades, it does not manage money, and
nothing it produces is personal advice or a recommendation that any transaction is suitable for you.
Trading in securities and derivatives carries risk of loss, including loss exceeding your initial
capital in leveraged instruments.*
