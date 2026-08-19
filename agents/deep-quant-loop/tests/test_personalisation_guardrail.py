"""P8a — the personalisation refusal guardrail is a control, not a prompt rule.

`docs/business/PLAN_OF_ACTION.md` §4.2 lists blocker **P8a**, and §8.1 calls it
"the load-bearing control" for the US publisher's exclusion (*Lowe v. SEC*). Two
things therefore need proving, and the second is the one a prompt-only
implementation would fail:

  1. The detector fires on personalised questions and stays quiet on impersonal
     ones — over a generated corpus, not a handful of examples.
  2. **The refusal never reaches the LLM.** `qa_node` must return the fixed
     refusal without invoking any model. A guardrail that lets the model answer
     and then appends a disclaimer has personalised the answer anyway; the
     tripwire below fails the suite if the model is touched.

Non-vacuity matters as much as the assertions: the same tripwire is checked to
FIRE on an impersonal question, so a mis-wired stub cannot make the suite pass by
never calling anything at all.
"""

import re

import pytest
from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage, HumanMessage

import personalisation
from personalisation import (
    PERSONALISATION_CATEGORIES,
    PersonalisationHit,
    build_refusal,
    detect_personalisation,
)

# ── Corpora ──────────────────────────────────────────────────────────────────
# Real phrasings, grouped by the category each must be recognised as. Written the
# way a retail user actually types (lower case, no punctuation discipline, Indian
# money units) because that is the input the control has to survive.

_MUST_MATCH: dict[str, tuple[str, ...]] = {
    "position_sizing": (
        "how many shares should i buy of this",
        "how much should i invest in this setup",
        "how much of my 5 lakh should i put in this",
        "how many lots should i take",
        "how many shares to buy here",
        "what quantity should i take on this trade",
        "what position size for me",
        "how much margin do i need for one lot",
        "how much to risk on this",
        "what percentage of my portfolio should go here",
        "should i go all in on this",
        "should i double my exposure here",
        "what is my ideal position size",
        "what is my max position size here",
    ),
    "holdings": (
        "does this fit my portfolio",
        "what about my holdings in this stock",
        "i already own this stock, what now",
        "i am long from lower levels, should i stay",
        "i'm short here, is that a problem",
        "my average price is 24100, what do i do",
        "how do i cut my losses on this",
        "should i book my profits now",
        "what about my existing position in banknifty",
        "i have bought this yesterday",
        "my mutual funds are down, should i switch",
    ),
    "capital": (
        "i have 50000 rupees, what should i do",
        "my capital is small, is this viable",
        "with 2 lakh in capital can i trade this",
        "i want to invest 100000 in this",
        "my trading account has limited funds",
        "i only have a small budget",
        "can i afford this trade",
        "my rs 25000 is all i have",
        "i have about 3 lakh",
    ),
    "income": (
        "my salary is 80000 a month, how much should go here",
        "i earn well, does that change the plan",
        "my monthly income is irregular",
        "i am salaried, what suits that",
        "i make 150000 every month",
    ),
    "net_worth": (
        "my net worth is mostly in equity",
        "my savings are in fixed income right now",
        "given my financial situation what next",
        "my total wealth is tied up in property",
    ),
    "goals": (
        "i want to retire in ten years, does this help",
        "this is for my child's education",
        "my time horizon is three years",
        "i need to save for a house",
        "my emi is high, can i still do this",
        "what about my tax on this",
    ),
    "third_party": (
        "my father is retired, is this ok for him",
        "is this good for my wife",
        "i am asking on behalf of my brother",
        "my friend wants to buy this, what should he do",
        "he is retired and needs steady income",
        "they have 10 lakh to invest",
        "what should i tell my mother about this",
        "her portfolio is mostly debt, is this ok",
        "is this suitable for them",
        "my client has 50 lakh, where should he put it",
    ),
    "suitability": (
        "is this suitable for me",
        "is this trade right for me",
        "is it safe for me to take this",
        "my risk appetite is low, what then",
        "i am a beginner, should i do this",
        "i'm a retired person, what would you suggest",
        "for someone like me is this sensible",
        "what should i do given my situation",
        "is this appropriate for my age",
        "is this trade right for a conservative investor like me",
        "i am 55 and retired, is this too risky for me",
        "is this too complex for me",
    ),
}

