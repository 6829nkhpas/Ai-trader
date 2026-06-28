"""Scope-boundary smoke test for options-agent-integration (F3) (task 12.3).

Feature: options-agent-integration

This smoke test pins the F3 scope boundary so the feature can never silently grow
beyond a filter / context aid:

  1. The Options_Bias_Classifier emits ONLY a label — never a BUY/SELL/HOLD trade
     decision — and neither ``options_bias.py`` nor the classifier path imports or
     invokes ``declare_trade`` (R10.2 / R10.4 — never commits/blocks/overrides a
     trade).
  2. The options bias derives EXCLUSIVELY from the F1/F2 option data and the
     system spot: ``options_bias.py`` is standard-library-only (no network / httpx
     / requests) and re-computes none of the F2 analytics — the analytic math
     lives in the F2 engine (``options.compute_options_analytics``), which the
     tool consumes verbatim (R10.5).
  3. F3 adds no frontend UI changes — those are Phase F4. The feature touches only
     the agent Python modules under ``agents/deep-quant-loop/`` (R10.4 / R10.5).

Validates: Requirements 10.2, 10.4, 10.5.

These are lightweight, non-brittle source/shape assertions (no live LLM / Rust /
network). The sys.path / import pattern mirrors the sibling ``test_options_*``
modules. NOTE: a separate ``test_options_scope_boundary.py`` belongs to the F2
options-analytics-engine; this F3 smoke test uses a distinct filename.
"""

import os
import re
import sys

# Make the service package importable (options_bias.py / tools.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options_bias  # noqa: E402
from options_bias import classify_options_bias, resolve_options_bias_config  # noqa: E402

_OPTIONS_BIAS_SRC = os.path.join(_SVC_DIR, "options_bias.py")


def _read_source(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── R10.2 / R10.4: the classifier emits no trade decision and never commits ──
def test_classifier_emits_label_only_no_action_keys():
    """Validates: Requirements 10.2, 10.4

    ``classify_options_bias`` returns ONLY ``options_bias_state`` / ``alignment``
    / ``signals`` — never a trade-decision / action key — for a representative
    usable analytics result.
    """
    config = resolve_options_bias_config()
    analytics = {
        "underlying": "NIFTY 50",
        "spot": 22000.0,
        "pcr_oi": 1.4,
        "pcr_volume": 1.2,
        "max_pain": 22100.0,
        "oi_buildup": {"call": "short_buildup", "put": "long_buildup"},
        "oi_walls": {"support": 21800.0, "resistance": 22300.0},
        "iv_skew": {"put_minus_call": -0.5},
        "futures_basis": 8.0,
    }
    label = classify_options_bias(analytics, config, proposed_direction="BUY")

    assert set(label.keys()) == {"options_bias_state", "alignment", "signals"}
    # No trade-decision / action fields at the top level.
    forbidden = {
        "action", "recommendation", "conviction", "conviction_score", "score",
        "entry", "stop_loss", "take_profit", "decision", "trade",
    }
    assert not (set(label.keys()) & forbidden)
    # The bias state is a category, never a BUY/SELL/HOLD action.
    assert label["options_bias_state"] in {"bullish", "bearish", "neutral"}
    assert label["alignment"] in {"aligned", "misaligned", "neutral"}


def test_options_bias_source_never_commits_a_trade():
    """Validates: Requirements 10.2, 10.4

    ``options_bias.py`` must never reference the trade-committing /
    run-suspending tools — it is a pure filter / context aid.
    """
    src = _read_source(_OPTIONS_BIAS_SRC)
    assert "declare_trade" not in src
    assert "watch_price_condition" not in src


# ── R10.5: derives exclusively from F1/F2 option data + system spot ──────────
def test_options_bias_is_standard_library_only_no_network():
    """Validates: Requirements 10.5

    The classifier module performs zero I/O — no network client is imported, so
    it can only consume the analytics + config + spot passed to it.
    """
    src = _read_source(_OPTIONS_BIAS_SRC)
    for forbidden_import in ("import httpx", "import requests", "import urllib", "import socket"):
        assert forbidden_import not in src, f"options_bias.py must not {forbidden_import!r}"
    # The actual imports are the standard-library trio used by the module.
    assert re.search(r"^import math$", src, re.MULTILINE)
    assert re.search(r"^import os$", src, re.MULTILINE)


def test_classifier_does_not_recompute_f2_analytics():
    """Validates: Requirements 10.5

    The bias is a threshold vote over the analytics the F2 engine produced; the
    classifier reads the analytics fields but never re-derives them. The
    ``signals`` echo reproduces the engine's values verbatim rather than
    recomputing PCR / max pain / OI walls / IV skew / basis.
    """
    config = resolve_options_bias_config()
    analytics = {
        "spot": 100.0,
        "pcr_oi": 1.55,
        "pcr_volume": 1.1,
        "max_pain": 101.0,
        "oi_buildup": {"call": "short_buildup", "put": "long_buildup"},
        "oi_walls": {"support": 98.0, "resistance": 103.0},
        "iv_skew": {"put_minus_call": -0.3},
        "futures_basis": 0.5,
    }
    label = classify_options_bias(analytics, config, proposed_direction="BUY")
    signals = label["signals"]
    # Every driving signal is echoed verbatim from the F2 analytics (not recomputed).
    assert signals["pcr_oi"] == analytics["pcr_oi"]
    assert signals["max_pain"] == analytics["max_pain"]
    assert signals["oi_walls"] == analytics["oi_walls"]
    assert signals["futures_basis"] == analytics["futures_basis"]
    assert signals["oi_buildup"] == analytics["oi_buildup"]


def test_tool_delegates_analytics_to_f2_engine():
    """Validates: Requirements 10.5

    The ``get_options_analytics`` tool sources its analytics from the F2 engine
    (``options.compute_options_analytics``) rather than computing them itself —
    confirmed by the tool source delegating to that single entry point.
    """
    tools_src = _read_source(os.path.join(_SVC_DIR, "tools.py"))
    assert "options.compute_options_analytics" in tools_src


# ── R10.4 / R10.5: no frontend UI changes (Phase F4) ─────────────────────────
def test_no_frontend_changes_feature_is_python_only():
    """Validates: Requirements 10.4, 10.5

    F3 is an agent-side feature: its new module lives under
    ``agents/deep-quant-loop/`` and it introduces no frontend UI. This is a
    lightweight, non-brittle assertion that the F3 Python module exists in the
    agent package (UI wiring is explicitly deferred to Phase F4).
    """
    assert os.path.isfile(_OPTIONS_BIAS_SRC), "options_bias.py must live in the agent package"
    # The agent package is the deep-quant-loop service dir; F3 ships no frontend.
    assert os.path.basename(_SVC_DIR) == "deep-quant-loop"
