"""Unit tests for Regime Gate prompt content (task 9.2).

Feature: regime-detection-gate

These are example-based unit tests that assert the agent system prompts
(`DEEP_QUANT_SYSTEM_PROMPT` and `RISK_MANAGER_PROMPT` in `graph.py`) carry the
Regime Gate instructions required by Requirements 6.1 - 6.7. They run with no
live LLM, no live Rust server, and no Hypothesis.

Checks are deliberately robust to minor wording changes: they look for stable
anchor tokens (the tool name `get_market_regime`, the regime field names
`trend_state` / `volatility_state` / `favorability`, the unfavorable-handling
verbs `conviction` / `wait` / `HOLD`, and an "unavailable ... proceed" pairing)
rather than matching whole sentences verbatim.

Requirement 6.1: DEEP_QUANT_SYSTEM_PROMPT order_of_operations instructs calling
    `get_market_regime` for the symbol/timeframe under analysis.
Requirement 6.2: DEEP_QUANT_SYSTEM_PROMPT self_verification_protocol instructs
    checking Favorability before a directional (BUY/SELL, not HOLD) trade.
Requirement 6.3: DEEP_QUANT_SYSTEM_PROMPT instructs that an `unfavorable`
    regime requires one of: lower conviction, wait, or HOLD.
Requirement 6.4: DEEP_QUANT_SYSTEM_PROMPT setup_validation_disclosure instructs
    stating trend_state, volatility_state, and favorability.
Requirement 6.5: RISK_MANAGER_PROMPT instructs consulting `get_market_regime`
    while verifying a user-proposed trade.
Requirement 6.6: RISK_MANAGER_PROMPT instructs an explicit warning when a
    user-proposed trade is taken in an `unfavorable` regime.
Requirement 6.7: Both prompts instruct noting the regime as unavailable and
    proceeding (not blocking) when the regime is unavailable.
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
    proceed / continue / do not block / not block within ~240 chars of an
    'unavailable' mention.
    """
    for m in re.finditer(r"unavailable", text_lower):
        window = text_lower[m.start(): m.start() + 240]
        if re.search(r"proceed|continue|do not (?:abort|block)|not block|do not block", window):
            return True
    return False


# ── Requirement 6.1: order_of_operations calls get_market_regime ─────────────
def test_system_prompt_instructs_calling_get_market_regime():
    """Validates: Requirements 6.1"""
    # The tool name must appear in the system prompt so the agent is told to
    # call it for the symbol/timeframe under analysis.
    assert "get_market_regime" in _SYS
    # Tied to the timeframe under analysis (regime is a per-timeframe call).
    assert "timeframe" in _SYS


# ── Requirement 6.2: check favorability before a directional trade ───────────
def test_system_prompt_instructs_favorability_check_before_directional_trade():
    """Validates: Requirements 6.2"""
    assert "favorability" in _SYS
    # Directional-trade framing: references BUY/SELL and excludes HOLD.
    assert "buy" in _SYS and "sell" in _SYS
    assert "hold" in _SYS


# ── Requirement 6.3: unfavorable -> lower conviction / wait / HOLD ───────────
def test_system_prompt_instructs_unfavorable_handling_actions():
    """Validates: Requirements 6.3"""
    assert "unfavorable" in _SYS
    # All three permitted responses must be present as guidance.
    assert "conviction" in _SYS
    assert "wait" in _SYS
    assert "hold" in _SYS


# ── Requirement 6.4: disclosure states trend/volatility/favorability ─────────
def test_system_prompt_instructs_regime_disclosure_fields():
    """Validates: Requirements 6.4"""
    assert "trend_state" in _SYS
    assert "volatility_state" in _SYS
    assert "favorability" in _SYS


# ── Requirement 6.5: RISK_MANAGER_PROMPT consults get_market_regime ──────────
def test_risk_manager_prompt_consults_get_market_regime():
    """Validates: Requirements 6.5"""
    assert "get_market_regime" in _RISK


# ── Requirement 6.6: RISK_MANAGER_PROMPT warns on unfavorable regime ─────────
def test_risk_manager_prompt_instructs_unfavorable_warning():
    """Validates: Requirements 6.6"""
    assert "unfavorable" in _RISK
    assert "warning" in _RISK


# ── Requirement 6.7: both prompts: unavailable -> note and proceed ───────────
def test_system_prompt_unavailable_and_proceed():
    """Validates: Requirements 6.7"""
    assert _has_unavailable_and_proceed(_SYS)


def test_risk_manager_prompt_unavailable_and_proceed():
    """Validates: Requirements 6.7"""
    assert _has_unavailable_and_proceed(_RISK)
