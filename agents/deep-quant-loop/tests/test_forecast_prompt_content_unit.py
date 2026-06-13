"""Unit tests for Volatility-Aware Forecaster prompt content (task 10.2).

Feature: volatility-aware-forecaster

These are example-based unit tests that assert the agent system prompts
(`DEEP_QUANT_SYSTEM_PROMPT` and `RISK_MANAGER_PROMPT` in `graph.py`) carry the
forecast-integration instructions required by Requirements 8.1 - 8.6. They run
with no live LLM, no live Rust server, and no Hypothesis.

Checks are deliberately robust to minor wording changes: they look for stable
anchor tokens (the tool name `get_forecast`, the secondary tool `get_prediction`,
the forecast field names `forecast_alignment` / `up_probability` /
`projected_direction` / `expected_move_atr`, the "primary predictive cross-check"
/ "secondary input" framing, the probability thresholds, the misaligned-handling
verbs `conviction` / `wait` / `HOLD`, the VERIFY-mode warning phrase
`misaligned with the volatility-aware forecast`, and an "unavailable ... proceed"
pairing) rather than matching whole sentences verbatim.

Requirement 8.1: DEEP_QUANT_SYSTEM_PROMPT order_of_operations instructs calling
    `get_forecast` as the primary predictive cross-check while retaining
    `get_prediction` as a secondary input.
Requirement 8.2: DEEP_QUANT_SYSTEM_PROMPT self_verification_protocol instructs
    checking Forecast_Alignment and Up_Probability before a directional
    (BUY/SELL, not HOLD) trade.
Requirement 8.3: DEEP_QUANT_SYSTEM_PROMPT instructs that a `misaligned`
    forecast (or an unsupportive Up_Probability) requires one of: lower
    conviction, wait, or HOLD.
Requirement 8.4: DEEP_QUANT_SYSTEM_PROMPT setup_validation_disclosure instructs
    stating Projected_Direction, Up_Probability, Expected_Move_ATR, and
    Forecast_Alignment.
Requirement 8.5: RISK_MANAGER_PROMPT instructs consulting `get_forecast` while
    verifying and warning on a misaligned directional trade.
Requirement 8.6: Both prompts instruct noting the forecast as unavailable and
    proceeding (not blocking) when it is unavailable.
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


# ── Requirement 8.1: primary cross-check call, get_prediction kept secondary ──
def test_system_prompt_instructs_get_forecast_primary_cross_check():
    """Validates: Requirements 8.1"""
    # The forecast tool must be called for the symbol/timeframe under analysis.
    assert "get_forecast" in _SYS
    # It is the PRIMARY predictive cross-check.
    assert "primary predictive cross-check" in _SYS
    # get_prediction is retained as a SECONDARY input.
    assert "get_prediction" in _SYS
    assert "secondary input" in _SYS
    # Tied to the timeframe under analysis (forecast is a per-timeframe call).
    assert "timeframe" in _SYS


# ── Requirement 8.2: check alignment/probability before a directional trade ──
def test_system_prompt_instructs_alignment_probability_check_before_directional_trade():
    """Validates: Requirements 8.2"""
    assert "forecast_alignment" in _SYS
    assert "up_probability" in _SYS
    # Directional-trade framing: references BUY/SELL and excludes HOLD.
    assert "buy" in _SYS and "sell" in _SYS
    assert "hold" in _SYS
    # The probability-support thresholds are stated for the two directions.
    assert "up_probability >= 0.5" in _SYS
    assert "up_probability <= 0.5" in _SYS


# ── Requirement 8.3: misaligned / unsupportive -> lower conviction / wait / HOLD
def test_system_prompt_instructs_misaligned_handling_actions():
    """Validates: Requirements 8.3"""
    assert "misaligned" in _SYS
    # All three permitted responses must be present as guidance.
    assert "conviction" in _SYS
    assert "wait" in _SYS
    assert "hold" in _SYS


# ── Requirement 8.4: setup_validation discloses direction/prob/move/alignment ─
def test_system_prompt_instructs_forecast_disclosure_fields():
    """Validates: Requirements 8.4"""
    # The setup_validation disclosure block is anchored by a "FORECAST:" label.
    assert "forecast:" in _SYS
    assert "projected_direction" in _SYS
    assert "up_probability" in _SYS
    assert "expected_move_atr" in _SYS
    assert "forecast_alignment" in _SYS


# ── Requirement 8.5: RISK_MANAGER_PROMPT consults get_forecast ───────────────
def test_risk_manager_prompt_consults_get_forecast():
    """Validates: Requirements 8.5"""
    assert "get_forecast" in _RISK


# ── Requirement 8.5: RISK_MANAGER_PROMPT warns on misaligned trade ───────────
def test_risk_manager_prompt_instructs_misaligned_warning():
    """Validates: Requirements 8.5"""
    assert "warning" in _RISK
    # The explicit VERIFY-mode warning statement names the volatility-aware forecast.
    assert "misaligned with the volatility-aware forecast" in _RISK


# ── Requirement 8.6: both prompts: unavailable -> note and proceed ───────────
def test_system_prompt_unavailable_and_proceed():
    """Validates: Requirements 8.6"""
    assert _has_unavailable_and_proceed(_SYS)


def test_risk_manager_prompt_unavailable_and_proceed():
    """Validates: Requirements 8.6"""
    assert _has_unavailable_and_proceed(_RISK)
