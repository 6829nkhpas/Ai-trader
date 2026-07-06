# Feature: multi-agent-debate (task 15.2): DEBATE acceptance and routing
"""Example (unit) tests for DEBATE acceptance and entry routing.

Feature: multi-agent-debate

These concrete example cases verify the two user-facing acceptance criteria for
introducing the DEBATE analysis mode:

  * A ``DEBATE`` request is *accepted* by the service request model — i.e.
    ``main.RunRequest`` constructs without error for ``mode="DEBATE"`` and
    round-trips the value (R1.1).
  * ``route_entry`` selects the research/debate branch for a DEBATE run while
    leaving the FIND / VERIFY / QA branches byte-identical to the legacy
    routing (R1.2).

Validates: Requirements 1.1, 1.2.

Unlike the sibling property test (``test_debate_non_debate_routing_invariance``)
which sweeps the input space, these are fixed, readable examples that pin the
exact routing targets and the request-model acceptance contract.

The sys.path / import pattern mirrors the sibling ``test_loop_routing`` and
``test_debate_*`` modules. Importing ``graph`` (transitively imported by
``main``) constructs an LLM client object at import time but performs no network
I/O, so a plain import is safe here.
"""

import os
import sys

# Make the service package importable (graph.py and main.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import (  # noqa: E402
    DEBATE_MODE,
    DEBATE_RESEARCH_ENTRY,
    route_entry,
)
import main  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# route_entry: DEBATE selects the research/debate branch (R1.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_route_entry_debate_selects_research_branch():
    """Validates: Requirements 1.2

    A DEBATE-mode request routes to the distinct research entry string, which
    the compiled graph maps onto the shared analysis loop's ``agent`` node.
    """
    assert route_entry({"mode": "DEBATE"}) == DEBATE_RESEARCH_ENTRY
    # The research entry string is the literal "research".
    assert DEBATE_RESEARCH_ENTRY == "research"
    assert DEBATE_MODE == "DEBATE"


# ─────────────────────────────────────────────────────────────────────────────
# route_entry: FIND / VERIFY / QA branches are unchanged (R1.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_route_entry_find_uses_agent():
    """Validates: Requirements 1.2 — FIND keeps the legacy analysis entry."""
    assert route_entry({"mode": "FIND"}) == "agent"


def test_route_entry_verify_uses_agent():
    """Validates: Requirements 1.2 — VERIFY keeps the legacy analysis entry."""
    assert route_entry({"mode": "VERIFY"}) == "agent"


def test_route_entry_qa_uses_qa_agent():
    """Validates: Requirements 1.2 — QA keeps routing to the Q&A handler."""
    assert route_entry({"mode": "QA"}) == "qa_agent"


def test_route_entry_branches_are_distinct():
    """Validates: Requirements 1.2

    The DEBATE research branch is distinct from every legacy branch, so adding
    DEBATE does not collide with FIND / VERIFY / QA routing.
    """
    find_target = route_entry({"mode": "FIND"})
    verify_target = route_entry({"mode": "VERIFY"})
    qa_target = route_entry({"mode": "QA"})
    debate_target = route_entry({"mode": "DEBATE"})

    # FIND and VERIFY share the legacy analysis entry; QA and DEBATE are each
    # their own distinct target.
    assert find_target == verify_target == "agent"
    assert qa_target == "qa_agent"
    assert debate_target == DEBATE_RESEARCH_ENTRY
    assert len({find_target, qa_target, debate_target}) == 3


# ─────────────────────────────────────────────────────────────────────────────
# RunRequest: a DEBATE request is accepted (R1.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_request_accepts_debate_mode():
    """Validates: Requirements 1.1

    The service request model accepts ``mode="DEBATE"`` and round-trips it,
    so a DEBATE run can be submitted to ``/run``.
    """
    req = main.RunRequest(thread_id="t", message="m", mode="DEBATE")
    assert req.mode == "DEBATE"
    assert req.thread_id == "t"
    assert req.message == "m"


def test_run_request_defaults_to_find():
    """Validates: Requirements 1.1

    Omitting ``mode`` falls back to the legacy FIND default, so existing
    callers are unaffected by the addition of DEBATE.
    """
    req = main.RunRequest(thread_id="t", message="m")
    assert req.mode == "FIND"


# ─────────────────────────────────────────────────────────────────────────────
# Compiled graph: the debate roles are registered (optional)
# ─────────────────────────────────────────────────────────────────────────────

def test_graph_registers_debate_role_nodes():
    """Validates: Requirements 1.2

    The compiled graph exposes the Bull / Bear / Judge debate role nodes, so a
    DEBATE run that hands off from the research phase has somewhere to go.
    """
    from graph import graph

    node_names = set(graph.get_graph().nodes)
    for role in ("bull", "bear", "judge"):
        assert role in node_names, f"expected debate node {role!r} in {node_names}"
