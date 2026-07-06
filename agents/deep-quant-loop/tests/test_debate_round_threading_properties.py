"""Property-based test for prior-round stance threading (graph.py, task 7.3).

Feature: multi-agent-debate

This module implements design **Property 9: Prior-round stances are threaded
into later rounds**:

    For ANY configured round count R > 1, the Bear_Agent's input at round k
    includes the Bull_Agent stance produced at round k, and the Bull_Agent's
    input at round k+1 includes the Bear_Agent stance produced at round k.

Validates: Requirements 3.6.

The threading lives in ``graph._run_debate_role`` (shared by ``bull_node`` /
``bear_node``):

  * For the BEAR role, whenever a prior ``bull_stance`` is present in the state,
    its JSON is embedded into the HumanMessage handed to the role LLM
    ("The BULL argued the following ...").
  * For the BULL role, when ``debate_round > 1`` AND a prior ``bear_stance`` is
    present, the Bear stance JSON is embedded ("The BEAR argued the following in
    the prior round ..."). ``bull_node`` derives the round deterministically as
    ``(debate_turns // TURNS_PER_ROUND) + 1``, so seeding
    ``debate_turns == TURNS_PER_ROUND`` yields round 2 (the "k+1" case).

To test this without a live LLM we monkeypatch ``graph.get_role_llm`` to return a
stub whose ``.invoke(messages)`` CAPTURES the messages it receives (so the test
can inspect the HumanMessage content) and returns a valid stance-JSON AIMessage.
The opposing prior stances are generated with unique marker tokens (drawn from a
``[A-Z0-9_]`` alphabet so ``json.dumps`` never escapes them); the test asserts
every marker appears verbatim in the role's input message for the appropriate
round. The sys.path / import pattern mirrors the sibling test modules.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the agent package importable (graph.py / debate.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import bull_node, bear_node  # noqa: E402
from debate import TURNS_PER_ROUND  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402


# ── Capturing stub LLM ────────────────────────────────────────────────────────
class _CapturingStubLLM:
    """A stand-in for the role LLM whose ``.invoke`` records its input messages.

    It returns a valid stance-JSON ``AIMessage`` so ``_run_debate_role`` parses a
    usable stance and completes normally, while the captured message list lets
    the test inspect the threaded HumanMessage content.
    """

    def __init__(self, captured):
        self._captured = captured

    def invoke(self, messages):
        self._captured.append(messages)
        return AIMessage(
            content=json.dumps(
                {
                    "role": "stub",
                    "lean": "neutral",
                    "strength": 50,
                    "arguments": ["stub argument"],
                    "biggest_risk": "stub risk",
                    "available": True,
                }
            )
        )


# A marker alphabet that ``json.dumps`` reproduces verbatim (no escaping), so a
# generated marker token is guaranteed to appear in the dumped prior-stance JSON.
_MARKER = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=8, max_size=20
)


@st.composite
def _stance_with_markers(draw, role, tag):
    """An arbitrary, distinctive stance dict + the unique marker tokens in it.

    The markers (embedded in ``arguments`` and ``biggest_risk``) are tagged so
    the Bull and Bear stances never collide, letting the test assert the right
    side's stance was threaded in.
    """
    n_args = draw(st.integers(min_value=1, max_value=3))
    markers = [f"{tag}_ARG{i}_{draw(_MARKER)}" for i in range(n_args)]
    risk_marker = f"{tag}_RISK_{draw(_MARKER)}"
    stance = {
        "role": role,
        "lean": draw(st.sampled_from(["long", "short", "neutral"])),
        "strength": draw(st.integers(min_value=0, max_value=100)),
        "arguments": list(markers),
        "biggest_risk": risk_marker,
        "available": True,
    }
    return stance, markers + [risk_marker]


def _last_human_content(captured):
    """The text content of the HumanMessage from the most recent capture."""
    assert captured, "the stub LLM was never invoked"
    messages = captured[-1]
    humans = [m for m in messages if isinstance(m, HumanMessage)]
    assert humans, "no HumanMessage was passed to the role LLM"
    content = humans[-1].content
    return content if isinstance(content, str) else str(content)


# ─────────────────────────────────────────────────────────────────────────────
# Property 9: Prior-round stances are threaded into later rounds
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 9: Prior-round stances are threaded into later rounds
@settings(max_examples=100, deadline=None)
@given(bull_data=_stance_with_markers("bull", "BULLMARK"))
def test_property_9_bear_threads_prior_bull_stance(bull_data):
    """Validates: Requirements 3.6

    The Bear_Agent's input at round k includes the Bull_Agent stance produced at
    round k: with a prior ``bull_stance`` in the state, every marker token from
    that stance appears verbatim in the HumanMessage handed to the Bear role LLM.
    """
    bull_stance, markers = bull_data

    captured = []
    original = graph.get_role_llm
    graph.get_role_llm = lambda role: _CapturingStubLLM(captured)
    try:
        state = {
            "messages": [],
            "bull_stance": bull_stance,
            "bear_stance": None,
            "debate_round": 2,
            "debate_turns": TURNS_PER_ROUND,
        }
        bear_node(state)
        content = _last_human_content(captured)
    finally:
        graph.get_role_llm = original

    # The Bull stance must be threaded into the Bear's input message (R3.6).
    assert "The BULL argued the following" in content
    for marker in markers:
        assert marker in content, (
            f"Bull stance marker {marker!r} was not threaded into the Bear input"
        )


# Feature: multi-agent-debate, Property 9: Prior-round stances are threaded into later rounds
@settings(max_examples=100, deadline=None)
@given(
    bear_data=_stance_with_markers("bear", "BEARMARK"),
    extra_turns=st.integers(min_value=0, max_value=3),
)
def test_property_9_bull_threads_prior_bear_stance_in_later_round(bear_data, extra_turns):
    """Validates: Requirements 3.6

    The Bull_Agent's input at round k+1 includes the Bear_Agent stance produced
    at round k. ``bull_node`` derives the round as
    ``(debate_turns // TURNS_PER_ROUND) + 1``; seeding ``debate_turns`` to a full
    number of completed rounds (>= 1) puts the Bull in round >= 2, where the
    prior ``bear_stance`` must be threaded into its input message.
    """
    bear_stance, markers = bear_data
    # Complete at least one full round so the derived round is >= 2 (the "k+1"
    # case). debate_turns = TURNS_PER_ROUND * rounds_done, rounds_done >= 1.
    rounds_done = 1 + extra_turns
    debate_turns = TURNS_PER_ROUND * rounds_done

    captured = []
    original = graph.get_role_llm
    graph.get_role_llm = lambda role: _CapturingStubLLM(captured)
    try:
        state = {
            "messages": [],
            "bull_stance": None,
            "bear_stance": bear_stance,
            "debate_turns": debate_turns,
        }
        bull_node(state)
        content = _last_human_content(captured)
    finally:
        graph.get_role_llm = original

    # The Bear stance must be threaded into the later-round Bull input (R3.6).
    assert "The BEAR argued the following in the prior round" in content
    for marker in markers:
        assert marker in content, (
            f"Bear stance marker {marker!r} was not threaded into the round "
            f">1 Bull input"
        )


# Feature: multi-agent-debate, Property 9: Prior-round stances are threaded into later rounds
@settings(max_examples=100, deadline=None)
@given(bear_data=_stance_with_markers("bear", "BEARMARK"))
def test_property_9_bull_does_not_thread_bear_stance_in_first_round(bear_data):
    """Validates: Requirements 3.6

    Complement of the threading rule: in round 1 (no prior round exists) the Bull
    has no prior Bear stance to rebut, so a (defensive) ``bear_stance`` must NOT
    be threaded into the Bull's first-round input. This pins the "later rounds"
    semantics: threading is exclusive to round > 1.
    """
    bear_stance, markers = bear_data

    captured = []
    original = graph.get_role_llm
    graph.get_role_llm = lambda role: _CapturingStubLLM(captured)
    try:
        state = {
            "messages": [],
            "bull_stance": None,
            "bear_stance": bear_stance,
            "debate_turns": 0,  # round 1
        }
        bull_node(state)
        content = _last_human_content(captured)
    finally:
        graph.get_role_llm = original

    assert "The BEAR argued the following in the prior round" not in content
    for marker in markers:
        assert marker not in content, (
            f"Bear stance marker {marker!r} must NOT be threaded into the "
            f"first-round Bull input"
        )