# Legitimate impersonal questions. Every one of these is a question about the
# RESEARCH — the product's whole purpose — and must keep working. This corpus is
# the counterweight to a detector that "leans toward refusing": it pins the floor
# below which leaning is no longer acceptable.
_MUST_NOT_MATCH: tuple[str, ...] = (
    "why was the stop loss placed at 24150",
    "what is the risk reward ratio on this trade",
    "how many chart patterns did you detect",
    "how many contracts traded today",
    "what does the rsi read on the 15 minute",
    "is the trend up or down on the daily",
    "what is the lot size for nifty",
    "how much has the price moved today",
    "what is the max pain strike this expiry",
    "why did you hold instead of buying",
    "how many days until expiry",
    "what was the volume profile poc",
    "which indicators supported the entry",
    "is banknifty suitable for intraday trading",
    "how much margin does one lot require",
    "how much profit would this trade make at target",
    "should i buy or sell",
    "is this a good entry level",
    "why is my entry at 24500",
    "why is my trade a hold",
    "what is my stop loss on this recommendation",
    "my question is about the take profit",
    "in my view the trend is up, do you agree",
    "what would invalidate this setup",
    "what is the atr based volatility basis here",
    "how confident is the forecast",
    "which tool gave the support level",
    "what is the option chain pcr right now",
    "how many times has this pattern appeared",
    "what percentage move does the target imply",
    "is the market regime favourable",
    "how much volume came in at the poc",
    "what is the relative strength versus nifty",
    "why was the take profit set below the resistance",
    "explain the multi timeframe trend",
    # Third-person market talk. "they" in an options question means the writers,
    # buyers or institutions on the other side — not a person asking for advice.
    # These pin the third_party patterns to an actual human predicate.
    "they are showing high oi at 25000",
    "they are selling into the resistance",
    "they have 5000 contracts open at 25000",
    "they have booked profits near the highs",
    "she said the market is bullish",
    "is their oi rising or falling",
    "what are they doing at the 25000 strike",
    # The widened adjective run inside `my <noun>` patterns must not swallow
    # ordinary possessives about the analysis itself.
    "my chart shows a different support level",
    "my understanding is that oi is rising",
    "i have 2 doubts about the entry",
    "how many lots trade at the oi wall",
    "is the current allocation of oi bullish",
    "what is the total volume today",
    # Evaluative adjectives about the SETUP, not about the user. These are the
    # nearest neighbours of the suitability patterns, so they are pinned.
    "is this too risky for a swing trade",
    "what is the right entry for this setup",
    "what is the correct stop loss placement",
    "is this appropriate for intraday",
    "is 24500 a safe entry",
    "what is the recommended timeframe for this pattern",
    "i am 100% sure the trend is up",
    "i am 2 points away from the target",
)


# ── 1. Detection ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "category,question",
    [(cat, q) for cat, questions in _MUST_MATCH.items() for q in questions],
)
def test_personalised_question_is_detected(category, question):
    hit = detect_personalisation(question)
    assert hit is not None, f"personalisation NOT detected: {question!r}"
    assert hit.category in PERSONALISATION_CATEGORIES
    # The declared category is asserted, not just "some hit": a mislabelled hit
    # still refuses correctly but records the wrong reason in the audit log.
    assert hit.category == category, (
        f"{question!r} detected as {hit.category!r}, expected {category!r}"
    )
    assert hit.matched, "a hit must carry the substring that tripped it"
    assert hit.matched in personalisation._normalise(question)


