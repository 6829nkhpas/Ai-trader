"""Unit tests for the Python Trade_Validator mirror (validator.py, task 5.2).

Feature: deep-quant-analysis-hardening

These tests mirror the Rust unit tests in
``frontend/src-tauri/src/quant/mod.rs`` so the two implementations are verified
to agree on the same inputs (R6.1–R6.5). Boundary inputs called out in the
design are covered explicitly: RR exactly 2.0 and a stop distance exactly at
1.5×ATR both pass.
"""

import math
import os
import sys

# Make the service package importable (validator.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    ValidatorReason,
    validate_trade,
)


def _levels(entry, sl, tp):
    return ExecutionLevels(entry=entry, stop_loss=sl, take_profit=tp)


# ── HOLD bypasses all level checks (R6) ──────────────────────────────────────

def test_hold_passes_without_levels():
    outcome = validate_trade(Action.HOLD, None, None)
    assert outcome.is_pass()
    assert outcome.risk_reward == 0.0


def test_hold_passes_even_with_inconsistent_levels():
    # HOLD bypasses checks entirely, even with "inconsistent" levels.
    assert validate_trade(Action.HOLD, _levels(100.0, 200.0, 50.0), 10.0).is_pass()


# ── MissingLevels (R6.1) ─────────────────────────────────────────────────────

def test_missing_levels_when_none():
    outcome = validate_trade(Action.BUY, None, None)
    assert not outcome.is_pass()
    assert outcome.reason == ValidatorReason.MISSING_LEVELS


def test_non_finite_price_counts_as_missing():
    outcome = validate_trade(Action.SELL, _levels(100.0, math.nan, 90.0), None)
    assert outcome.reason == ValidatorReason.MISSING_LEVELS


def test_infinite_price_counts_as_missing():
    outcome = validate_trade(Action.BUY, _levels(100.0, 90.0, math.inf), None)
    assert outcome.reason == ValidatorReason.MISSING_LEVELS


# ── Direction consistency (R6.4 / R6.5) ──────────────────────────────────────

def test_buy_direction_consistency_enforced():
    # Valid BUY: stop below entry, target above.
    assert validate_trade(Action.BUY, _levels(100.0, 90.0, 130.0), None).is_pass()
    # Stop above entry — inconsistent.
    assert (
        validate_trade(Action.BUY, _levels(100.0, 110.0, 130.0), None).reason
        == ValidatorReason.DIRECTION_INCONSISTENT
    )
    # Target below entry — inconsistent.
    assert (
        validate_trade(Action.BUY, _levels(100.0, 90.0, 95.0), None).reason
        == ValidatorReason.DIRECTION_INCONSISTENT
    )


def test_sell_direction_consistency_enforced():
    # Valid SELL: stop above entry, target below. risk=10, reward=30 → 3.0
    assert validate_trade(Action.SELL, _levels(100.0, 110.0, 70.0), None).is_pass()
    # Stop below entry — inconsistent.
    assert (
        validate_trade(Action.SELL, _levels(100.0, 90.0, 70.0), None).reason
        == ValidatorReason.DIRECTION_INCONSISTENT
    )


# ── Risk-reward boundary (R6.2) ──────────────────────────────────────────────

def test_risk_reward_boundary_exactly_two_passes():
    # risk = 10, reward = 20 → RR = 2.0 exactly → passes (R6.2).
    outcome = validate_trade(Action.BUY, _levels(100.0, 90.0, 120.0), None)
    assert outcome.is_pass()
    assert abs(outcome.risk_reward - 2.0) < 1e-9


def test_risk_reward_below_two_fails():
    # risk = 10, reward = 19.9 → RR < 2.0 → fails.
    outcome = validate_trade(Action.BUY, _levels(100.0, 90.0, 119.9), None)
    assert outcome.reason == ValidatorReason.RISK_REWARD_TOO_LOW


# ── Stop-distance boundary (R6.3) ────────────────────────────────────────────

def test_stop_distance_above_atr_multiple_passes():
    # ATR = 10 → min stop distance = 15. risk = 20 (>=15) passes the ATR check;
    # reward = 60 → RR 3.0 → overall pass.
    assert validate_trade(Action.BUY, _levels(100.0, 80.0, 160.0), 10.0).is_pass()


def test_stop_distance_exactly_atr_multiple_passes():
    # Stop distance exactly 1.5*ATR = 15 → passes the ATR check.
    assert validate_trade(Action.BUY, _levels(100.0, 85.0, 145.0), 10.0).is_pass()


def test_stop_too_tight_fails():
    # Stop distance 14 < 15 → StopTooTight (R6.3).
    outcome = validate_trade(Action.BUY, _levels(100.0, 86.0, 200.0), 10.0)
    assert outcome.reason == ValidatorReason.STOP_TOO_TIGHT


def test_stop_too_tight_skipped_when_atr_unavailable():
    # No ATR → ATR check skipped; RR = 3.0 → pass.
    assert validate_trade(Action.BUY, _levels(100.0, 99.0, 103.0), None).is_pass()
    # Non-finite ATR → treated as unavailable, ATR check skipped.
    assert validate_trade(Action.BUY, _levels(100.0, 99.0, 103.0), math.nan).is_pass()


# ── Determinism ──────────────────────────────────────────────────────────────

def test_validator_is_deterministic():
    a = validate_trade(Action.SELL, _levels(250.0, 270.0, 200.0), 8.0)
    b = validate_trade(Action.SELL, _levels(250.0, 270.0, 200.0), 8.0)
    assert a == b


# ── Lenient action parsing mirror ────────────────────────────────────────────

def test_action_from_str_lenient():
    assert Action.from_str_lenient("buy") == Action.BUY
    assert Action.from_str_lenient("  SELL ") == Action.SELL
    assert Action.from_str_lenient("hold") == Action.HOLD
    assert Action.from_str_lenient("nonsense") == Action.HOLD
    assert Action.from_str_lenient("") == Action.HOLD
    assert Action.from_str_lenient(None) == Action.HOLD
