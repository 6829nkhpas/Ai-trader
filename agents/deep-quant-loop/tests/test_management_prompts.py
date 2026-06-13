"""Unit tests for Trade Management prompt content (task 14.2).

Feature: trade-management

Example-based unit tests asserting the agent system prompts
(`DEEP_QUANT_SYSTEM_PROMPT` and `RISK_MANAGER_PROMPT` in `graph.py`) carry the
trade-management instructions required by Requirements 8.1 - 8.4. They run with
no live LLM, no live Rust server, and no Hypothesis.

Checks are deliberately robust to minor wording changes: they look for stable
anchor tokens (e.g. "management plan", "scale-out" / "scale out", "breakeven",
"blended", "trailing", "fraction") in the relevant prompt rather than matching
whole sentences verbatim.

Requirement 8.1: DEEP_QUANT_SYSTEM_PROMPT instructs the agent, when declaring a
    directional trade, to provide a Management_Plan with at least a scale-out
    target and a breakeven move, in addition to the entry and initial stop.
Requirement 8.2: DEEP_QUANT_SYSTEM_PROMPT self_verification_protocol instructs
    confirming the plan's leg fractions, ordering, breakeven trigger, and
    blended reward-to-risk before committing.
Requirement 8.3: DEEP_QUANT_SYSTEM_PROMPT setup_validation_disclosure instructs
    stating the scale-out targets, fractions, breakeven trigger, and trailing
    rule in the setup_validation.
Requirement 8.4: VERIFY-mode RISK_MANAGER_PROMPT instructs evaluating a
    user-proposed trade's management (or its absence) and recommending
    partials / breakeven / trailing where appropriate.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402

# Case-insensitive haystacks reused across assertions.
_SYS = graph.DEEP_QUANT_SYSTEM_PROMPT.lower()
_RISK = graph.RISK_MANAGER_PROMPT.lower()


def _has_scale_out(text_lower: str) -> bool:
    """True if the text mentions a scale-out (hyphenated or spaced)."""
    return "scale-out" in text_lower or "scale out" in text_lower


# ── Requirement 8.1: management-plan instruction in the system prompt ────────
def test_system_prompt_instructs_management_plan_with_scale_out_and_breakeven():
    """Validates: Requirements 8.1"""
    # The agent is told to provide a Management_Plan for directional trades.
    assert "management plan" in _SYS
    # ... with at least a scale-out target ...
    assert _has_scale_out(_SYS)
    # ... and a breakeven move ...
    assert "breakeven" in _SYS
    # ... in addition to the entry and the initial stop.
    assert "entry" in _SYS
    assert "stop" in _SYS


# ── Requirement 8.2: self-verification items for the plan ────────────────────
def test_system_prompt_self_verification_items_for_management_plan():
    """Validates: Requirements 8.2"""
    # Leg fractions in range / summing constraint.
    assert "fraction" in _SYS
    # Target ordering on the profit side.
    assert "order" in _SYS  # matches "ordered" / "ordering" / "non-decreasing order"
    # Breakeven trigger placement.
    assert "breakeven" in _SYS
    # Blended (fraction-weighted) reward-to-risk.
    assert "blended" in _SYS


# ── Requirement 8.3: setup-validation disclosure of plan components ──────────
def test_system_prompt_setup_validation_discloses_plan_components():
    """Validates: Requirements 8.3"""
    # The disclosure block names the scale-out targets and their fractions ...
    assert _has_scale_out(_SYS)
    assert "fraction" in _SYS
    # ... the breakeven trigger ...
    assert "breakeven" in _SYS
    # ... and the trailing rule.
    assert "trailing" in _SYS or "trail" in _SYS


# ── Requirement 8.4: VERIFY-mode management-evaluation guidance ──────────────
def test_risk_manager_prompt_evaluates_and_recommends_management():
    """Validates: Requirements 8.4"""
    # The risk manager evaluates the proposed trade's management (or its absence).
    assert "management" in _RISK
    # It recommends partials / scale-out where appropriate ...
    assert _has_scale_out(_RISK) or "partial" in _RISK
    # ... a breakeven move ...
    assert "breakeven" in _RISK
    # ... and a trailing stop.
    assert "trailing" in _RISK or "trail" in _RISK