@pytest.mark.parametrize("question", _MUST_NOT_MATCH)
def test_impersonal_research_question_is_not_refused(question):
    hit = detect_personalisation(question)
    assert hit is None, (
        f"legitimate impersonal question refused: {question!r} "
        f"(category={hit.category if hit else None}, matched={hit.matched if hit else None})"
    )


def test_every_category_is_reachable():
    """No declared category may be dead code.

    An unreachable category means either a pattern set that can never fire (a
    hole in the control) or a stale label. Both are silent, so they are pinned.
    """
    reached = {detect_personalisation(q).category for qs in _MUST_MATCH.values() for q in qs}
    assert reached == set(PERSONALISATION_CATEGORIES)


# ── 2. Robustness of detection ───────────────────────────────────────────────


@given(
    st.sampled_from([q for qs in _MUST_MATCH.values() for q in qs]),
    st.sampled_from(["", " ", "  ", "\n", "\t", "\n\n"]),
    st.sampled_from(["", "?", "!", "...", " please", " thanks", " pls answer"]),
)
@settings(max_examples=200, deadline=None)
def test_detection_survives_whitespace_and_padding(question, pad, suffix):
    """Cosmetic edits must not open a bypass.

    Whitespace, trailing politeness and punctuation are exactly what varies
    between two users asking the same prohibited question.
    """
    mutated = f"{pad}{question}{suffix}{pad}"
    assert detect_personalisation(mutated) is not None


@given(st.sampled_from([q for qs in _MUST_MATCH.values() for q in qs]))
@settings(max_examples=200, deadline=None)
def test_detection_is_case_insensitive(question):
    for variant in (question.upper(), question.title(), _alternating_case(question)):
        assert detect_personalisation(variant) is not None, variant


def _alternating_case(text: str) -> str:
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))


@given(st.sampled_from([q for qs in _MUST_MATCH.values() for q in qs]))
@settings(max_examples=200, deadline=None)
def test_detection_survives_smart_apostrophes_and_full_width(question):
    """Unicode look-alikes are the cheapest bypass, so normalisation is pinned.

    A pasted question from a phone keyboard carries U+2019 rather than ASCII
    ``'``; a full-width paste carries U+FF4D for ``m``. Neither may slip past.
    """
    assert detect_personalisation(question.replace("'", "’")) is not None
    full_width = question.translate(
        {ord(c): ord(c) - 0x21 + 0xFF01 for c in question if "!" <= c <= "~"}
    )
    assert detect_personalisation(full_width) is not None


@given(
    st.sampled_from([q for qs in _MUST_MATCH.values() for q in qs]),
    st.sampled_from(_MUST_NOT_MATCH),
)
@settings(max_examples=200, deadline=None)
def test_personalisation_buried_in_a_longer_question_still_detected(personal, benign):
    """Wrapping the prohibited ask in legitimate context must not dilute it.

    "Why is the stop at 24150, and how much of my 5 lakh should I put in?" is
    still a request for personalised advice.
    """
    assert detect_personalisation(f"{benign}. also {personal}") is not None
    assert detect_personalisation(f"{personal}. also {benign}") is not None


# ── 3. Purity and totality ───────────────────────────────────────────────────


@given(st.text(max_size=400))
@settings(max_examples=400, deadline=None)
def test_detector_never_raises_on_arbitrary_text(text):
    result = detect_personalisation(text)
    assert result is None or isinstance(result, PersonalisationHit)


@given(
    st.one_of(
        st.none(),
        st.integers(),
        st.floats(allow_nan=True, allow_infinity=True),
        st.booleans(),
        st.lists(st.text(max_size=20), max_size=5),
        st.dictionaries(st.text(max_size=5), st.text(max_size=5), max_size=3),
        st.lists(st.dictionaries(st.just("text"), st.text(max_size=20)), max_size=3),
    )
)
@settings(max_examples=300, deadline=None)
def test_detector_never_raises_on_non_string_input(value):
    """Message content is not always a string.

    LangChain multimodal content is a list of parts, and a malformed payload can
    be anything. The detector runs on the Q&A hot path, so a TypeError here would
    take down a legitimate question.
    """
    result = detect_personalisation(value)
    assert result is None or isinstance(result, PersonalisationHit)


