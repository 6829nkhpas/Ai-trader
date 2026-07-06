"""Unit tests for task 4.12 — get_event_risk tool identity and argument wiring.

Feature: earnings-event-risk-gate

Validates: Requirements 4.1, 4.2, 4.4

R4.1 — the Event_Tool is exposed as an ``@tool``-decorated function named
       ``get_event_risk`` (a LangChain StructuredTool object exposing ``.name`` /
       ``.func`` / ``.invoke``).
R4.2 — the tool accepts a ``symbol`` and an optional Holding_Horizon and
       classifies the event risk for that symbol.
R4.4 — an absent / empty / unrecognized Holding_Horizon applies the documented
       default (``events.DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON``).

These are plain pytest example-based unit tests (no hypothesis). The Event_Source
reader (``tools._load_event_candidates``) and the process clock (``tools.time.time``)
are MOCKED with ``unittest.mock`` so a future candidate classifies deterministically
in-memory with NO network / filesystem I/O. The ``sys.path`` / import pattern and
the ``_raw`` @tool-unwrap helper mirror ``tests/test_event_tool_wellformed_properties.py``.
"""

import os
import sys
from unittest import mock

# Make the service package importable (tools.py / events.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
import tools  # noqa: E402
from tools import (  # noqa: E402
    EVENT_RISK_STATES,
    EVENT_RECOMMENDATIONS,
    get_event_risk,
)

# A non-empty symbol so argument validation passes.
_SYMBOL = "RELIANCE"

# A fixed reference "now" (epoch ms): 2024-01-15 12:00:00 UTC.
_NOW_MS = 1_705_320_000_000
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _fake_source(candidate_ms):
    """A source-reader stand-in: a configured source that read cleanly and
    yielded exactly one strictly-future candidate for the symbol."""

    def _load(symbol, config):
        return {
            "candidates": [candidate_ms],
            "source_configured": True,
            "retrieval_failed": False,
            "failure_reason": None,
        }

    return _load


# ─────────────────────────────────────────────────────────────────────────────
# R4.1 — tool identity: @tool-decorated, correctly named get_event_risk.
# ─────────────────────────────────────────────────────────────────────────────
def test_get_event_risk_is_tool_decorated_and_named():
    """Validates: Requirements 4.1

    ``get_event_risk`` exists in tools.py and is a LangChain ``@tool`` object
    (exposing ``.name``, ``.func``, and ``.invoke``) correctly named
    ``get_event_risk``.
    """
    # Present in the module.
    assert hasattr(tools, "get_event_risk")

    # LangChain StructuredTool identity: a callable tool object, not a bare fn.
    assert tools.get_event_risk.name == "get_event_risk"

    # The @tool wrapper exposes the underlying function and the invoke entry.
    assert hasattr(tools.get_event_risk, "func")
    assert callable(tools.get_event_risk.func)
    assert hasattr(tools.get_event_risk, "invoke")


# ─────────────────────────────────────────────────────────────────────────────
# R4.2 — passing symbol + horizon classifies for THAT symbol/horizon.
# ─────────────────────────────────────────────────────────────────────────────
def test_tool_classifies_for_given_symbol_and_horizon():
    """Validates: Requirements 4.2

    With the source and clock mocked to yield one upcoming event, passing a
    ``symbol`` and an explicit ``holding_horizon`` produces a usable
    Event_Assessment that reflects the given symbol and horizon.
    """
    # A strictly-future candidate 3 days out (inside the through-event window for
    # multi_session; well beyond same-session for intraday).
    candidate_ms = _NOW_MS + 3 * _DAY_MS + _HOUR_MS

    with mock.patch.object(tools, "_load_event_candidates",
                           side_effect=_fake_source(candidate_ms)), \
            mock.patch.object(tools.time, "time", return_value=_NOW_MS / 1000.0):
        result = _raw(get_event_risk)(symbol=_SYMBOL, holding_horizon="multi_session")

    assert isinstance(result, dict)
    assert result.get("unavailable") is not True
    assert "error" not in result

    # The assessment reflects the requested symbol and horizon verbatim.
    assert result.get("symbol") == _SYMBOL
    assert result.get("holding_horizon") == "multi_session"

    # A well-formed classification for that symbol/horizon.
    assert result.get("event_risk") in EVENT_RISK_STATES
    assert result.get("event_recommendation") in EVENT_RECOMMENDATIONS
    assert isinstance(result.get("days_until_event"), (int, float))
    assert result["days_until_event"] >= 0


