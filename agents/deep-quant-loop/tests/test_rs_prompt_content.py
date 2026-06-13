"""Unit tests for Relative Strength & Index Context prompt content (task 9.2).

Feature: relative-strength-context

These are example-based unit tests that assert the agent system prompts
(`DEEP_QUANT_SYSTEM_PROMPT` and `RISK_MANAGER_PROMPT` in `graph.py`) carry the
relative-strength instructions required by Requirements 7.1 - 7.6. They run
with no live LLM, no live Rust server, and no Hypothesis.

Checks are deliberately robust to minor wording changes: they look for stable
anchor tokens (the tool name `get_relative_strength`, the relative-strength
field names `index_direction` / `relative_strength_state` / `alignment`, the
misaligned-handling verbs `conviction` / `wait` / `HOLD`, the `warning` keyword,
and an "unavailable ... proceed" pairing) rather than matching whole sentences
verbatim.

Requirement 7.1: DEEP_QUANT_SYSTEM_PROMPT order_of_operations instructs calling
    `get_relative_strength` for the symbol/timeframe under analysis.
Requirement 7.2: DEEP_QUANT_SYSTEM_PROMPT instructs checking
    Index_Direction / Relative_Strength_State / Alignment before a directional
    (BUY/SELL, not HOLD) trade.
Requirement 7.3: DEEP_QUANT_SYSTEM_PROMPT instructs that a `misaligned`
    relative-strength context requires one of: lower conviction, wait, or HOLD.
Requirement 7.4: DEEP_QUANT_SYSTEM_PROMPT setup_validation_disclosure instructs
    stating index_direction, relative_strength_state, and alignment.
Requirement 7.5: RISK_MANAGER_PROMPT instructs consulting `get_relative_strength`
    while verifying and warning on a misaligned directional trade.
Requirement 7.6: Both prompts instruct noting relative strength as unavailable
    and proceeding (not blocking) when it is unavailable.
"""

import os
import re
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import (  # noqa: E402
    DEEP_QUANT_SYSTEM_PROMPT,
    RISK_MANAGER_PROMPT,
)

# Case-insensitive haystacks reused across assertions.
_SYS = DEEP_QUANT_SYSTEM_PROMPT.lower()
_RISK = RISK_MANAGER_PROMPT.lower()


def _has_unavailable_and_proceed(text_lower: str) -> bool:
    """True if `text_lower` pairs an "unavailable" notion with a "proceed"
    (non-blocking) notion reasonably close together. Robust to wording: accepts
    proceed / continue / do not block / not block within ~260 chars of an
    'unavailable' mention.
    """
    for m in re.finditer(r"unavailable", text_lower):
        window = text_lower[m.start(): m.start() + 260]
        if re.search(r"proceed|continue|do not (?:abort|block)|not block|do not block", window):
            return True
    return False


# ── Requirement 7.1: order_of_operations calls get_relative_strength ─────────
def test_system_prompt_instructs_calling_get_relative_strength():
    """Validates: Requirements 7.1"""
    # The tool name must appear in the system prompt so the agent is told to
    # call it for the symbol/timeframe under analysis.
    assert "get_relative_strength" in _SYS
    # Tied to the timeframe under analysis (relative strength is a per-timeframe call).
    assert "timeframe" in _SYS


# ── Requirement 7.2: check alignment before a directional trade ──────────────
def test_system_prompt_instructs_alignment_check_before_directional_trade():
    """Validates: Requirements 7.2"""
    assert "index_direction" in _SYS
    assert "relative_strength_state" in _SYS
    assert "alignment" in _SYS
    # Directional-trade framing: references BUY/SELL and excludes HOLD.
    assert "buy" in _SYS and "sell" in _SYS
    assert "hold" in _SYS


# ── Requirement 7.3: misaligned -> lower conviction / wait / HOLD ────────────
def test_system_prompt_instructs_misaligned_handling_actions():
    """Validates: Requirements 7.3"""
    assert "misaligned" in _SYS
    # All three permitted responses must be present as guidance.
    assert "conviction" in _SYS
    assert "wait" in _SYS
    assert "hold" in _SYS


# ── Requirement 7.4: disclosure states index/relative-strength/alignment ─────
def test_system_prompt_instructs_relative_strength_disclosure_fields():
    """Validates: Requirements 7.4"""
    assert "index_direction" in _SYS
    assert "relative_strength_state" in _SYS
    assert "alignment" in _SYS


# ── Requirement 7.5: RISK_MANAGER_PROMPT consults get_relative_strength ───────
def test_risk_manager_prompt_consults_get_relative_strength():
    """Validates: Requirements 7.5"""
    assert "get_relative_strength" in _RISK


# ── Requirement 7.5: RISK_MANAGER_PROMPT warns on misaligned trade ───────────
def test_risk_manager_prompt_instructs_misaligned_warning():
    """Validates: Requirements 7.5"""
    assert "misaligned" in _RISK
    assert "warning" in _RISK


# ── Requirement 7.6: both prompts: unavailable -> note and proceed ───────────
def test_system_prompt_unavailable_and_proceed():
    """Validates: Requirements 7.6"""
    assert _has_unavailable_and_proceed(_SYS)


def test_risk_manager_prompt_unavailable_and_proceed():
    """Validates: Requirements 7.6"""
    assert _has_unavailable_and_proceed(_RISK)