@given(st.text(max_size=200))
@settings(max_examples=300, deadline=None)
def test_detector_is_deterministic(text):
    """Same input, same output — a refusal must be reproducible at inspection.

    Purity is the property that lets a refusal from two years ago be re-derived
    from the logged question alone.
    """
    first = detect_personalisation(text)
    second = detect_personalisation(text)
    assert first == second


def test_multimodal_content_parts_are_inspected():
    """A question arriving as content parts must not bypass the control."""
    parts = [
        {"type": "text", "text": "given the levels, "},
        {"type": "text", "text": "how much of my 5 lakh should i put in this"},
    ]
    assert detect_personalisation(parts) is not None


def test_empty_and_blank_input_is_not_a_hit():
    for value in ("", "   ", "\n\t", None, [], {}):
        assert detect_personalisation(value) is None


# ── 4. The refusal text ──────────────────────────────────────────────────────


@given(st.sampled_from(PERSONALISATION_CATEGORIES), st.text(max_size=30))
@settings(max_examples=100, deadline=None)
def test_refusal_is_deterministic_and_complete(category, matched):
    hit = PersonalisationHit(category=category, matched=matched)
    text = build_refusal(hit)
    assert text == build_refusal(hit)
    # The three things the refusal must do: decline, explain the impersonal
    # basis, and redirect. Asserted on substance, not on exact prose, so the
    # [COUNSEL] wording pass does not break the suite.
    assert "impersonal research" in text
    assert "investment adviser" in text.lower()
    assert "recorded" in text  # points the user at what CAN be answered


def test_refusal_never_raises_on_an_unknown_category():
    """An unexpected category must degrade to the generic clause, not crash.

    ``_CATEGORY_CLAUSE`` is membership-checked rather than indexed: on the Q&A
    hot path a KeyError would turn a refusal into a 500, which reads to the user
    as a broken product rather than a boundary.
    """
    text = build_refusal(PersonalisationHit(category="not_a_category", matched="x"))
    assert "impersonal research" in text
    text_none = build_refusal(PersonalisationHit(category=None, matched=""))  # type: ignore[arg-type]
    assert "impersonal research" in text_none


def test_refusal_makes_no_registration_claim():
    """The refusal must not assert a registration the entity does not yet hold.

    Claiming or implying SEBI registration before INH is granted is its own
    violation, so the copy is checked for it here rather than in review.
    """
    text = build_refusal(PersonalisationHit(category="suitability", matched="for me"))
    lowered = text.lower()
    for claim in (
        "we are registered",
        "we are a registered",
        "sebi registered research analyst",
        "sebi-registered research analyst",
        "our registration",
        "inh",
    ):
        assert claim not in lowered, f"refusal implies registration: {claim!r}"


def test_refusal_gives_no_personalised_answer():
    """The refusal itself must not sneak in advice.

    A refusal that ends "but for your size I'd suggest 2 lots" is not a refusal.
    """
    text = build_refusal(PersonalisationHit(category="position_sizing", matched="how many")).lower()
    assert not re.search(r"\b\d+\s*(?:lots?|shares?|units?|%)\b", text)
    assert "i suggest you" not in text
    assert "i recommend" not in text


# ── 5. The refusal never reaches the model (the load-bearing assertion) ──────


@pytest.fixture
def llm_tripwire(monkeypatch):
    """Fail if `qa_node` invokes any LLM.

    Every model entry point `qa_node` can take is replaced with a stub that
    records the call. This is what separates a control from a disclaimer: if the
    model runs, the personalised answer was generated (and billed, and logged)
    even if something downstream replaced the text.
    """
    tripped = {"called": False}

    class _Boom:
        def invoke(self, *_args, **_kwargs):
            tripped["called"] = True
            return AIMessage(content="MODEL WAS CALLED")

    import graph

    monkeypatch.setattr(graph, "_base_llm_for_run", lambda *a, **k: _Boom())
    monkeypatch.setattr(graph, "_build_profile_llm_for_model", lambda *a, **k: _Boom())
    return tripped