def test_tool_respects_intraday_horizon():
    """Validates: Requirements 4.2

    A future-dated event under an explicit ``intraday`` horizon reflects that
    horizon in the assessment (a same-session trade does not straddle a
    future-dated event -> classified as clear).
    """
    candidate_ms = _NOW_MS + 3 * _DAY_MS + _HOUR_MS

    with mock.patch.object(tools, "_load_event_candidates",
                           side_effect=_fake_source(candidate_ms)), \
            mock.patch.object(tools.time, "time", return_value=_NOW_MS / 1000.0):
        result = _raw(get_event_risk)(symbol=_SYMBOL, holding_horizon="intraday")

    assert result.get("holding_horizon") == "intraday"
    # A later-dated event under intraday is clear (Requirement 2.4 mapping).
    assert result.get("event_risk") == "clear"


# ─────────────────────────────────────────────────────────────────────────────
# R4.4 — absent / empty / unrecognized horizon applies the documented default.
# ─────────────────────────────────────────────────────────────────────────────
def test_absent_horizon_applies_documented_default():
    """Validates: Requirements 4.4

    Calling the tool WITHOUT a ``holding_horizon`` argument applies the documented
    default (``events.DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON``), observable via the
    returned assessment's ``holding_horizon``.
    """
    candidate_ms = _NOW_MS + 3 * _DAY_MS + _HOUR_MS

    with mock.patch.object(tools, "_load_event_candidates",
                           side_effect=_fake_source(candidate_ms)), \
            mock.patch.object(tools.time, "time", return_value=_NOW_MS / 1000.0):
        result = _raw(get_event_risk)(symbol=_SYMBOL)  # holding_horizon defaults to ""

    assert result.get("unavailable") is not True
    assert result.get("holding_horizon") == events.DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON


def test_empty_and_unrecognized_horizons_apply_documented_default():
    """Validates: Requirements 4.4

    Empty, whitespace-only, and unrecognized ``holding_horizon`` values each
    normalize to the documented default in the returned assessment.
    """
    candidate_ms = _NOW_MS + 3 * _DAY_MS + _HOUR_MS
    default = events.DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON

    for horizon in ("", "   ", "swing", "n/a", "weekly"):
        with mock.patch.object(tools, "_load_event_candidates",
                               side_effect=_fake_source(candidate_ms)), \
                mock.patch.object(tools.time, "time", return_value=_NOW_MS / 1000.0):
            result = _raw(get_event_risk)(symbol=_SYMBOL, holding_horizon=horizon)

        assert result.get("holding_horizon") == default, (
            f"horizon {horizon!r} did not normalize to the documented default"
        )


# ─────────────────────────────────────────────────────────────────────────────
# R4.1 / R4.2 — the tool consults ONLY the configured Event_Source: the success
# path flows through _load_event_candidates and events.assess_event_risk.
# ─────────────────────────────────────────────────────────────────────────────
def test_success_path_flows_through_event_source_and_classifier_only():
    """Validates: Requirements 4.1, 4.2

    The tool's success path consults the configured Event_Source via
    ``_load_event_candidates`` and classifies via ``events.assess_event_risk`` —
    it does NOT consult any transcript / report-content data source. We assert
    both feature functions are invoked exactly on the success path (with the
    real classifier wrapped as a spy so its output is preserved).
    """
    candidate_ms = _NOW_MS + 3 * _DAY_MS + _HOUR_MS

    real_assess = events.assess_event_risk

    with mock.patch.object(tools, "_load_event_candidates",
                           side_effect=_fake_source(candidate_ms)) as m_load, \
            mock.patch.object(tools.events, "assess_event_risk",
                              side_effect=real_assess) as m_assess, \
            mock.patch.object(tools.time, "time", return_value=_NOW_MS / 1000.0):
        result = _raw(get_event_risk)(symbol=_SYMBOL, holding_horizon="multi_session")

    # The configured Event_Source reader was consulted exactly once for the symbol.
    assert m_load.call_count == 1
    load_args = m_load.call_args
    assert load_args.args[0] == _SYMBOL

    # Classification flowed through the pure Event_Classifier entry point.
    assert m_assess.call_count == 1

    # And the result is the classifier's usable assessment (no fabrication).
    assert result.get("event_risk") in EVENT_RISK_STATES
    assert result.get("symbol") == _SYMBOL
