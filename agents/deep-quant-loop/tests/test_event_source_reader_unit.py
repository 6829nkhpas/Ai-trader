"""Unit tests for task 4.11 — the pluggable Event_Source reader.

Feature: earnings-event-risk-gate

Validates: Requirements 1.1, 1.2, 1.3, 1.4

R1.1 — the Event_Source obtains scheduled-event dates ONLY from an operator-
       configured provider (a local calendar file and/or a calendar API URL) and
       does NOT hardcode or scrape a specific paid vendor.
R1.2 — with NO source configured the gate returns an Unavailable_Marker whose
       reason identifies that no event source is configured (no exception).
R1.3 — a configured source with no scheduled event for the symbol yields no
       candidates (the tool returns a no-upcoming-event Unavailable_Marker) and
       fabricates no date.
R1.4 — an unreachable / timed-out / non-2xx / malformed source yields no
       candidates (retrieval-cause Unavailable_Marker) without raising.

These are plain pytest unit tests (no hypothesis). The only I/O the reader
performs is the local file read and the calendar-API ``tools.httpx.get`` call.
Temp calendar files use the ``tmp_path`` fixture; the API call is mocked with
``unittest.mock`` so the tests run in-memory with no network. Env changes go
through ``monkeypatch`` (auto-restored) so ``os.environ`` is left untouched.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest import mock

# Make the service package importable (tools.py / events.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
import tools  # noqa: E402
from events import EventConfig  # noqa: E402

_SYMBOL = "RELIANCE"
_IST = ZoneInfo("Asia/Kolkata")


# ── helpers ──────────────────────────────────────────────────────────────────
def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _config(*, calendar_file_path=None, calendar_api_url=None,
            timezone="Asia/Kolkata", enabled=True):
    """Build an EventConfig directly with documented-default numeric params.

    Only the source fields (and timezone / enabled) vary across these tests, so
    the numeric windows are pinned to the documented defaults.
    """
    return EventConfig(
        enabled=enabled,
        timezone=timezone,
        default_holding_horizon="multi_session",
        imminent_window_days=5,
        through_event_window_days=2,
        source_timeout_s=10.0,
        calendar_api_url=calendar_api_url,
        calendar_file_path=calendar_file_path,
    )


def _date_str(days_ahead):
    """A future ``YYYY-MM-DD`` date string ``days_ahead`` days from today (IST)."""
    d = (datetime.now(tz=_IST) + timedelta(days=days_ahead)).date()
    return d.isoformat()


def _expected_ms(date_str, tz="Asia/Kolkata"):
    """Epoch-ms a date-only string anchors to (midnight in ``tz``)."""
    y, m, d = int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])
    return datetime(y, m, d, 0, 0, 0, tzinfo=ZoneInfo(tz)).timestamp() * 1000.0


def _mock_response(json_data, status_code=200, malformed=False):
    """Build a stand-in for an httpx.Response carrying ``json_data``."""
    resp = mock.Mock()
    resp.status_code = status_code
    if malformed:
        resp.json = mock.Mock(side_effect=ValueError("not JSON"))
        resp.text = "<<not json>>"
    else:
        resp.json = mock.Mock(return_value=json_data)
        resp.text = json.dumps(json_data)
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# R1.1 / R1.3 — local JSON calendar file: parse upcoming dates for the symbol.
# ─────────────────────────────────────────────────────────────────────────────
def test_json_file_parses_upcoming_dates_for_symbol(tmp_path):
    """Validates: Requirements 1.1, 1.3

    A temp JSON calendar file mapping symbol -> upcoming date(s) is parsed into
    epoch-ms candidates for the requested symbol.
    """
    d1, d2 = _date_str(3), _date_str(9)
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({_SYMBOL: [d1, d2], "TCS": [_date_str(4)]}),
                    encoding="utf-8")

    config = _config(calendar_file_path=str(path))
    result = tools._load_event_candidates(_SYMBOL, config)

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is False
    assert sorted(result["candidates"]) == sorted(
        [_expected_ms(d1), _expected_ms(d2)]
    )


def test_json_file_symbol_match_is_case_insensitive(tmp_path):
    """Validates: Requirements 1.1

    The symbol match is case-insensitive (operator files may differ in case).
    """
    d1 = _date_str(5)
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"reliance": d1}), encoding="utf-8")

    result = tools._load_event_candidates(_SYMBOL, _config(calendar_file_path=str(path)))
    assert result["candidates"] == [_expected_ms(d1)]


# ─────────────────────────────────────────────────────────────────────────────
# R1.1 / R1.3 — local CSV calendar file: parse upcoming dates for the symbol.
# ─────────────────────────────────────────────────────────────────────────────
def test_csv_file_parses_upcoming_dates_for_symbol(tmp_path):
    """Validates: Requirements 1.1, 1.3

    A temp CSV calendar file (symbol,date header) is parsed into epoch-ms
    candidates for the requested symbol.
    """
    d1, d2 = _date_str(2), _date_str(7)
    path = tmp_path / "calendar.csv"
    path.write_text(
        f"symbol,date\n{_SYMBOL},{d1}\nTCS,{_date_str(4)}\n{_SYMBOL},{d2}\n",
        encoding="utf-8",
    )

    result = tools._load_event_candidates(_SYMBOL, _config(calendar_file_path=str(path)))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is False
    assert sorted(result["candidates"]) == sorted(
        [_expected_ms(d1), _expected_ms(d2)]
    )


# ─────────────────────────────────────────────────────────────────────────────
# R1.3 — symbol absent from the file -> no candidates (no fabrication).
# ─────────────────────────────────────────────────────────────────────────────
def test_file_symbol_absent_yields_no_candidates(tmp_path):
    """Validates: Requirements 1.3

    A file that reads cleanly but has no entry for the symbol yields NO
    candidates and NO retrieval failure — the tool will surface a
    no-upcoming-event marker; no date is fabricated.
    """
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"TCS": _date_str(3)}), encoding="utf-8")

    result = tools._load_event_candidates(_SYMBOL, _config(calendar_file_path=str(path)))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is False
    assert result["candidates"] == []


def test_missing_file_degrades_to_retrieval_failure(tmp_path):
    """Validates: Requirements 1.4

    A configured-but-missing calendar file degrades to a retrieval failure with
    no candidates and does not raise.
    """
    missing = tmp_path / "does_not_exist.json"
    result = tools._load_event_candidates(_SYMBOL, _config(calendar_file_path=str(missing)))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is True
    assert result["candidates"] == []
    assert result["failure_reason"]


def test_malformed_json_file_degrades_to_retrieval_failure(tmp_path):
    """Validates: Requirements 1.4

    A malformed (unparseable) JSON calendar file degrades to a retrieval failure
    without raising.
    """
    path = tmp_path / "calendar.json"
    path.write_text("{ this is not valid json ", encoding="utf-8")

    result = tools._load_event_candidates(_SYMBOL, _config(calendar_file_path=str(path)))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is True
    assert result["candidates"] == []


# ─────────────────────────────────────────────────────────────────────────────
# R1.1 — calendar API: a success parses candidates from the CONFIGURED URL only.
# ─────────────────────────────────────────────────────────────────────────────
def test_api_success_parses_candidates_from_configured_url():
    """Validates: Requirements 1.1

    A 2xx calendar-API response is parsed into candidates, and the request is
    issued against the OPERATOR-CONFIGURED URL only (no hardcoded vendor).
    """
    api_url = "https://operator.example.internal/calendar"
    d1, d2 = _date_str(3), _date_str(8)
    body = {"dates": [d1, d2]}

    with mock.patch.object(tools.httpx, "get",
                           return_value=_mock_response(body)) as mget:
        result = tools._load_event_candidates(_SYMBOL, _config(calendar_api_url=api_url))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is False
    assert sorted(result["candidates"]) == sorted([_expected_ms(d1), _expected_ms(d2)])

    # The reader consulted ONLY the operator-configured URL.
    assert mget.call_count == 1
    called_url = mget.call_args.args[0] if mget.call_args.args else mget.call_args.kwargs.get("url")
    assert called_url == api_url


def test_api_timeout_yields_no_candidates_without_raising():
    """Validates: Requirements 1.4

    A timed-out / unreachable calendar API degrades to a retrieval failure with
    no candidates and does not raise.
    """
    api_url = "https://operator.example.internal/calendar"

    with mock.patch.object(tools.httpx, "get",
                           side_effect=TimeoutError("timed out")):
        result = tools._load_event_candidates(_SYMBOL, _config(calendar_api_url=api_url))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is True
    assert result["candidates"] == []


def test_api_non_2xx_yields_no_candidates():
    """Validates: Requirements 1.4

    A non-2xx calendar-API status degrades to a retrieval failure with no
    candidates.
    """
    api_url = "https://operator.example.internal/calendar"

    with mock.patch.object(tools.httpx, "get",
                           return_value=_mock_response(None, status_code=503)):
        result = tools._load_event_candidates(_SYMBOL, _config(calendar_api_url=api_url))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is True
    assert result["candidates"] == []


def test_api_malformed_body_yields_no_candidates():
    """Validates: Requirements 1.4

    A 2xx calendar-API response with an unparseable body degrades to a retrieval
    failure with no candidates.
    """
    api_url = "https://operator.example.internal/calendar"

    with mock.patch.object(tools.httpx, "get",
                           return_value=_mock_response(None, malformed=True)):
        result = tools._load_event_candidates(_SYMBOL, _config(calendar_api_url=api_url))

    assert result["source_configured"] is True
    assert result["retrieval_failed"] is True
    assert result["candidates"] == []


# ─────────────────────────────────────────────────────────────────────────────
# R1.2 — NEITHER source configured -> "no event source configured" marker.
# ─────────────────────────────────────────────────────────────────────────────
def test_neither_configured_signals_no_source():
    """Validates: Requirements 1.2

    With neither a file nor an API configured, the loader signals that no source
    is configured (and never touches the network).
    """
    with mock.patch.object(tools.httpx, "get",
                           side_effect=AssertionError("no retrieval when unconfigured")):
        result = tools._load_event_candidates(_SYMBOL, _config())

    assert result["source_configured"] is False
    assert result["retrieval_failed"] is False
    assert result["candidates"] == []


def test_tool_returns_no_source_marker_when_unconfigured(monkeypatch):
    """Validates: Requirements 1.2

    End-to-end via the tool: with the two source env vars unset (and the gate
    enabled), get_event_risk returns an Unavailable_Marker whose reason
    identifies that no event source is configured — without raising, and without
    an event_risk / event_recommendation label.
    """
    monkeypatch.delenv("EVENT_CALENDAR_FILE", raising=False)
    monkeypatch.delenv("EVENT_CALENDAR_API_URL", raising=False)
    monkeypatch.delenv("EVENT_GATE_ENABLED", raising=False)  # default enabled

    # Guard: no network is touched on the unconfigured path.
    with mock.patch.object(tools.httpx, "get",
                           side_effect=AssertionError("no retrieval when unconfigured")):
        result = _raw(tools.get_event_risk)(symbol=_SYMBOL, holding_horizon="multi_session")

    assert result.get("unavailable") is True
    assert "no event source configured" in result.get("reason", "")
    assert "event_risk" not in result
    assert "event_recommendation" not in result


def test_tool_returns_no_upcoming_event_when_symbol_absent(tmp_path, monkeypatch):
    """Validates: Requirements 1.3

    End-to-end via the tool: a configured file that reads cleanly but has no
    entry for the symbol yields a no-upcoming-event Unavailable_Marker (no
    fabricated date).
    """
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"TCS": _date_str(3)}), encoding="utf-8")

    monkeypatch.delenv("EVENT_CALENDAR_API_URL", raising=False)
    monkeypatch.delenv("EVENT_GATE_ENABLED", raising=False)
    monkeypatch.setenv("EVENT_CALENDAR_FILE", str(path))

    result = _raw(tools.get_event_risk)(symbol=_SYMBOL, holding_horizon="multi_session")

    assert result.get("unavailable") is True
    assert "no upcoming" in result.get("reason", "").lower()
    assert "event_risk" not in result


def test_tool_classifies_when_file_has_future_event(tmp_path, monkeypatch):
    """Validates: Requirements 1.1, 1.3

    End-to-end via the tool: a configured file with an upcoming event for the
    symbol produces a usable Event_Assessment (no fabrication, real parse).
    """
    d1 = _date_str(3)  # inside the through-event window for multi_session
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({_SYMBOL: d1}), encoding="utf-8")

    monkeypatch.delenv("EVENT_CALENDAR_API_URL", raising=False)
    monkeypatch.delenv("EVENT_GATE_ENABLED", raising=False)
    monkeypatch.setenv("EVENT_CALENDAR_FILE", str(path))

    result = _raw(tools.get_event_risk)(symbol=_SYMBOL, holding_horizon="multi_session")

    assert result.get("unavailable") is not True
    assert result.get("event_risk") in tools.EVENT_RISK_STATES
    assert result.get("event_recommendation") in tools.EVENT_RECOMMENDATIONS
    assert result.get("event_date") == d1
    assert isinstance(result.get("days_until_event"), (int, float))
    assert result["days_until_event"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# R1.1 — no hardcoded/scraped paid vendor in the event-source code path.
# ─────────────────────────────────────────────────────────────────────────────
def test_event_source_code_hardcodes_no_paid_vendor():
    """Validates: Requirements 1.1

    Robust structural guard: the event-source reader consults an operator-
    configured URL (proven above) and the tools.py source references no known
    paid-vendor calendar domain for this feature.
    """
    src_path = os.path.join(_SVC_DIR, "tools.py")
    with open(src_path, "r", encoding="utf-8") as handle:
        source = handle.read().lower()

    known_vendor_domains = [
        "alphavantage",
        "finnhub",
        "polygon.io",
        "iexcloud",
        "financialmodelingprep",
        "tradingeconomics",
        "zacks",
        "earningswhispers",
    ]
    for domain in known_vendor_domains:
        assert domain not in source, f"tools.py hardcodes a paid vendor: {domain!r}"
