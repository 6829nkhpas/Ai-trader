"""Deterministic personalisation refusal guardrail for Trade_QA_Mode (P8a).

WHY THIS IS CODE AND NOT A PROMPT INSTRUCTION
---------------------------------------------
Two separate regimes make "does this answer consider the user's own finances?"
a licence boundary rather than a style preference:

  * **India.** A SEBI Research Analyst may publish impersonal research. Tailoring
    a recommendation to an individual's capital, income, holdings, goals or risk
    profile is *investment advice* and requires Investment Adviser registration
    instead. `docs/business/PLAN_OF_ACTION.md` §4.2 lists this guardrail as
    blocker **P8a**: "prevents drift from RA (research) into IA (advice)
    territory".
  * **United States.** The publisher's exclusion recognised in *Lowe v. SEC*,
    472 U.S. 181 (1985) only covers publications that are **strictly
    impersonal**. `PLAN_OF_ACTION.md` §8.1 names this guardrail as "the
    load-bearing control here".

A rule inside a system prompt is a request, not a control: it is subject to
temperature, model swaps, prompt-injection in the user's own question, and
context truncation. So the refusal is decided **before** the model is invoked, by
the pure function below. `build_qa_system_prompt` also carries the rule as
defence in depth, but that layer is not what is being relied on.

DESIGN
------
* **Pure and total.** No I/O, no clock, no randomness, no network. Same input →
  same output, forever, so a refusal is reproducible years later during an
  inspection. Never raises: unusable input returns ``None``.
* **Legible over clever.** The patterns are an explicit per-category table rather
  than a compressed grammar, because this table is the artefact counsel and an
  inspector read. Adding a phrasing should be a one-line diff.
* **Biased toward refusing.** A false positive costs one refused question the
  user can rephrase impersonally. A false negative is personalised advice
  emitted by an unregistered entity. The two are not symmetric, so the detector
  leans toward the hit — but ``_MUST_NOT_MATCH`` in the test suite pins a corpus
  of legitimate impersonal questions that must keep working.

Deliberately NOT detected — with reasons, because these look like gaps:

* Bare ``my trade`` / ``my position``. In Q&A the user naturally calls *this
  session's declared trade* "my trade" ("why is my trade a HOLD?"). That is a
  question about the research output, not about their book. The verb forms that
  do imply a real holding (``should I add to my position``, ``should I book my
  profits``) are matched below instead.
* ``should I buy?`` / ``is this a good entry?``. A directional call on a security
  is exactly what impersonal research *is*; refusing it would delete the product
  rather than repackage it.
* ``is NIFTY suitable for intraday?``. Suitability of an *instrument* is a market
  fact. Only suitability *for the user* is the regulated question.

[COUNSEL] The refusal copy in ``REFUSAL_TEXT`` is user-facing and names the
RA/IA boundary. `PLAN_OF_ACTION.md` §11 requires sign-off on wording of this
kind before it ships. The text below is deliberately factual, makes no
representation about registration status, and directs the user to a registered
investment adviser for the decisions it declines.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple

# ── Categories ────────────────────────────────────────────────────────────────
# Declared order IS the match order. A question can plausibly hit more than one
# category ("how much of my ₹5 lakh should I put in this" is both sizing and a
# capital disclosure); the first category in this tuple wins so the recorded
# category is deterministic. Sizing leads because it is the sharpest
# advice-shaped formulation and the most useful label in an audit log.
PERSONALISATION_CATEGORIES: Tuple[str, ...] = (
    "position_sizing",
    "holdings",
    "capital",
    "income",
    "net_worth",
    "goals",
    "third_party",
    "suitability",
)


@dataclass(frozen=True)
class PersonalisationHit:
    """One detected request for personalised advice.

    ``category`` is a stable machine-readable id from
    ``PERSONALISATION_CATEGORIES`` — safe to persist in the interaction log and
    aggregate on. ``matched`` is the exact substring that tripped the detector,
    kept so a refusal can be justified after the fact without re-deriving it.
    """

    category: str
    matched: str


# ── Pattern table ─────────────────────────────────────────────────────────────
# Every pattern is applied to the NORMALISED question (see ``_normalise``):
# NFKC-folded, lower-cased, straight apostrophes, single-spaced. Patterns are
# therefore written lower-case with no alternation for curly quotes.
#
# Bounded gaps (``.{0,40}``) rather than ``.*`` keep matching linear-ish and stop
# a long question from bridging two unrelated clauses into a false hit.

_MONEY = r"(?:₹|rs\.?|inr|\$|usd)"
_MAGNITUDE = r"(?:lakh|lakhs|lac|lacs|crore|crores|cr|k|thousand|million)"

# Possessive, plus an optional run of adjectives before the noun. Without the
# adjective slot, "my ENTIRE capital" and "my LOW risk tolerance" walk straight
# past a `my\s+capital` pattern — a one-word bypass, and the first thing a user
# naturally writes. The adjective list is bounded and financial/quantitative, so
# it widens the match without reaching unrelated phrasings.
_MY = r"(?:my|our)"
_ADJ = (
    r"(?:\s+(?:entire|whole|full|total|complete|all|remaining|available|own|only|"
    r"limited|small|little|tiny|modest|big|large|huge|low|high|current|existing|"
    r"overall|personal|monthly|yearly|annual|net|gross|spare|idle|extra|free|"
    r"initial|hard[-\s]?earned|life|retirement|family|joint|liquid|invested|"
    r"ideal|optimal|right|correct|proper|max|maximum|min|minimum|safe|typical|"
    r"usual|average|planned|preferred|recommended))*"
)
_MYADJ = _MY + _ADJ

# Relations a question can be asked on behalf of. Advice for a third party is not
# impersonal by definition, whoever the person is.
_RELATION = (
    r"(?:father|mother|dad|mom|mum|papa|parents?|wife|husband|spouse|partner|"
    r"brother|sister|sibling|friend|uncle|aunt|cousin|in[-\s]?laws?|grandfather|"
    r"grandmother|boss|colleague|client|neighbour|neighbor|family\s+member)"
)

_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "position_sizing",
        (
            # "how many shares should I buy", "how much should I invest in this"
            r"\bhow\s+(?:many|much)\b.{0,40}?\b(?:should|shall|can|could|must|do|would)\s+i\s+"
            r"(?:buy|sell|invest|put|allocate|risk|trade|deploy|enter|take|add|commit)\b",
            # "how much of my capital", "how many of my shares"
            rf"\bhow\s+(?:much|many)\s+(?:of\s+)?{_MY}\b",
            # "how many shares to buy" — the same request with the "I" dropped.
            # A buy/sell verb is required so market facts ("how many contracts
            # traded today", "how many lots sit at the OI wall") stay answerable.
            r"\bhow\s+many\s+(?:shares?|lots?|units?|contracts?|qty|quantity)\b.{0,25}?"
            r"\b(?:should|shall|can|could|must|do|would|to)\s+(?:i\s+)?"
            r"(?:buy|sell|invest|trade|take|enter|allocate|deploy|purchase|hold)\b",
            # "how many lots for a 10 lakh account" — sizing against a stated
            # account size, with no verb and no "I" anywhere in the sentence.
            rf"\b(?:how\s+many|what)\s+(?:shares?|lots?|units?|contracts?|qty|quantity|size)\b"
            rf".{{0,20}}?\bfor\s+(?:a|an|my|our)?\s*(?:{_MONEY}\s*)?[\d,.]+\s*{_MAGNITUDE}?\s*"
            rf"(?:account|capital|corpus|portfolio|budget|balance)\b",
            # "what quantity should I take", "what size should I trade"
            r"\bwhat\s+(?:quantity|qty|size|lot\s+size|position\s+size)\s+(?:should|do|can|must)\s+i\b",
            # "position size for me", "how much margin do I need"
            r"\b(?:position\s+siz(?:e|ing)|lot\s+size|quantity|qty|number\s+of\s+shares)\b"
            r".{0,30}?\bfor\s+(?:me|my|us|our)\b",
            # "what is my ideal position size" — the same request as a possessive.
            # Only sizing nouns, so "my exposure"/"my allocation" stay with
            # `holdings` where they describe something already held.
            rf"\b{_MYADJ}\s+(?:position\s+siz(?:e|ing)|lot\s+siz(?:e|ing)|trade\s+size|"
            rf"order\s+size|quantity|qty)\b",
            r"\bhow\s+much\s+(?:capital|money|margin|cash|funds?)\s+(?:do|should|would|will)\s+i\s+"
            r"(?:need|require|use|put)\b",
            # "how much to invest/risk/allocate" — an allocation decision even
            # without an explicit "I"; there is no impersonal reading of it.
            r"\bhow\s+much\s+(?:should\s+)?to\s+(?:invest|risk|allocate|put|deploy|buy)\b",
            # "what percentage of my portfolio", "what % of my capital"
            rf"\bwhat\s+(?:percent(?:age)?|%|share|fraction|part)\s+of\s+{_MY}\b",
            r"\bshould\s+i\s+go\s+all\s*-?\s*in\b",
            rf"\bshould\s+i\s+(?:put|invest|deploy|risk|commit)\s+{_MYADJ}\s+"
            rf"(?:capital|funds?|money|corpus|savings?|cash|balance)\b",
            rf"\bshould\s+i\s+(?:double|increase|reduce|cut|trim)\s+{_MYADJ}\s+"
            rf"(?:size|exposure|position|quantity|qty|allocation)\b",
        ),
    ),
    (
        "holdings",
        (
            rf"\b{_MYADJ}\s+(?:portfolio|holdings?|basket|allocation|exposure|book)\b",
            rf"\b{_MYADJ}\s+(?:stocks?|shares?|investments?|equities|mutual\s+funds?|"
            rf"nps|ppf|epf|fds?|fixed\s+deposits?|bonds?)\b",
            # "my trade"/"my position" alone is this session's declared trade, not
            # a holding — see the module docstring. A qualifier makes it a holding.
            rf"\b{_MY}\s+(?:existing|current|open|live|running|old|previous)\s+"
            rf"(?:trade|trades|position|positions|exposure|lots?|bet|bets|holding)\b",
            r"\bi\s+(?:already\s+)?(?:own|hold|bought|purchased|sold|shorted)\b",
            r"\bi'?\s?(?:ve|have)\s+(?:already\s+)?(?:bought|sold|shorted|entered|taken|got\s+a\s+position)\b",
            r"\bi'?\s?(?:m|am)\s+(?:already\s+)?(?:long|short)\b",
            r"\bi\s+am\s+(?:already\s+)?(?:holding|carrying|sitting\s+on)\b",
            # "my average price", "my buy price" — these presuppose a completed
            # purchase. "my entry price" is deliberately absent: in Q&A that
            # almost always means THIS session's declared entry, and refusing
            # "why is my entry at 24,500?" would break the core product.
            rf"\b{_MY}\s+(?:average|avg|buying|buy|purchase|cost)\s+(?:price|cost)\b",
            rf"\b{_MY}\s+(?:average|avg)\s+(?:is|was)\b",
            rf"\b{_MYADJ}\s+(?:p\s*&\s*l|p\s*and\s*l|pnl|losses|gains|returns|drawdown|"
            rf"unrealised|unrealized|realised|realized)\b",
            # Verb forms that presuppose a real holding.
            rf"\bshould\s+i\s+(?:book|exit|square\s+off|average\s+(?:down|up)|add\s+to|"
            rf"hold\s+on\s+to|get\s+out\s+of|dump|offload|sell)\s+(?:{_MY}|out\s+of\s+{_MY})\b",
        ),
    ),
    (
        "capital",
        (
            rf"\b{_MYADJ}\s+(?:capital|funds?|money|corpus|budget|cash|balance|"
            rf"investable|investible|liquidity)\b",
            rf"\b{_MY}\s+(?:trading|demat|broker(?:age)?|margin)\s+account\b",
            # "my ₹5 lakh", "my 2 lakhs", "my 50k". Digits are mandatory after the
            # currency token so "my rsi" cannot match the "rs" alternative.
            rf"\b{_MYADJ}\s+{_MONEY}\s*[\d,.]+",
            rf"\b{_MYADJ}\s+[\d,.]+\s*{_MAGNITUDE}\b",
            # "I have ₹50,000", "I have about 2 lakh", "I have 50000 rupees".
            # A currency token or a magnitude word is mandatory — a bare number
            # would make "I have 3 questions about the entry" a refusal.
            rf"\bi\s+(?:only\s+)?have\s+(?:got\s+)?(?:about|around|roughly|nearly|only|just)?\s*"
            rf"(?:{_MONEY}\s*[\d,.]+|[\d,.]+\s*(?:{_MAGNITUDE}|rupees?|bucks)\b)",
            rf"\bwith\s+(?:{_MONEY}\s*)?[\d,.]+\s*{_MAGNITUDE}?\s*(?:in\s+)?"
            rf"(?:capital|funds?|cash|to\s+invest|to\s+trade|in\s+hand|in\s+my\s+account)\b",
            # An amount is required here too: "I want to invest in this" is a
            # request for impersonal research, not a disclosure of means.
            rf"\bi\s+(?:can|could|want\s+to|wish\s+to|plan\s+to|intend\s+to|am\s+able\s+to)\s+"
            rf"(?:only\s+)?(?:invest|deploy|risk|spend|put\s+in|allocate)\s+"
            rf"(?:about|around|roughly|only|up\s+to|at\s+most|max(?:imum)?|a\s+max(?:imum)?\s+of)?\s*"
            rf"(?:{_MONEY}\s*)?[\d,.]+",
            r"\bi\s+(?:only\s+)?have\s+(?:a\s+)?(?:small|limited|little|tiny|big|large)\s+"
            r"(?:capital|amount|account|budget|corpus|sum)\b",
            r"\bcan\s+i\s+afford\b",
        ),
    ),
    (
        "income",
        (
            rf"\b{_MYADJ}\s+(?:salary|income|earnings|pay|wages?|take[-\s]?home|"
            rf"cash\s*flow|business\s+income)\b",
            rf"\b{_MY}\s+monthly\s+(?:income|inflow|surplus|savings?)\b",
            r"\bi\s+earn\b",
            rf"\bi\s+make\s+(?:{_MONEY}\s*)?[\d,.]+",
            r"\bi\s+am\s+(?:salaried|self[-\s]?employed|unemployed|between\s+jobs)\b",
            r"\bi'?m\s+(?:salaried|self[-\s]?employed|unemployed|between\s+jobs)\b",
        ),
    ),
    (
        "net_worth",
        (
            rf"\b{_MYADJ}\s+(?:net\s*worth|assets|wealth|savings?|nest\s+egg|"
            rf"total\s+investments?)\b",
            rf"\b{_MY}\s+financial\s+(?:position|situation|health|standing)\b",
            r"\bi\s+am\s+worth\b",
        ),
    ),
    (
        "goals",
        (
            rf"\b{_MYADJ}\s+(?:goals?|objectives?|target\s+corpus|retirement|"
            rf"time\s*horizon|investment\s+horizon|sip|emergency\s+fund|"
            rf"down\s*payment|loan|emi|mortgage|tax|taxes)\b",
            rf"\b{_MY}\s+financial\s+(?:plan|goals?|future)\b",
            r"\bi\s+(?:want|need|plan|hope|wish)\s+to\s+"
            r"(?:retire|buy\s+a\s+(?:house|home|flat|car|bike)|fund\s+my|save\s+(?:for|up)|"
            r"pay\s+(?:off|for)|build\s+a\s+corpus)\b",
            # Personal purposes. Plurals and possessive-without-apostrophe forms
            # are covered ("for my daughters wedding") because that is how the
            # phrase is actually typed.
            rf"\bfor\s+{_MY}\s+(?:retirement|child(?:ren|s)?|kids?|daughters?|sons?|"
            rf"wedding|marriage|education|house|home|family|old\s+age)\b",
            r"\bi\s+(?:will|may|might)\s+need\s+(?:this|the|that)\s+money\b",
            r"\bby\s+the\s+time\s+i\s+(?:retire|turn)\b",
        ),
    ),
    (
        "third_party",
        (
            # Asked on behalf of another person. Placed after `goals` so
            # "for my child's education" is still recorded as a goal; either way
            # the refusal is identical.
            rf"\bfor\s+(?:a|an|{_MY})\s+{_RELATION}\b",
            rf"\bfor\s+(?:him|her|them|his|hers|their)\b",
            rf"\b{_MYADJ}\s+{_RELATION}\b",
            rf"\b(?:on|in)\s+behalf\s+of\b",
            rf"\basking\s+(?:this\s+)?for\s+(?:a|an|someone|somebody)\b",
            # Another person's circumstances. A bare pronoun is NOT enough: "they
            # are selling into resistance" and "he is testing 24,500" are ordinary
            # market talk about counterparties or an instrument. Only a predicate
            # describing a PERSON's finances or status counts, which is why each
            # alternative below carries its own object.
            rf"\b(?:he|she|they)\s+(?:is|are|was|were)\s+(?:a\s+|an\s+)?(?:retired|retiring|"
            rf"salaried|self[-\s]?employed|unemployed|pensioner|housewife|homemaker|student|"
            rf"beginner|novice|conservative|aggressive|risk[-\s]averse|elderly|"
            rf"[\d,.]+\s*(?:years?|yrs?|yo)\b)",
            # A magnitude or currency token is mandatory, so "they have 5000
            # contracts open" stays a market fact while "they have 5 lakh" does not.
            rf"\b(?:he|she|they)\s+(?:has|have|had)\s+(?:{_MONEY}\s*[\d,.]+|"
            rf"[\d,.]+\s*{_MAGNITUDE}\b)",
            rf"\b(?:he|she|they)\s+(?:wants?|wishes|wish|needs?|plans?|hopes?)\s+to\s+"
            rf"(?:invest|trade|buy|sell|start|enter|put|park|deploy|retire|save)\b",
            rf"\b(?:his|her|their)\s+(?:capital|money|funds?|savings?|portfolio|holdings?|"
            rf"income|salary|net\s*worth|risk\s+(?:appetite|tolerance|profile)|goals?|"
            rf"retirement|corpus|budget|demat|trading\s+account)\b",
        ),
    ),
    (
        "suitability",
        (
            # Suitability *for the user* — instrument-level suitability ("is NIFTY
            # suitable for intraday") deliberately does not match.
            r"\b(?:suitable|suited|appropriate|right|good|ok|okay|fine|safe|wise|advisable|"
            r"sensible|risky|dangerous|aggressive|conservative|complex|complicated|advanced|"
            r"much|many)\s+for\s+(?:me|my|us|our)\b",
            # "for a conservative investor like me" — a bounded gap, because the
            # adjectives users insert here are unbounded ("for a small retail
            # investor like me") and each one would otherwise be a bypass.
            r"\bfor\s+(?:someone|somebody|a|an|people|persons?|investors?|traders?|"
            r"beginners?)\b.{0,30}?\blike\s+(?:me|us|myself|ourselves)\b",
            rf"\b{_MYADJ}\s+risk\s+(?:appetite|tolerance|profile|capacity|comfort|"
            rf"preference|willingness)\b",
            rf"\b{_MYADJ}\s+(?:age|profile|situation|circumstances|experience(?:\s+level)?|"
            rf"skill\s+level|knowledge\s+level)\b",
            # Experience/profile disclosures. "new to <market thing>" only — "new
            # to this platform" is a support question, not a suitability one.
            r"\bi\s*(?:'?m|\s+am)\s+(?:a\s+)?(?:beginner|newbie|novice|fresher|conservative|"
            r"aggressive|risk[-\s]averse|retired|a\s+student|a\s+pensioner|a\s+housewife|"
            r"new\s+to\s+(?:this|trading|the\s+market|markets|stocks?|equit(?:y|ies)|"
            r"options?|futures?|f\s*&\s*o|fno|derivatives?|investing|intraday)|"
            r"\d+\s+years?\s+old)\b",
            # A stated age, with or without the word "years": "I am 55 and retired".
            # Two digits only, so "I am 100% sure" cannot match.
            r"\bi\s*(?:'?m|\s+am)\s+(?:1[89]|[2-9]\d)\s*(?:years?\s*old|yrs?\s*old|yo)?\b",
            # Prepositional forms — restricted to a financial-profile noun so
            # "given my reading of the chart" does not trip.
            rf"\b(?:given|considering|based\s+on|in\s+light\s+of|with|for)\s+{_MYADJ}\s+"
            rf"(?:capital|funds?|money|portfolio|holdings?|income|salary|situation|profile|"
            rf"risk|goals?|age|net\s*worth|budget|savings?|circumstances|experience|"
            rf"exposure|allocation|corpus|account)\b",
            rf"\bwhat\s+should\s+i\s+do\s+(?:given|considering|with)\s+{_MY}\b",
            rf"\badvise\s+me\s+(?:on|about)\s+{_MY}\b",
        ),
    ),
)

# Compiled once at import. Order preserved from ``_PATTERNS``.
_COMPILED: Tuple[Tuple[str, Tuple[re.Pattern, ...]], ...] = tuple(
    (category, tuple(re.compile(p) for p in patterns))
    for category, patterns in _PATTERNS
)

# Every declared category must appear in the pattern table and vice versa —
# otherwise a category could be silently unreachable (a gap in the control) or a
# hit could carry a category nothing knows about. Checked at import so a bad edit
# fails at service start, not on the first user question.
assert tuple(c for c, _ in _PATTERNS) == PERSONALISATION_CATEGORIES, (
    "personalisation: _PATTERNS order/content must equal PERSONALISATION_CATEGORIES"
)


# ── Normalisation ─────────────────────────────────────────────────────────────

_WHITESPACE = re.compile(r"\s+")
_SMART_QUOTES = {
    "‘": "'",
    "’": "'",
    "‛": "'",
    "ʼ": "'",
    "“": '"',
    "”": '"',
}


def _normalise(text: str) -> str:
    """Fold ``text`` to the canonical form the patterns are written against.

    NFKC first, so full-width and compatibility characters (a pasted ``ｍｙ`` or a
    ligature) reduce to their ASCII equivalents instead of sliding past every
    pattern. Curly apostrophes become straight ones so ``I'm`` and ``I’m`` are
    the same input. Whitespace collapses so a line break inside a phrase does not
    break a bounded gap.
    """
    folded = unicodedata.normalize("NFKC", text)
    for smart, plain in _SMART_QUOTES.items():
        folded = folded.replace(smart, plain)
    return _WHITESPACE.sub(" ", folded).strip().lower()


def question_text(question: object) -> str:
    """Best-effort extraction of the user's text from a message payload.

    Accepts a plain string, or the list-of-content-parts shape LangChain uses for
    multimodal messages (``[{"type": "text", "text": "..."}, ...]``). Anything
    else yields ``""``. Never raises — this runs on the Q&A hot path and an
    exception here would break a legitimate question rather than fail closed.

    Public because callers that log the question (the graph node, the interaction
    log) need the same flattening the detector uses, so what is recorded is
    exactly what was inspected.
    """
    if isinstance(question, str):
        return question
    if isinstance(question, (list, tuple)):
        parts = []
        for item in question:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return " ".join(parts)
    return ""


# ── Detector ──────────────────────────────────────────────────────────────────


def detect_personalisation(question: object) -> Optional[PersonalisationHit]:
    """Return the personalisation category ``question`` requests, or ``None``.

    Pure, total and deterministic. Categories are tested in the fixed order of
    ``PERSONALISATION_CATEGORIES`` and the first match wins, so a question that
    spans several categories always reports the same one.

    ``None`` means "no personalisation detected" — it does NOT mean the question
    is safe in every other respect; it means this specific control found nothing.
    """
    text = _normalise(question_text(question))
    if not text:
        return None
    for category, patterns in _COMPILED:
        for pattern in patterns:
            match = pattern.search(text)
            if match is not None:
                return PersonalisationHit(category=category, matched=match.group(0).strip())
    return None


# ── Refusal ───────────────────────────────────────────────────────────────────

# What each category declines, phrased so the sentence reads naturally after
# "I can't". Membership-checked (never indexed) so an unexpected category can
# never raise on the hot path.
_CATEGORY_CLAUSE = {
    "position_sizing": "size a position or decide how much to allocate for you",
    "holdings": "advise on what you already hold",
    "capital": "tailor an answer to the capital you have available",
    "income": "take your income into account",
    "net_worth": "take your net worth or overall finances into account",
    "goals": "plan around your personal goals or timelines",
    "third_party": "advise on another person's behalf",
    "suitability": "judge whether a trade is suitable for you",
}

_FALLBACK_CLAUSE = "tailor an answer to your personal financial circumstances"

REFUSAL_HEADLINE = "This is impersonal research, so I can't {clause}."

REFUSAL_BODY = (
    "The same analysis goes to every subscriber on this tier. Nothing here is a "
    "personal recommendation, and no assessment has been made of your finances, "
    "holdings or objectives.\n\n"
    "What I can do is explain the recorded analysis for this session on its own "
    "terms: why the entry, stop-loss and take-profit sit where they do, the "
    "risk-reward and volatility basis behind them, what each analysis tool "
    "returned, and what would invalidate the setup. Ask the same question "
    "without reference to your own position and I will answer it from the "
    "recorded data.\n\n"
    "Position sizing, allocation, and whether any trade is appropriate for you "
    "are decisions for you — or for a SEBI-registered investment adviser who has "
    "assessed your circumstances."
)


def build_refusal(hit: PersonalisationHit) -> str:
    """Compose the fixed refusal for ``hit``.

    Deterministic: the only variation is one clause naming what was declined, so
    the user learns which part of their question crossed the line. No model is
    involved, so the wording cannot drift between runs, models or temperatures —
    which is what makes it reviewable as a control rather than as output.
    """
    category = getattr(hit, "category", None)
    clause = (
        _CATEGORY_CLAUSE[category]
        if isinstance(category, str) and category in _CATEGORY_CLAUSE
        else _FALLBACK_CLAUSE
    )
    return f"{REFUSAL_HEADLINE.format(clause=clause)}\n\n{REFUSAL_BODY}"


# Rule text injected into the Q&A system prompt as defence in depth. Kept here,
# beside the detector, so the prompt rule and the enforced control cannot drift
# apart in separate files.
QA_PROMPT_RULE = (
    "NEVER personalise. Do not tailor any answer to the user's capital, funds, "
    "income, net worth, existing holdings, position sizing, personal goals, age, "
    "tax situation or risk profile, and do not tell them how much to buy, how "
    "many shares/lots to take, what fraction of their money to allocate, or "
    "whether a trade is suitable for them — even if they volunteer those details "
    "or insist. This product publishes IMPERSONAL research only; personalised "
    "recommendations require a separate registration this service does not hold. "
    "If the user asks for that, decline briefly and answer the impersonal version "
    "of their question from the recorded context instead. (A deterministic guard "
    "refuses such questions before they reach you; this rule covers phrasings it "
    "does not catch.)"
)
