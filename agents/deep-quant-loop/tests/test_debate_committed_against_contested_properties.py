"""Property-based test for the contested-but-committed debate flag (graph.py).

Feature: multi-agent-debate

This module implements design **Property 19: Contested-but-committed trades are
flagged**:

    When the Debate_Consensus is ``contested`` AND the Judge committed a
    directional BUY/SELL trade, the debate entry built by
    ``build_defensibility_record`` includes an explicit
    ``committed_against_contested`` statement. For a non-contested consensus
    (``strong_agree`` / ``lean``), OR for a HOLD action, the statement is absent.

Validates: Requirements 7.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` — the
    top-level record builder. In DEBATE mode (no ``manual_trade``) it resolves
    the action from ``decision["action"]`` via ``_resolve_action_and_levels`` /
    ``_normalize_action`` and attaches ``record["debate"]`` via ``_debate_entry``.
  - ``_debate_entry(decision, mode, action)`` — adds ``committed_against_contested``
    ONLY when ``consensus == "contested"`` AND ``action in ("BUY", "SELL")``.

The strategy draws ``consensus`` from ``DEBATE_CONSENSUS_VALUES``
(strong_agree / lean / contested) and ``action`` from BUY / SELL / HOLD. Setting
``decision["action"]`` makes the resolved action match the intended action (the
DEBATE-mode resolver reads ``decision["action"]``, not ``manual_trade``). The
real LLM / tool server are never invoked; an empty message history is enough
because the contested flag depends only on the threaded debate consensus and the
committed action. The sys.path / import pattern mirrors the sibling
``test_debate_*`` / ``test_defensibility_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py / debate.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import build_defensibility_record  # noqa: E402
from debate import DEBATE_CONSENSUS_VALUES  # noqa: E402

_ACTIONS = ("BUY", "SELL", "HOLD")
_DIRECTIONAL = ("BUY", "SELL")


@st.composite
def _debate_decision(draw):
    """A DEBATE-mode committed decision carrying a threaded ``_debate`` verdict.

    ``decision["action"]`` is set so the DEBATE-mode resolver derives exactly the
    intended BUY / SELL / HOLD action, and ``_debate.consensus`` is drawn from the
    canonical Debate_Consensus values. Optional bull/bear stance strengths are
    included to exercise the basis-statement read path without affecting the
    contested flag.
    """
    consensus = draw(st.sampled_from(DEBATE_CONSENSUS_VALUES))
    action = draw(st.sampled_from(_ACTIONS))

    def _stance():
        # Either a present stance with a strength, or an unavailable stance.
        if draw(st.booleans()):
            return {
                "available": True,
                "strength": draw(st.integers(min_value=0, max_value=10)),
            }
        return {"available": False}

    decision = {
        "action": action,
        "_debate": {
            "bull_stance": _stance(),
            "bear_stance": _stance(),
            "consensus": consensus,
            "conviction": draw(st.integers(min_value=0, max_value=10)),
        },
    }
    return decision, consensus, action


@settings(max_examples=100)
@given(_debate_decision())
def test_contested_but_committed_trades_are_flagged(case):
    """Property 19: contested + directional commit ⇒ explicit flag; else absent."""
    decision, consensus, action = case

    record = build_defensibility_record([], decision, mode="DEBATE")

    # The debate entry is always present for a DEBATE-mode decision carrying a
    # threaded ``_debate`` verdict.
    assert "debate" in record
    debate = record["debate"]

    if consensus == "contested" and action in _DIRECTIONAL:
        # A directional trade committed against a contested debate is flagged with
        # an explicit, non-empty statement (R7.4).
        assert "committed_against_contested" in debate
        statement = debate["committed_against_contested"]
        assert isinstance(statement, str)
        assert statement.strip() != ""
    else:
        # Non-contested consensus, OR a HOLD action ⇒ no flag (R7.4).
        assert "committed_against_contested" not in debate
