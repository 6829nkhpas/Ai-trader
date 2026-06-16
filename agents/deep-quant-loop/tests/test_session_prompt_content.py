"""Unit tests for Session & Expiry Awareness prompt content (task 10.2).

Feature: session-expiry-awareness

These are example-based unit tests that assert the agent system prompts
(`DEEP_QUANT_SYSTEM_PROMPT` and `RISK_MANAGER_PROMPT` in `graph.py`) carry the
session-integration instructions required by Requirements 7.1 - 7.6. They run
with no live LLM, no live Rust server, and no Hypothesis.

Checks are deliberately robust to minor wording changes: they look for stable
anchor tokens (the tool name `get_session_context`, the session field names
`session_phase` / `expiry_context` / `time_favorability`, the directional
BUY/SELL/HOLD framing, the unfavorable-handling verbs `conviction` / `wait` /
`HOLD`, the VERIFY-mode warning phrase `unfavorable time window`, and an
"unavailable ... proceed" pairing) rather than matching whole sentences
verbatim.

Requirement 7.1: DEEP_QUANT_SYSTEM_PROMPT order_of_operations instructs calling
    `get_session_context` for the symbol and timeframe under analysis.
Requirement 7.2: DEEP_QUANT_SYSTEM_PROMPT self_verification_protocol instructs
    checking Time_Favorability before a directional (BUY/SELL, not HOLD) trade.
Requirement 7.3: DEEP_QUANT_SYSTEM_PROMPT instructs that an `unfavorable` time
    window requires exactly one of: lower conviction, wait, or HOLD.
Requirement 7.4: DEEP_QUANT_SYSTEM_PROMPT setup_validation_disclosure instructs
    stating Session_Phase, Expiry_Context, and Time_Favorability.
Requirement 7.5: RISK_MANAGER_PROMPT instructs consulting `get_session_context`
    while verifying and warning on an unfavorable-window directional trade.
Requirement 7.6: Both prompts instruct noting the session context as unavailable
    and proceeding (not blocking) when it is unavailable.
"""

import os
import re
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402

# Case-insensitive haystacks reused across assertions.
_SYS = graph.DEEP_QUANT_SYSTEM_PROMPT.lower()
_RISK = graph.RISK_MANAGER_PROMPT.lower()


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


# ── Requirement 7.1: call get_session_context for symbol/timeframe ───────────
def test_system_prompt_instructs_calling_get_session_context():
    """Validates: Requirements 7.1"""
    # The session tool must be called during analysis.
    assert "get_session_context" in _SYS
    # Tied to the timeframe under analysis (session is a per-timeframe call).
    assert "timeframe" in _SYS


# ── Requirement 7.2: check favorability before a directional trade ───────────
def test_system_prompt_instructs_favorability_check_before_directional_trade():
    """Validates: Requirements 7.2"""
    assert "time_favorability" in _SYS
    # Directional-trade framing: references BUY/SELL and excludes HOLD.
    assert "buy" in _SYS and "sell" in _SYS
    assert "hold" in _SYS
    # The check is explicitly tied to a directional decision.
    assert "directional" in _SYS


# ── Requirement 7.3: unfavorable -> lower conviction / wait / HOLD ───────────
def test_system_prompt_instructs_unfavorable_handling_actions():
    """Validates: Requirements 7.3"""
    assert "unfavorable" in _SYS
    # All three permitted responses must be present as guidance.
    assert "conviction" in _SYS
    assert "wait" in _SYS
    assert "hold" in _SYS


# ── Requirement 7.4: setup_validation discloses phase/expiry/favorability ────
def test_system_prompt_instructs_session_disclosure_fields():
    """Validates: Requirements 7.4"""
    # The setup_validation disclosure block is anchored by a "SESSION" label.
    assert "session context:" in _SYS or "session:" in _SYS
    assert "session_phase" in _SYS
    assert "expiry_context" in _SYS
    assert "time_favorability" in _SYS


# ── Requirement 7.5: RISK_MANAGER_PROMPT consults get_session_context ────────
def test_risk_manager_prompt_consults_get_session_context():
    """Validates: Requirements 7.5"""
    assert "get_session_context" in _RISK


# ── Requirement 7.5: RISK_MANAGER_PROMPT warns on unfavorable window ─────────
def test_risk_manager_prompt_instructs_unfavorable_warning():
    """Validates: Requirements 7.5"""
    assert "warning" in _RISK
    # The explicit VERIFY-mode warning statement names the unfavorable window.
    assert "unfavorable time window" in _RISK
    # The warning is tied to the session fields it must state.
    assert "session_phase" in _RISK
    assert "time_favorability" in _RISK


# ── Requirement 7.6: both prompts: unavailable -> note and proceed ───────────
def test_system_prompt_unavailable_and_proceed():
    """Validates: Requirements 7.6"""
    assert _has_unavailable_and_proceed(_SYS)


def test_risk_manager_prompt_unavailable_and_proceed():
    """Validates: Requirements 7.6"""
    assert _has_unavailable_and_proceed(_RISK)
