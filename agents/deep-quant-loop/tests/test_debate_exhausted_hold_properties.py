"""Property-based test that an exhausted debate finalizes a stated HOLD
(graph.py ``judge_node``, task 8.4).

Feature: multi-agent-debate

This module implements design **Property 13: An exhausted debate finalizes a
stated HOLD**:

    For ANY debate run that reaches the turn bound (``debate_turns >=
    max_turns``) without a validated Judge ``declare_trade``, the finalized
    decision has ``action == "HOLD"`` and carries a stated reason rather than a
    fabricated trade.

Validates: Requirements 5.3.

Requirement 5.3 states: "WHEN the Judge_Agent fails to commit a validated trade
within the bounded debate turns, THE Deep_Quant_Agent SHALL finalize a HOLD with
a stated reason rather than fabricating a trade."

Hermetic strategy (no LLM, no network):

* ``graph.get_judge_llm`` is monkeypatched to return a stub LLM whose
  ``.invoke(messages)`` returns an ``AIMessage`` carrying ONLY pure reasoning
  prose and an empty ``tool_calls`` list. The prose is drawn from a curated
  sentence pool so it can never accidentally parse as a ``declare_trade`` (or any
  other) tool call. With no actionable tool call, ``judge_node`` exhausts its
  bounded loop without committing and MUST finalize a stated HOLD.
* ``graph._finalize_decision`` is monkeypatched to a no-op returning a dummy
  dict, so no journal / DB write happens (the real finalize path is exercised by
  the dedicated finalize-parity test, task 8.6).

Both patches are saved and restored in a ``finally`` block (the monkeypatch
fixture is not reset between hypothesis-generated inputs). The arbitrary stored
Bull/Bear stances, the judge reasoning content, and the ``debate_turns`` are all
drawn from hypothesis strategies. The sys.path / import pattern mirrors the
sibling ``test_debate_only_judge_commits_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import AIMessage, ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import judge_node  # noqa: E402
from debate import DEBATE_CONSENSUS_VALUES  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Stub Judge LLM: returns ONLY pure reasoning prose, never a tool call.
# ─────────────────────────────────────────────────────────────────────────────

# Curated reasoning fragments. None of these contain a registered tool name
# followed by a JSON object, a custom-token call block, or a separator token, so
# ``extract_tool_calls`` discovers ZERO tool calls — the Judge therefore never
# commits and the bounded loop falls through to the stated-HOLD finalize path.
_REASONING_FRAGMENTS = [
    "The bull case rests on momentum that the shared evidence only weakly supports.",
    "The bear case highlights a clear risk that I cannot dismiss with this data.",
    "Both stances are plausible, and the disagreement leaves no A-plus setup.",
    "I am weighing the structure of the debate before reaching any conclusion.",
    "The risk-reward here is unattractive given the contested interpretation.",
    "Neither side has produced a defensible, validated entry from this evidence.",
    "Holding preserves capital rather than forcing a low-conviction position.",
    "The arguments cancel out and I see no edge worth committing to.",
    "I would rather pass than rationalize a marginal trade out of these stances.",
    "There is insufficient confluence to justify taking directional risk now.",
]


@st.composite
def _reasoning_content(draw):
    """Arbitrary judge reasoning prose that never declares a trade.

    Either an empty string / ``None`` (empty/garbled output) or one-to-four
    curated sentences joined together. The curated pool guarantees the content
    cannot be parsed as any tool call, keeping the test fully hermetic.
    """
    chosen = draw(
        st.lists(st.sampled_from(_REASONING_FRAGMENTS), min_size=0, max_size=4)
    )
    if not chosen:
        # An empty-content turn is also valid "pure reasoning that never commits".
        return draw(st.sampled_from(["", None]))
    return " ".join(chosen)


class _StubResponse:
    """Minimal stand-in for an ``AIMessage`` response from the Judge LLM.

    ``judge_node`` reads ``response.content`` (via ``extract_tool_calls`` ->
    ``getattr(response, "content", "")``) and ``response.tool_calls``. Exposing an
    empty ``tool_calls`` plus pure-reasoning ``content`` guarantees no actionable
    tool call is discovered, so the Judge never commits.
    """

    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.additional_kwargs = {}


class _StubJudgeLLM:
    """Stub returned by the monkeypatched ``get_judge_llm``.

    ``.invoke(messages)`` ignores the messages and returns the same
    pure-reasoning ``_StubResponse`` every turn, so NO real LLM / network call
    ever happens and the Judge can never declare a trade.
    """

    def __init__(self, content):
        self._content = content

    def invoke(self, messages):
        return _StubResponse(self._content)


# ─────────────────────────────────────────────────────────────────────────────
# Arbitrary stored Bull/Bear stances (as they would be persisted in state).
# ─────────────────────────────────────────────────────────────────────────────

_STANCE_DICT = st.fixed_dictionaries(
    {
        "lean": st.sampled_from(["long", "short", "neutral"]),
        "strength": st.integers(min_value=0, max_value=100),
        "arguments": st.lists(st.text(max_size=40), max_size=4),
        "biggest_risk": st.text(max_size=60),
    }
)

# A stored stance may be a well-formed dict, ``None`` (role unavailable / never
# ran), or an empty dict — the pure debate core treats a missing stance as
# strength 0 and never fabricates one.
_STORED_STANCE = st.one_of(_STANCE_DICT, st.none(), st.just({}))

# Evidence ToolMessage names drawn from the read-only analysis tools so the
# shared-evidence collection has realistic content to render.
_EVIDENCE_NAMES = st.sampled_from(
    [
        "get_candles",
        "get_consensus_report",
        "get_multi_tf_trend",
        "get_chart_patterns",
        "get_support_resistance",
        "get_market_regime",
    ]
)


def _build_state(bull_stance, bear_stance, evidence, debate_turns):
    """Build a DEBATE state at/over the turn bound with stored stances."""
    messages = [AIMessage(content="research complete", tool_calls=[])]
    for i, (name, content) in enumerate(evidence):
        messages.append(ToolMessage(content=content, name=name, tool_call_id=f"ev_{i}"))
    return {
        "messages": messages,
        "mode": "DEBATE",
        "phase": "debate",
        "symbol": "TEST",
        "debate_turns": debate_turns,
        "debate_round": (debate_turns // 2) + 1,
        "bull_stance": bull_stance,
        "bear_stance": bear_stance,
        "decision": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: An exhausted debate finalizes a stated HOLD
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 13: An exhausted debate finalizes a stated HOLD
@settings(max_examples=100, deadline=None)
@given(
    bull_stance=_STORED_STANCE,
    bear_stance=_STORED_STANCE,
    content=_reasoning_content(),
    evidence=st.lists(st.tuples(_EVIDENCE_NAMES, st.text(max_size=60)), max_size=5),
    debate_turns=st.integers(min_value=0, max_value=12),
)
def test_property_13_exhausted_debate_finalizes_hold(
    bull_stance, bear_stance, content, evidence, debate_turns
):
    """Validates: Requirements 5.3

    For ANY stored Bull/Bear stances and ANY judge reasoning that never declares
    a trade, once the Judge fails to commit a validated trade within its bounded
    budget, ``judge_node`` finalizes a stated HOLD:
      * the returned decision has ``action == "HOLD"``,
      * it carries a stated, non-empty reason (and the ``debate_hold`` source)
        rather than a fabricated trade, and
      * ``debate_consensus`` / ``debate_conviction`` are set in the update.
    """
    # Stub the Judge LLM (no commit, no network) and no-op the finalize path so
    # no journal/DB write happens. Save/restore explicitly because the monkeypatch
    # fixture is not reset between hypothesis-generated inputs.
    original_get_judge_llm = graph.get_judge_llm
    original_finalize = graph._finalize_decision
    graph.get_judge_llm = lambda: _StubJudgeLLM(content)
    graph._finalize_decision = lambda state, decision, thread_id=None: {}
    try:
        state = _build_state(bull_stance, bear_stance, evidence, debate_turns)
        update = judge_node(state)

        # ── A decision must be finalized (the run does not hang/return empty). ──
        assert "decision" in update, "judge_node must finalize a decision"
        decision = update["decision"]
        assert isinstance(decision, dict), f"decision must be a dict, got {decision!r}"

        # ── It is a HOLD, never a fabricated trade (R5.3). ──────────────────────
        assert decision.get("action") == "HOLD", (
            f"an exhausted debate must finalize action=='HOLD', got "
            f"{decision.get('action')!r}"
        )

        # ── The HOLD carries a stated reason rather than a fabricated trade. ────
        assert decision.get("source") == "debate_hold", (
            f"the exhausted-debate HOLD must be sourced 'debate_hold', got "
            f"{decision.get('source')!r}"
        )
        reason = decision.get("reason")
        assert isinstance(reason, str) and reason.strip(), (
            f"the HOLD must carry a non-empty stated reason, got {reason!r}"
        )
        # A stated rationale (setup_validation) is present and non-empty too.
        setup_validation = decision.get("setup_validation")
        assert isinstance(setup_validation, str) and setup_validation.strip(), (
            f"the HOLD must state its rationale, got {setup_validation!r}"
        )

        # ── The synthesis bookkeeping is set in the update. ─────────────────────
        consensus = update.get("debate_consensus")
        assert consensus in DEBATE_CONSENSUS_VALUES, (
            f"debate_consensus must be one of {sorted(DEBATE_CONSENSUS_VALUES)}, "
            f"got {consensus!r}"
        )
        conviction = update.get("debate_conviction")
        assert isinstance(conviction, int) and not isinstance(conviction, bool), (
            f"debate_conviction must be an int, got {conviction!r}"
        )
        assert 0 <= conviction <= 100, (
            f"debate_conviction must be in [0, 100], got {conviction!r}"
        )
    finally:
        graph.get_judge_llm = original_get_judge_llm
        graph._finalize_decision = original_finalize
