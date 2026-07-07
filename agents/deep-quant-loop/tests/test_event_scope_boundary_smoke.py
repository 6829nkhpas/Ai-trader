"""Scope-boundary smoke test for earnings-event-risk-gate (task 11.1).

Feature: earnings-event-risk-gate

This smoke test pins the scope boundary of the Event_Risk_Gate so the feature can
never silently grow beyond a *risk filter / context aid*:

  1. **R12.3 — the hard rules are unchanged.** A committed directional (BUY/SELL)
     trade still passes the UNCHANGED Trade_Validator hard rules (stop >= 1.5 x
     ATR, Risk:Reward >= 2.0) under ANY Event_Risk. ``validator.validate_trade``
     has no event input at all, so the event context has no structural way to
     relax, bypass, or override the hard rules — a sound bracket passes and an
     unsound one fails identically whatever the Event_Risk classification is.
  2. **R12.6 — date-only input.** The Event_Source input is limited to
     scheduled-event *dates*: the recognized field names are all date-bearing,
     the source reader extracts only date values (ignoring any transcript /
     report-content / sentiment fields that happen to sit alongside a date), and
     neither ``events.py`` nor the event source path in ``tools.py`` references
     transcript / report-content sentiment analysis.
  3. **R12.7 — no broker orders.** The event path places, modifies, or cancels no
     live broker orders: neither the pure classifier nor the tool / source-reader
     functions ever invoke the trade-committing / order tools
     (``declare_trade`` / ``watch_price_condition``) or issue any mutating
     (POST / PUT / PATCH / DELETE) request — the only I/O is a read-only calendar
     lookup.

Validates: Requirements 12.3, 12.6, 12.7.

These are lightweight, non-brittle source/shape assertions plus a direct exercise
of the pure validator (no live LLM / Rust / network). The sys.path / import
pattern mirrors the sibling ``test_options_scope_boundary_smoke.py`` and the
``test_*_validator_*`` modules. The existing ``tests/conftest.py`` is not
disturbed.
"""

import inspect
import io
import os
import sys
import tokenize

import pytest

# Make the service package importable (events.py / tools.py / validator.py live
# one level up). This mirrors the sibling scope-boundary / validator tests.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
import tools  # noqa: E402
from tools import EVENT_RISK_STATES  # noqa: E402
from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    MIN_RISK_REWARD,
    MIN_STOP_ATR_MULTIPLE,
    ValidatorReason,
    validate_trade,
)

_EVENTS_SRC_PATH = os.path.join(_SVC_DIR, "events.py")

# The event-path functions living in tools.py. Their source must never reach for
# a trade-committing / order-mutating API (R12.7) nor a transcript / sentiment
# source (R12.6). ``get_event_risk`` is @tool-decorated, so its underlying
# function is reached via ``.func``.
_EVENT_TOOL_PATH_FUNCS = (
    tools.get_event_risk.func,
    tools._load_event_candidates,
    tools._read_event_api,
    tools._read_event_file,
    tools._collect_symbol_dates,
    tools._collect_api_dates,
    tools._dates_to_ms,
    tools._parse_event_date_to_ms,
    tools._extract_dates_from_csv,
    tools._event_unavailable,
)


