"""Property-based test for non-DEBATE routing invariance (graph.py, task 5.2).

Feature: multi-agent-debate

This module implements design **Property 6: Non-DEBATE modes are unaffected and
never enter the debate**:

    For ANY mode value that is not "DEBATE" (including "FIND", "VERIFY", "QA",
    lowercase variants, whitespace-padded variants, and arbitrary strings),
    ``route_entry`` selects the SAME target as the legacy routing (QA ->
    "qa_agent", everything else -> "agent"), and the DEBATE research branch
    ("research" == ``DEBATE_RESEARCH_ENTRY``) is returned ONLY for the DEBATE
    mode.

Validates: Requirements 1.3, 1.4, 5.4.

``route_entry`` normalizes the mode via ``(state.get("mode") or "").strip()
.upper()`` before branching, so the strategy deliberately mixes canonical
values, case variants, whitespace padding, and arbitrary random text to exercise
the normalization and prove the DEBATE research branch is reachable by — and
only by — ``mode`` normalizing to "DEBATE".

The sys.path / import pattern mirrors the sibling ``test_loop_routing`` and
``test_debate_*`` modules. Importing ``graph`` constructs an LLM client object
at import time but performs no network I/O, so a plain import is safe here and
mirrors ``test_loop_routing``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import (  # noqa: E402
    DEBATE_MODE,
    DEBATE_RESEARCH_ENTRY,
    QA_MODE,
    route_entry,
)


def _legacy_target(mode):
    """The pre-debate legacy routing target for a normalized mode.

    QA -> "qa_agent"; every other (non-DEBATE) mode -> "agent".
    """
    return "qa_agent" if mode == QA_MODE else "agent"


# A blend of canonical mode values, case variants, whitespace-padded forms, and
# arbitrary random text so normalization (strip + upper) is fully exercised and
# DEBATE is shown to be the only trigger for the research branch.
_mode_strategy = st.one_of(
    st.sampled_from(
        [
            "FIND",
            "VERIFY",
            "QA",
            "DEBATE",
            "find",
            "verify",
            "qa",
            "debate",
            "  FIND  ",
            "\tVERIFY\n",
            "  qa ",
            " Debate ",
            "DeBaTe",
            "",
            "   ",
            "FINDING",
            "DEBATER",
            "PREDEBATE",
        ]
    ),
    st.text(max_size=24),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 6: Non-DEBATE modes are unaffected and never enter the debate
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 6: Non-DEBATE modes are unaffected and never enter the debate
@settings(max_examples=100, deadline=None)
@given(mode=_mode_strategy)
def test_property_6_non_debate_routing_invariance(mode):
    """Validates: Requirements 1.3, 1.4, 5.4

    For any mode value, ``route_entry`` agrees with the legacy routing for every
    non-DEBATE mode (QA -> "qa_agent", else -> "agent"), and returns the DEBATE
    research branch (``DEBATE_RESEARCH_ENTRY`` == "research") if and only if the
    mode normalizes to "DEBATE".
    """
    normalized = (mode or "").strip().upper()
    result = route_entry({"mode": mode})

    if normalized == QA_MODE:
        assert result == "qa_agent"
    elif normalized == DEBATE_MODE:
        assert result == DEBATE_RESEARCH_ENTRY  # "research"
    else:
        # Every other (non-DEBATE) mode follows the byte-identical legacy path.
        assert result == _legacy_target(normalized)

    # The DEBATE research branch is reachable ONLY for the DEBATE mode: no
    # non-DEBATE mode ever enters the debate (R1.4, R5.4).
    if result == DEBATE_RESEARCH_ENTRY:
        assert normalized == DEBATE_MODE
    if normalized != DEBATE_MODE:
        assert result != DEBATE_RESEARCH_ENTRY
        # Non-DEBATE routing is exactly the legacy target.
        assert result == _legacy_target(normalized)