def _qa_state(question: str) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "symbol": "RELIANCE",
        "mode": "QA",
        "qa_turns": 0,
    }


@pytest.mark.parametrize(
    "question", [qs[0] for qs in _MUST_MATCH.values()]
)
def test_qa_node_refuses_without_calling_the_model(question, llm_tripwire):
    import graph

    result = graph.qa_node(_qa_state(question))

    assert llm_tripwire["called"] is False, "LLM was invoked for a refused question"
    messages = result["messages"]
    assert len(messages) == 1
    answer = messages[0]
    assert "impersonal research" in answer.content
    assert "MODEL WAS CALLED" not in answer.content
    # No tool call, so `qa_should_continue` routes to "end" and the turn is final.
    assert not getattr(answer, "tool_calls", None)
    assert graph.qa_should_continue({"messages": messages, "qa_turns": 1}) == "end"


@pytest.mark.parametrize("question", list(_MUST_MATCH["position_sizing"][:4]))
def test_refused_turn_never_touches_the_committed_decision(question, llm_tripwire):
    """R18.6 still holds on the refusal path.

    The refusal is an early return, so it must not accidentally acquire the power
    the normal path is denied: no ``decision`` key may appear in the update.
    """
    import graph

    result = graph.qa_node(_qa_state(question))
    assert "decision" not in result
    assert set(result) == {"messages", "qa_turns"}


def test_refused_turn_increments_the_turn_counter(llm_tripwire):
    """The bounded Q&A budget must still advance, or a refusal loop is free.

    If a refusal did not consume a turn, a user could hold the thread open
    indefinitely; the counter is what makes the loop terminate either way.
    """
    import graph

    state = _qa_state("how many shares should i buy")
    state["qa_turns"] = 3
    assert graph.qa_node(state)["qa_turns"] == 4


def test_refusal_records_the_category_for_the_interaction_log(llm_tripwire):
    """P5 needs to log WHY a turn was refused without re-deriving it."""
    import graph

    result = graph.qa_node(_qa_state("is this suitable for me"))
    kwargs = result["messages"][0].additional_kwargs
    assert kwargs.get("_personalisation_refusal") in PERSONALISATION_CATEGORIES


def test_tripwire_actually_fires_on_an_impersonal_question(llm_tripwire, monkeypatch):
    """Non-vacuity: prove the tripwire CAN trip.

    Without this, a stub that is never reached would make every assertion above
    pass for the wrong reason.
    """
    import graph

    monkeypatch.setattr(graph, "build_qa_context", lambda _state: {"has_declared_trade": False})
    graph.qa_node(_qa_state("why was the stop loss placed at 24150"))
    assert llm_tripwire["called"] is True, "an impersonal question must reach the model"


# ── 6. Defence in depth: the prompt rule is present too ─────────────────────


def test_qa_system_prompt_carries_the_personalisation_rule():
    """The prompt rule must exist AND be the same text as the module's.

    Two copies of a rule drift. Asserting identity with
    ``personalisation.QA_PROMPT_RULE`` means the prompt can only change by
    changing the module the detector lives in.
    """
    import graph

    prompt = graph.build_qa_system_prompt({"has_declared_trade": True})
    assert personalisation.QA_PROMPT_RULE in prompt
    assert "6." in prompt  # numbered into the existing rule list, not appended loose


def test_prompt_rule_names_what_must_not_be_personalised():
    rule = personalisation.QA_PROMPT_RULE.lower()
    for term in ("capital", "income", "holdings", "goals", "risk profile", "suitable"):
        assert term in rule, f"prompt rule omits {term!r}"