def _read_source(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _code_only(src):
    """Strip comments and string literals (docstrings included) from Python source,
    leaving only executable code tokens. This keeps the scope-boundary scans
    non-brittle: prose in docstrings/comments (which legitimately *describes* the
    scope boundary, e.g. "no transcript / sentiment") never trips a code check.
    """
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        # Fall back to the raw source if tokenization is interrupted (never for
        # the well-formed modules under test).
        return src
    return " ".join(out)


def _event_path_source():
    """Concatenated CODE (comments/strings stripped) of the pure classifier module
    plus every event-path function in tools.py — the complete executable surface
    of the gate."""
    chunks = [_code_only(_read_source(_EVENTS_SRC_PATH))]
    for fn in _EVENT_TOOL_PATH_FUNCS:
        chunks.append(_code_only(inspect.getsource(fn)))
    return "\n".join(chunks)


# ── R12.3: committed directional trade passes the UNCHANGED hard rules ────────
#
# A sound bracket (entry 100 / stop 90 / target 120) with a known ATR of 5:
#   risk    = |100 - 90|  = 10
#   reward  = |120 - 100| = 20   -> Risk:Reward = 2.0  (>= MIN_RISK_REWARD 2.0)
#   stop    = 10 >= 1.5 * 5 = 7.5                        (>= MIN_STOP_ATR_MULTIPLE)
_SOUND_ATR = 5.0
_SOUND_BUY = ExecutionLevels(entry=100.0, stop_loss=90.0, take_profit=120.0)
_SOUND_SELL = ExecutionLevels(entry=100.0, stop_loss=110.0, take_profit=80.0)


@pytest.mark.parametrize("event_risk", sorted(EVENT_RISK_STATES))
@pytest.mark.parametrize(
    "action, levels",
    [(Action.BUY, _SOUND_BUY), (Action.SELL, _SOUND_SELL)],
)
def test_sound_directional_trade_passes_hard_rules_under_any_event_risk(
    action, levels, event_risk
):
    """Validates: Requirements 12.3

    A committed directional trade that satisfies the hard rules
    (stop >= 1.5 x ATR AND Risk:Reward >= 2.0) passes ``validate_trade`` for every
    Event_Risk value. The event context (``event_risk``) is not even an input to
    the validator, so it cannot relax, bypass, or override the rules.
    """
    # The classifier can produce this event_risk, but it plays no part in the
    # validation — the hard rules stand alone.
    assert event_risk in EVENT_RISK_STATES

    outcome = validate_trade(action, levels, _SOUND_ATR)

    assert outcome.is_pass(), (
        f"a sound {action.value} bracket must pass the hard rules under "
        f"event_risk={event_risk!r}"
    )
    # Risk:Reward is reported and meets the unchanged 1:2 minimum.
    assert outcome.risk_reward is not None
    assert outcome.risk_reward >= MIN_RISK_REWARD


@pytest.mark.parametrize("event_risk", sorted(EVENT_RISK_STATES))
def test_stop_too_tight_still_rejected_under_any_event_risk(event_risk):
    """Validates: Requirements 12.3

    A stop tighter than 1.5 x ATR is rejected as STOP_TOO_TIGHT regardless of the
    Event_Risk — the event context cannot loosen the ATR floor.
    """
    # risk = 10, ATR = 10 -> 1.5 * 10 = 15 > 10 -> STOP_TOO_TIGHT.
    tight_atr = 10.0
    assert MIN_STOP_ATR_MULTIPLE * tight_atr > 10.0  # guards the fixture
    outcome = validate_trade(Action.BUY, _SOUND_BUY, tight_atr)

    assert not outcome.is_pass()
    assert outcome.reason is ValidatorReason.STOP_TOO_TIGHT, (
        f"a too-tight stop must stay rejected under event_risk={event_risk!r}"
    )


@pytest.mark.parametrize("event_risk", sorted(EVENT_RISK_STATES))
def test_low_risk_reward_still_rejected_under_any_event_risk(event_risk):
    """Validates: Requirements 12.3

    A Risk:Reward below the 1:2 minimum is rejected as RISK_REWARD_TOO_LOW
    regardless of the Event_Risk — the event context cannot loosen the R:R floor.
    """
    # entry 100 / stop 90 / target 110 -> reward 10 / risk 10 -> R:R 1.0 < 2.0.
    thin_levels = ExecutionLevels(entry=100.0, stop_loss=90.0, take_profit=110.0)
    outcome = validate_trade(Action.BUY, thin_levels, None)

    assert not outcome.is_pass()
    assert outcome.reason is ValidatorReason.RISK_REWARD_TOO_LOW, (
        f"a sub-1:2 R:R must stay rejected under event_risk={event_risk!r}"
    )


def test_validator_signature_has_no_event_input():
    """Validates: Requirements 12.3

    The Trade_Validator takes no event-risk / event-recommendation / holding-
    horizon argument, so the event gate has no channel to relax or override the
    hard rules — they are structurally independent of the event context.
    """
    params = set(inspect.signature(validate_trade).parameters)
    forbidden = {
        "event_risk",
        "event_recommendation",
        "days_until_event",
        "holding_horizon",
        "event",
        "event_config",
    }
    assert not (params & forbidden), (
        f"validate_trade must not accept any event input; found {params & forbidden}"
    )


# ── R12.6: input limited to scheduled-event DATES (no transcript / sentiment) ─
def test_event_source_field_names_are_date_only():
    """Validates: Requirements 12.6

    Every recognized Event_Source field name is a date-bearing key. No key names
    a transcript, an earnings-report body, or a sentiment/content field — the
    gate consumes only scheduled-event dates.
    """
    forbidden_substrings = ("transcript", "sentiment", "content", "body", "text", "summary")
    for key in tools._EVENT_DATE_KEYS:
        assert "date" in key.lower(), f"date key {key!r} must be date-bearing"
        for bad in forbidden_substrings:
            assert bad not in key.lower(), (
                f"event date key {key!r} must not reference {bad!r}"
            )


def test_source_reader_extracts_only_dates_ignoring_transcript_and_sentiment():
    """Validates: Requirements 12.6

    Given a record that carries a transcript / report-content / sentiment field
    ALONGSIDE the event date, the source reader extracts ONLY the date value and
    ignores the transcript / sentiment content entirely.
    """
    records = [
        {
            "symbol": "RELIANCE",
            "date": "2025-01-15",
            # Fields the gate must never consume:
            "transcript": "management is very bullish on the quarter ...",
            "sentiment": "positive",
            "report_content": "revenue up 20% YoY, EPS beat ...",
        }
    ]
    raw_dates = tools._collect_symbol_dates(records, "RELIANCE")

    # Only the scheduled-event date is collected; no transcript / sentiment text.
    assert raw_dates == ["2025-01-15"]
    for value in raw_dates:
        assert "bullish" not in value
        assert "positive" not in value
        assert "revenue" not in value


def test_event_path_never_references_transcript_or_sentiment():
    """Validates: Requirements 12.6

    Neither the pure classifier module nor the event-path functions in tools.py
    perform (or reach toward) earnings-call transcript or earnings-report content
    sentiment analysis — the source is scheduled-event dates only.
    """
    src = _event_path_source().lower()
    for banned in ("transcript", "sentiment", "report_content", "report content"):
        assert banned not in src, (
            f"the event path must not reference {banned!r} (date-proximity only, R12.6)"
        )


def test_events_module_is_pure_no_network_or_source_io():
    """Validates: Requirements 12.6

    The pure Event_Classifier module reads no external source: it imports no
    network client, so it can only ever consume the (reference, event, horizon,
    config) tuple passed to it — never a transcript / report feed.
    """
    src = _read_source(_EVENTS_SRC_PATH)
    for forbidden_import in ("import httpx", "import requests", "import urllib", "import socket"):
        assert forbidden_import not in src, f"events.py must not {forbidden_import!r}"


# ── R12.7: the event path places, modifies, or cancels no broker orders ───────
def test_event_path_never_commits_or_places_a_trade():
    """Validates: Requirements 12.7

    No function on the event path (the pure classifier or the tool / source
    reader) references the trade-committing / run-suspending tools, so the gate
    can never place, modify, or cancel a live broker order.
    """
    src = _event_path_source()
    assert "declare_trade" not in src
    assert "watch_price_condition" not in src


def test_event_path_issues_no_mutating_requests():
    """Validates: Requirements 12.7

    The event path performs only a READ-ONLY calendar lookup. It never issues a
    mutating HTTP request (POST / PUT / PATCH / DELETE) that could reach a broker
    order endpoint — the sole network call is ``httpx.get`` against the operator
    calendar API.
    """
    src = _event_path_source()
    for mutating in ("httpx.post", "httpx.put", "httpx.patch", "httpx.delete", ".request("):
        assert mutating not in src, (
            f"the event path must not issue a {mutating!r} request (R12.7)"
        )
    # The pure classifier is completely network-free.
    events_src = _read_source(_EVENTS_SRC_PATH)
    assert "httpx" not in events_src


def test_event_path_references_no_broker_order_apis():
    """Validates: Requirements 12.7

    The event path names no broker order-management API — it neither places,
    modifies, nor cancels orders.
    """
    src = _event_path_source().lower()
    for banned in ("place_order", "cancel_order", "modify_order", "broker", "order_book"):
        assert banned not in src, (
            f"the event path must not reference {banned!r} (R12.7 — no broker orders)"
        )
