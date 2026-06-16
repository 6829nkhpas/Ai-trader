"""Unit tests for session prompt content (graph.py, task 10.2).

Feature: session-expiry-awareness

These plain pytest unit tests assert that the agent system prompts carry the
session-awareness instructions required by Requirement 7:

  * DEEP_QUANT_SYSTEM_PROMPT must instruct the agent to call
    ``get_session_context`` for the symbol/timeframe under analysis (R7.1),
    check Time_Favorability before a directional BUY/SELL trade (R7.2), take
    exactly one of lower-conviction / wait / HOLD when the window is
    ``unfavorable`` (R7.3), disclose the Session_Phase / Expiry_Context /
    Time_Favorability in its setup_validation (R7.4), and note the session as
    unavailable and proceed when it cannot be computed (R7.6).
  * RISK_MANAGER_PROMPT must instruct the agent to consult
    ``get_session_context`` while verifying (R7.5), include an explicit warning
    on an ``unfavorable`` window (R7.5), and note-as-unavailable-and-proceed
    (R7.6).

Checks are tolerant (case-insensitive substring tests keyed on stable tokens)
so they survive prose edits while still pinning the required content.
RISK_MANAGER_PROMPT is a ``str.format`` template carrying ``{placeholders}`` —
the raw string is asserted on directly (never ``.format()``-ed).

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules.

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import DEEP_QUANT_SYSTEM_PROMPT, RISK_MANAGER_PROMPT  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _lower(text: str) -> str:
    return text.lower()


DQ = _lower(DEEP_QUANT_SYSTEM_PROMPT)
RM = _lower(RISK_MANAGER_PROMPT)


# ── DEEP_QUANT_SYSTEM_PROMPT (R7.1–R7.4, R7.6) ───────────────────────────────

def test_deep_quant_prompt_instructs_calling_get_session_context():
    """R7.1: order_of_operations tells the agent to call get_session_context."""
    assert "get_session_context" in DQ


def test_deep_quant_prompt_checks_time_favorability_before_directional_trade():
    """R7.2: self_verification checks Time_Favorability before a BUY/SELL."""
    assert "time_favorability" in DQ
    # The directional gate is keyed on BUY or SELL (excluding HOLD).
    assert "buy" in DQ and "sell" in DQ
    assert "directional" in DQ


def test_deep_quant_prompt_unfavorable_one_of_lower_wait_hold():
    """R7.3: when unfavorable, take exactly one of lower-conviction / wait / HOLD."""
    assert "unfavorable" in DQ
    # The one-of remediation options must all be present.
    assert "conviction" in DQ          # lower the conviction score
    assert "wait" in DQ                # wait for a better window
    assert "hold" in DQ                # or HOLD


def test_deep_quant_prompt_setup_validation_discloses_session_fields():
    """R7.4: setup_validation discloses Session_Phase / Expiry_Context / Favorability."""
    assert "session_phase" in DQ
    assert "expiry_context" in DQ
    assert "time_favorability" in DQ


def test_deep_quant_prompt_notes_unavailable_and_proceeds():
    """R7.6: when the session context is unavailable, note it and proceed."""
    assert "unavailable" in DQ
    assert "proceed" in DQ


# ── RISK_MANAGER_PROMPT (R7.5, R7.6) ─────────────────────────────────────────

def test_risk_manager_prompt_consults_get_session_context():
    """R7.5: VERIFY-mode prompt consults get_session_context while verifying."""
    assert "get_session_context" in RM


def test_risk_manager_prompt_warns_on_unfavorable_window():
    """R7.5: include an explicit warning on an unfavorable time window."""
    assert "unfavorable" in RM
    assert "warning" in RM
    # The warning is about the time window specifically.
    assert "time window" in RM or "time_favorability" in RM


def test_risk_manager_prompt_notes_unavailable_and_proceeds():
    """R7.6: when the session context is unavailable, note it and proceed."""
    assert "unavailable" in RM
    assert "proceed" in RM


def test_risk_manager_prompt_is_raw_template_with_placeholders():
    """RISK_MANAGER_PROMPT is a format template — asserted raw, never .format()-ed."""
    # Sanity guard: the template still carries its named placeholders, confirming
    # we are testing the un-formatted source string.
    assert "{symbol}" in RISK_MANAGER_PROMPT
    assert "{side}" in RISK_MANAGER_PROMPT
